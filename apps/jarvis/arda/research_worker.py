"""M2.3A deterministic research worker over the existing worker protocol."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from arda.worker_protocol import WorkerProtocolClient


class ResearchContractError(ValueError):
    """The research job contract is invalid or cannot produce a safe result."""


class ResearchSourceError(RuntimeError):
    """A source could not be acquired or parsed deterministically."""


class ResearchBudgetError(TimeoutError):
    """The research task exceeded its declared time budget."""


@dataclass(frozen=True)
class ResearchTask:
    questions: tuple[str, ...]
    source_refs: tuple[str, ...]
    source_constraints: dict[str, Any]
    max_sources: int
    time_budget_seconds: float
    required_output_format: tuple[str, ...]
    expected_claims: tuple[dict[str, str], ...]

    @classmethod
    def from_objective(cls, objective: str) -> "ResearchTask":
        try:
            raw = json.loads(objective)
        except (TypeError, ValueError) as exc:
            raise ResearchContractError("research objective must be JSON") from exc
        if not isinstance(raw, dict):
            raise ResearchContractError("research objective must be an object")

        def strings(name: str, *, required: bool = True) -> tuple[str, ...]:
            value = raw.get(name)
            if not isinstance(value, list) or (required and not value):
                raise ResearchContractError(f"{name} must be a non-empty list")
            if not all(isinstance(item, str) and item.strip() for item in value):
                raise ResearchContractError(f"{name} must contain non-empty strings")
            return tuple(item.strip() for item in value)

        questions = strings("research_questions")
        source_refs = strings("source_refs")
        constraints = raw.get("source_constraints", {})
        if not isinstance(constraints, dict):
            raise ResearchContractError("source_constraints must be an object")
        allowed_types = constraints.get("allowed_source_types")
        if allowed_types is not None and (
            not isinstance(allowed_types, list)
            or not all(isinstance(item, str) and item.strip() for item in allowed_types)
        ):
            raise ResearchContractError("allowed_source_types must be a list of strings")
        try:
            max_sources = int(raw["max_sources"])
            budget = float(raw["time_budget_seconds"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ResearchContractError("max_sources and time_budget_seconds are required") from exc
        if max_sources < 1 or max_sources > len(source_refs):
            raise ResearchContractError("max_sources must be within source_refs")
        if budget <= 0:
            raise ResearchContractError("time_budget_seconds must be positive")
        formats = strings("required_output_format")
        if set(formats) != {"research.json", "research.md"}:
            raise ResearchContractError("required_output_format must contain research.json and research.md")
        raw_claims = raw.get("expected_claims", [])
        if not isinstance(raw_claims, list):
            raise ResearchContractError("expected_claims must be a list")
        claims: list[dict[str, str]] = []
        for item in raw_claims:
            if not isinstance(item, dict) or not isinstance(item.get("claim_id"), str) or not isinstance(item.get("text"), str):
                raise ResearchContractError("each expected claim needs claim_id and text")
            if not item["claim_id"].strip() or not item["text"].strip():
                raise ResearchContractError("claim_id and claim text must be non-empty")
            claims.append({"claim_id": item["claim_id"].strip(), "text": item["text"].strip()})
        if len({item["claim_id"] for item in claims}) != len(claims):
            raise ResearchContractError("claim_id values must be unique")
        return cls(questions, source_refs, dict(constraints), max_sources, budget, formats, tuple(claims))

    def plan(self) -> dict[str, Any]:
        return {
            "research_questions": list(self.questions),
            "source_refs": list(self.source_refs),
            "source_constraints": self.source_constraints,
            "max_sources": self.max_sources,
            "time_budget_seconds": self.time_budget_seconds,
            "required_output_format": list(self.required_output_format),
        }


@dataclass(frozen=True)
class FixtureSource:
    source_id: str
    source_type: str
    uri: str
    retrieved_at: str
    title: str
    content: str
    provenance: dict[str, Any]

    def metadata(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "uri": self.uri,
            "retrieved_at": self.retrieved_at,
            "title": self.title,
            "provenance": self.provenance,
        }


class FixtureSourceAdapter:
    """Offline source adapter with deterministic fixture data and no network access."""

    def __init__(self, sources: Mapping[str, FixtureSource | Mapping[str, Any]]):
        self._sources = dict(sources)

    def fetch(self, source_ref: str) -> FixtureSource:
        raw = self._sources.get(source_ref)
        if raw is None:
            raise ResearchSourceError(f"source unavailable: {source_ref}")
        if isinstance(raw, FixtureSource):
            source = raw
        else:
            try:
                source = FixtureSource(
                    source_id=str(raw["source_id"]), source_type=str(raw["source_type"]),
                    uri=str(raw["uri"]), retrieved_at=str(raw["retrieved_at"]),
                    title=str(raw.get("title", raw["uri"])), content=str(raw["content"]),
                    provenance=dict(raw.get("provenance", {})),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ResearchSourceError(f"malformed source: {source_ref}") from exc
        if not source.source_id or not source.uri or not source.retrieved_at or not source.content.strip():
            raise ResearchSourceError(f"malformed source: {source_ref}")
        if len(source.content.encode("utf-8")) > 128 * 1024:
            raise ResearchSourceError(f"source exceeds limit: {source_ref}")
        return source


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _evidence_id(source_id: str, index: int, excerpt: str) -> str:
    digest = hashlib.sha256(f"{source_id}:{index}:{excerpt}".encode("utf-8")).hexdigest()[:16]
    return f"evidence_{digest}"


def build_research(task: ResearchTask, adapter: FixtureSourceAdapter, *, clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    started = clock()
    sources: list[FixtureSource] = []
    evidence: list[dict[str, Any]] = []
    for source_ref in task.source_refs[:task.max_sources]:
        if clock() - started > task.time_budget_seconds:
            raise ResearchBudgetError("research time budget exhausted")
        source = adapter.fetch(source_ref)
        allowed = task.source_constraints.get("allowed_source_types")
        if allowed is not None and source.source_type not in allowed:
            raise ResearchSourceError(f"source type rejected: {source_ref}")
        sources.append(source)
        sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", source.content) if part.strip()]
        if not sentences:
            raise ResearchSourceError(f"source has no extractable evidence: {source_ref}")
        for index, excerpt in enumerate(sentences):
            evidence.append({
                "evidence_id": _evidence_id(source.source_id, index, excerpt),
                "source_id": source.source_id,
                "excerpt": excerpt,
                "provenance": {
                    "source_ref": source_ref,
                    "retrieved_at": source.retrieved_at,
                    "locator": index,
                    "source_provenance": source.provenance,
                },
            })

    claims: list[dict[str, Any]] = []
    for expected in task.expected_claims:
        claim_text = expected["text"]
        matching = [item["evidence_id"] for item in evidence if _norm(claim_text) in _norm(item["excerpt"])]
        claims.append({
            "claim_id": expected["claim_id"],
            "text": claim_text,
            "support_status": "supported" if matching else "not_found",
            "evidence_ids": matching,
        })
    result = {
        "schema_version": "m2.3a",
        "plan": task.plan(),
        "sources": [source.metadata() for source in sources],
        "evidence": evidence,
        "claims": claims,
    }
    validate_research_result(result)
    return result


def validate_research_result(result: dict[str, Any]) -> None:
    if not isinstance(result.get("claims"), list):
        raise ResearchContractError("claims must be a list")
    for claim in result["claims"]:
        if claim.get("support_status") == "supported" and not claim.get("evidence_ids"):
            raise ResearchContractError(f"supported claim has no evidence: {claim.get('claim_id')}")
        if claim.get("support_status") not in {"supported", "not_found", "unsupported"}:
            raise ResearchContractError("invalid claim support status")


def canonical_json(result: dict[str, Any]) -> bytes:
    validate_research_result(result)
    return json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def render_markdown(result: dict[str, Any]) -> bytes:
    lines = ["# Research report", "", "## Questions"]
    lines.extend(f"- {question}" for question in result["plan"]["research_questions"])
    lines.extend(["", "## Sources"])
    for source in result["sources"]:
        lines.append(f"- `{source['source_id']}` — {source['title']} ({source['uri']})")
    lines.extend(["", "## Claims"])
    for claim in result["claims"]:
        if claim["support_status"] == "supported":
            refs = ", ".join(f"`{item}`" for item in claim["evidence_ids"])
            lines.append(f"- **Supported:** {claim['text']} ({refs})")
        else:
            lines.append(f"- **Not found in acquired sources:** {claim['text']}")
    lines.extend(["", "## Evidence"])
    for item in result["evidence"]:
        lines.append(f"- `{item['evidence_id']}` ({item['source_id']}): {item['excerpt']}")
    return ("\n".join(lines) + "\n").encode("utf-8")


class DeterministicResearchWorker:
    """Claim and publish one research job through Core-owned protocol operations."""

    def __init__(self, client: WorkerProtocolClient, adapter: FixtureSourceAdapter, *, max_output_bytes: int = 512 * 1024):
        self.client = client
        self.adapter = adapter
        self.max_output_bytes = max_output_bytes

    def run_once(self, *, run_id: str) -> dict[str, Any]:
        claim = self.client.request("claim", {"job_type": "research"}, request_id=f"{run_id}:claim")
        lease = {"job_id": claim["job_id"], "lease_token": claim["lease_token"]}
        try:
            self.client.request("start", lease, request_id=f"{run_id}:start")
            job = self.client.request("describe", lease, request_id=f"{run_id}:describe")
            if job["type"] != "research":
                raise ResearchContractError("claimed job is not research")
            task = ResearchTask.from_objective(job["objective"])
            result = build_research(task, self.adapter)
            json_bytes = canonical_json(result)
            markdown_bytes = render_markdown(result)
            if len(json_bytes) + len(markdown_bytes) > self.max_output_bytes:
                raise ResearchBudgetError("research output exceeds limit")
            for kind, path, content in (
                ("research.json", "research.json", json_bytes),
                ("research.md", "research.md", markdown_bytes),
            ):
                self.client.request(
                    "upload_artifact",
                    {**lease, "kind": kind, "path_or_ref": path,
                     "content_b64": base64.b64encode(content).decode("ascii"),
                     "sha256": hashlib.sha256(content).hexdigest()},
                    request_id=f"{run_id}:upload:{kind}",
                )
            self.client.request("complete", {**lease, "result": {"schema_version": "m2.3a"}}, request_id=f"{run_id}:complete")
            return result
        except Exception as exc:
            try:
                self.client.request(
                    "fail", {**lease, "error": exc.__class__.__name__, "retryable": isinstance(exc, (ResearchSourceError, ResearchBudgetError))},
                    request_id=f"{run_id}:fail",
                )
            except Exception:
                pass
            raise
