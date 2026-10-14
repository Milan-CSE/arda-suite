"""Desktop-agent veri modelleri: risk seviyesi, araç sonucu, plan, adım logu, koşu sonucu.

Saf veri + LLM çıktısı doğrulama. `Plan`/`PlanStep` Ollama planlayıcısından gelen ham
JSON'u doğrular (bkz. agent_planner); hatalı yapı ValueError → executor güvenli fallback'e
düşer. params değerleri `Intent` kalıbıyla string'e normalize edilir (LLM bazen sayı döndürür).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RiskLevel(str, Enum):
    """Bir araç çağrısının yürütme riski → izin katmanının kararı.

    auto: salt-okuma/listeleme/arama/test → onaysız çalışır.
    confirm: yazma/kurulum/commit/dış-veri → kullanıcı onayı gerekir.
    deny: sır okuma/destructive/izinsiz mail → asla çalıştırılmaz.
    """

    AUTO = "auto"
    CONFIRM = "confirm"
    DENY = "deny"


@dataclass(frozen=True)
class ToolResult:
    """Bir aracın sonucu. `ok` her zaman vardır; handler'lar ASLA raise etmez →
    hata `ok=False` + `error` ile taşınır (pipeline bozulmaz)."""

    ok: bool
    output: str = ""              # kısa, özetlenebilir/sesli-dostu çıktı
    error: str = ""              # ok=False ise hata açıklaması
    data: dict | None = None     # yapılandırılmış sonuç (dosya listesi, test sayıları...)


def _coerce_params(raw: object) -> dict:
    """Ham params'ı string-değerli dict'e çevir (LLM sayı/bool döndürebilir).

    `Intent.from_dict` ile aynı normalizasyon: anahtarlar string olmalı, değerler
    str()'e çekilir → araç handler'ları tek tip (str) girdi görür."""
    if not isinstance(raw, dict) or not all(isinstance(k, str) for k in raw):
        raise ValueError("params string-anahtarlı bir nesne olmalı")
    return {k: str(v) for k, v in raw.items()}


@dataclass(frozen=True)
class PlanStep:
    """Planlayıcının ürettiği tek adım: hangi araç, hangi parametreler, neden."""

    tool: str
    params: dict = field(default_factory=dict)
    rationale: str = ""          # "ne yapmak istedi" — loglama + şeffaflık

    @classmethod
    def from_dict(cls, data: object) -> "PlanStep":
        if not isinstance(data, dict):
            raise ValueError(f"PlanStep dict olmalı, '{type(data).__name__}' geldi")
        tool = data.get("tool")
        if not isinstance(tool, str) or not tool.strip():
            raise ValueError("PlanStep.tool dolu bir string olmalı")
        params = _coerce_params(data.get("params", {}))
        rationale = data.get("rationale", "")
        if not isinstance(rationale, str):
            rationale = str(rationale)
        return cls(tool=tool, params=params, rationale=rationale)


@dataclass(frozen=True)
class Plan:
    """Planlayıcının tüm çıktısı: kısa özet + sıralı adımlar."""

    summary: str
    steps: tuple[PlanStep, ...]

    @classmethod
    def from_dict(cls, data: object) -> "Plan":
        if not isinstance(data, dict):
            raise ValueError(f"Plan dict olmalı, '{type(data).__name__}' geldi")
        summary = data.get("summary", "")
        if not isinstance(summary, str):
            summary = str(summary)
        raw_steps = data.get("steps", [])
        if not isinstance(raw_steps, list):
            raise ValueError("Plan.steps bir liste olmalı")
        steps = tuple(PlanStep.from_dict(s) for s in raw_steps)
        return cls(summary=summary, steps=steps)


@dataclass(frozen=True)
class StepLog:
    """Bir adımın tam izi — şeffaflık + hata ayıklama için.

    decision: "ran" | "skipped" | "denied" | "confirmed-no" | "error".
    """

    tool: str
    params: dict
    rationale: str
    risk: RiskLevel
    decision: str
    ok: bool
    output: str = ""
    error: str = ""
    verified: bool = True


@dataclass(frozen=True)
class AgentRunResult:
    """run_agent() dönüşü: sesli özet + tam adım izi + durum.

    status: "done" (hepsi ok) | "partial" (bazısı atlandı/hata) | "denied" (engellendi)
            | "empty" (plan boş) | "failed" (plan üretilemedi).
    """

    status: str
    spoken: str
    logs: tuple[StepLog, ...] = ()
    skill_id: str | None = None
    skill_source: str | None = None
    skill_hash: str | None = None
    run_id: str | None = None
    started_at: str | None = None
    duration_ms: float | None = None
    completed_steps: int = 0
    interrupted_step: str | None = None
    termination_reason: str | None = None
