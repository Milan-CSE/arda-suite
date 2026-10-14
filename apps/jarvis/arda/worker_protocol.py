"""M2.1 signed worker protocol and in-memory transport simulation."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from arda.core import Database, JobQueue, _hash_payload, _iso, _utc_now
from arda.worker_runtime import WorkerAuthError, WorkerRegistry


class ProtocolAuthenticationError(PermissionError):
    """Request identity, token, or signature is invalid."""


class ProtocolReplayConflictError(RuntimeError):
    """A request id was reused with a different payload."""


class ProtocolReplayInFlightError(RuntimeError):
    """The same request is currently being processed by another controller turn."""


class ProtocolUnavailableError(ConnectionError):
    """The transport is unavailable; the request was not delivered."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Parse JSON objects without allowing ambiguous duplicate keys."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _canonical(request_id: str, worker_id: str, message_type: str, payload: Any) -> bytes:
    return json.dumps(
        {"request_id": request_id, "worker_id": worker_id, "type": message_type, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True)
class ProtocolRequest:
    request_id: str
    worker_id: str
    message_type: str
    payload: dict[str, Any]
    auth_token: str
    signature: str

    @classmethod
    def create(
        cls,
        worker_id: str,
        auth_token: str,
        message_type: str,
        payload: dict[str, Any] | None = None,
        *,
        request_id: str | None = None,
    ) -> "ProtocolRequest":
        request_id = request_id or f"req_{uuid.uuid4().hex}"
        payload = payload or {}
        signature = hmac.new(
            auth_token.encode("utf-8"),
            _canonical(request_id, worker_id, message_type, payload),
            hashlib.sha256,
        ).hexdigest()
        return cls(request_id, worker_id, message_type, payload, auth_token, signature)


class WorkerProtocolServer:
    """Controller-side protocol handler with durable request replay protection."""

    def __init__(self, db: Database, *, clock=_utc_now):
        self.db = db
        self.clock = clock
        self.registry = WorkerRegistry(db, clock=clock)
        self.queue = JobQueue(db, clock=clock)

    def handle(self, request: ProtocolRequest) -> dict[str, Any]:
        try:
            self.registry.authenticate(request.worker_id, request.auth_token)
        except WorkerAuthError as exc:
            raise ProtocolAuthenticationError(str(exc)) from exc
        expected = hmac.new(
            request.auth_token.encode("utf-8"),
            _canonical(request.request_id, request.worker_id, request.message_type, request.payload),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, request.signature):
            raise ProtocolAuthenticationError("request signature mismatch")

        key = f"worker-protocol:{request.worker_id}:{request.request_id}"
        payload_hash = _hash_payload({"type": request.message_type, "payload": request.payload})
        with self.db.transaction():
            try:
                self.db.execute(
                    "INSERT INTO idempotency_keys(key, operation, result, created_at) VALUES (?, ?, NULL, ?)",
                    (key, payload_hash, _iso(self.clock())),
                )
            except Exception as exc:
                if "UNIQUE" not in str(exc).upper():
                    raise
                existing = self.db.execute(
                    "SELECT operation, result FROM idempotency_keys WHERE key=?", (key,)
                ).fetchone()
                if existing["operation"] != payload_hash:
                    raise ProtocolReplayConflictError("request id payload conflict")
                if existing["result"] is None:
                    raise ProtocolReplayInFlightError("request is already in flight")
                return json.loads(existing["result"])

        try:
            response = self._dispatch(request)
        except Exception:
            with self.db.transaction():
                self.db.execute("DELETE FROM idempotency_keys WHERE key=? AND result IS NULL", (key,))
            raise
        self.db.execute(
            "UPDATE idempotency_keys SET result=? WHERE key=? AND operation=? AND result IS NULL",
            (json.dumps(response, sort_keys=True), key, payload_hash),
        )
        return response

    def _dispatch(self, request: ProtocolRequest) -> dict[str, Any]:
        payload = request.payload
        if request.message_type == "ping":
            self.registry.heartbeat(request.worker_id, request.auth_token)
            return {"status": "ok", "worker_id": request.worker_id}
        if request.message_type == "claim":
            job = self.queue.claim(request.worker_id, job_type=payload.get("job_type"))
            row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job.id,)).fetchone()
            return {
                "job_id": job.id,
                "worker_id": request.worker_id,
                "lease_token": job.lease_token,
                "lease_expires_at": row["lease_expires_at"],
                "attempt_count": job.attempt_count,
            }
        if request.message_type == "describe":
            return self.queue.describe(payload["job_id"], payload["lease_token"])
        if request.message_type == "start":
            job = self.queue.start(payload["job_id"], payload["lease_token"])
            return {"job_id": job.id, "status": job.status}
        if request.message_type == "heartbeat":
            job = self.queue.heartbeat(payload["job_id"], payload["lease_token"])
            self.registry.heartbeat(request.worker_id, request.auth_token)
            return {"job_id": job.id, "status": job.status, "lease_token": job.lease_token}
        if request.message_type == "upload_artifact":
            content = base64.b64decode(payload["content_b64"], validate=True)
            artifact_id = self.queue.record_artifact(
                payload["job_id"], payload["kind"], payload["path_or_ref"],
                payload["sha256"], content=content, lease_token=payload["lease_token"],
            )
            return {"artifact_id": artifact_id, "verified": True}
        if request.message_type == "complete":
            job = self.queue.complete(
                payload["job_id"], payload["lease_token"], result=payload.get("result", {})
            )
            return {"job_id": job.id, "status": job.status}
        if request.message_type == "fail":
            job = self.queue.fail(
                payload["job_id"], payload["lease_token"],
                error=payload.get("error", "worker failure"),
                retryable=bool(payload.get("retryable", False)),
            )
            return {"job_id": job.id, "status": job.status}
        raise ValueError(f"unknown worker protocol message: {request.message_type}")


class InMemoryWorkerTransport:
    """Deterministic transport simulator; no socket or network side effects."""

    def __init__(self, server: WorkerProtocolServer):
        self.server = server
        self.available = True

    def send(self, request: ProtocolRequest) -> dict[str, Any]:
        if not self.available:
            raise ProtocolUnavailableError("worker transport unavailable")
        return self.server.handle(request)


class WorkerProtocolClient:
    def __init__(self, worker_id: str, auth_token: str, transport: InMemoryWorkerTransport):
        self.worker_id = worker_id
        self.auth_token = auth_token
        self.transport = transport

    def request(
        self,
        message_type: str,
        payload: dict[str, Any] | None = None,
        *,
        request_id: str | None = None,
        tamper_signature: bool = False,
    ) -> dict[str, Any]:
        request = ProtocolRequest.create(
            self.worker_id, self.auth_token, message_type, payload, request_id=request_id
        )
        if tamper_signature:
            request = ProtocolRequest(
                request.request_id, request.worker_id, request.message_type,
                request.payload, request.auth_token, "tampered",
            )
        return self.transport.send(request)
