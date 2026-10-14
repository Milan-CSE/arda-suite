"""Veri modelleri: Intent (LLM çıktısı), ProcessResult (pipeline sonucu),
AgentState/AgentEvent (UI'ya yayılan ajan durum akışı)."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from arda.trace import current_trace_id


@dataclass(frozen=True)
class Intent:
    """brain.py'nin Ollama'dan JSON schema ile aldığı doğrulanmış niyet."""

    intent: str
    category: str
    title: str
    clean_text: str
    tags: tuple[str, ...]
    follow_up: tuple[str, ...]
    action: str = ""                                  # intent=="action" → eylem adı (beyaz liste)
    params: dict = field(default_factory=dict)        # eylem parametreleri (str→str)
    confidence: str = "high"                          # router öz-güveni: "high" | "low" (belirsizse "low")
    mem_durable: bool = False                          # router: bu tur kalıcı gerçek içeriyor mu
    mem_scope: str = "personal"                        # "personal" | "jarvis"

    _STR_FIELDS = ("intent", "category", "title", "clean_text")
    _LIST_FIELDS = ("tags", "follow_up")

    @classmethod
    def from_dict(cls, data: object) -> "Intent":
        """Ham dict'i doğrula ve Intent'e çevir. Hatalıysa ValueError (→ Inbox fallback)."""
        if not isinstance(data, dict):
            raise ValueError(f"Intent verisi dict olmalı, '{type(data).__name__}' geldi")
        missing = [f for f in (*cls._STR_FIELDS, *cls._LIST_FIELDS) if f not in data]
        if missing:
            raise ValueError(f"Eksik alan(lar): {', '.join(missing)}")
        for name in cls._STR_FIELDS:
            if not isinstance(data[name], str):
                raise ValueError(f"'{name}' string olmalı, '{type(data[name]).__name__}' geldi")
        for name in cls._LIST_FIELDS:
            value = data[name]
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise ValueError(f"'{name}' string listesi olmalı")
        action = data.get("action", "")
        if not isinstance(action, str):
            raise ValueError(f"'action' string olmalı, '{type(action).__name__}' geldi")
        raw_params = data.get("params", {})
        if not isinstance(raw_params, dict) or not all(isinstance(k, str) for k in raw_params):
            raise ValueError("'params' string-anahtarlı bir nesne olmalı")
        # Değerleri string'e normalize et (LLM bazen sayı döndürür: level=50 → "50").
        params = {k: str(v) for k, v in raw_params.items()}
        # confidence opsiyonel (eski yükte yok) ve serbest: yalnız "low" anlamlı, gerisi
        # "high"a düşer → string değilse/eksikse güvenli varsayılan "high" (geriye uyumlu).
        confidence = "low" if data.get("confidence") == "low" else "high"
        # Hafıza etiketi OPSİYONEL + fail-safe: geçersizse özellik sessizce kapanır,
        # routing ASLA bozulmaz. Yalnız bool True → durable; scope yalnız personal|jarvis.
        mem_durable = data.get("mem_durable") is True
        mem_scope = data.get("mem_scope") if data.get("mem_scope") in ("personal", "jarvis") else "personal"
        return cls(
            intent=data["intent"],
            category=data["category"],
            title=data["title"],
            clean_text=data["clean_text"],
            tags=tuple(data["tags"]),
            follow_up=tuple(data["follow_up"]),
            action=action,
            params=params,
            confidence=confidence,
            mem_durable=mem_durable,
            mem_scope=mem_scope,
        )


@dataclass(frozen=True)
class ActionResult:
    """Bir Mac eyleminin sonucu: sesli onay metni + (opsiyonel) yapılandırılmış bellek.

    `spoken` her zaman vardır (kullanıcıya okunan kısa onay). `memory` yalnızca sonradan
    atıf gerektiren eylemlerde dolar (ör. set_reminder → {action, success, text, at, id});
    bu payload sohbet belleğine yazılıp hem router hem answer motoruna bağlam olur, böylece
    "az önce kurduğum hatırlatıcıyı sil" gibi atıflar çözülebilir. Düz eylemlerde None."""

    spoken: str
    memory: dict | None = None


@dataclass(frozen=True)
class ProcessResult:
    """pipeline.process_once() dönüşü — test edilebilir + loglanabilir sonuç (None değil)."""

    status: str               # saved|task|answered|action|researched|inbox|unclear|failed|ended
    message: str              # kullanıcıya sesli onay metni
    path: Path | None = None  # yazılan dosya (varsa)
    intent: Intent | None = None


class AgentState(str, Enum):
    """Ajan döngüsünün UI'ya yaydığı yaşam-döngüsü durumları.

    str tabanlı → değer doğrudan string gibi loglanır/serialize edilir.
    """

    LISTENING = "listening"        # "Arda" / manuel tetik bekleniyor
    WOKE = "woke"                  # uyandı, onaylanıyor
    RECORDING = "recording"        # mikrofon kaydı sürüyor
    TRANSCRIBING = "transcribing"  # STT (ses → metin)
    THINKING = "thinking"          # beyin (Ollama sınıflandırma)
    DONE = "done"                  # tur tamam (not/görev/inbox yazıldı)
    ERROR = "error"                # tur hatası (daemon ayakta, yeniden denenecek)


@dataclass(frozen=True)
class AgentRuntimeEvent:
    """Structured agent-run event carried by the existing AgentEvent queue."""

    event_type: str
    run_id: str
    step_id: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = "running"
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AgentEvent:
    """Worker thread'den UI'ya geçen tek durum olayı (queue ile taşınır).

    timestamp her örnekte otomatik atanır; source olayı yayanı belirtir
    ("agent" | "pipeline" | "manual" | ...).
    """

    state: AgentState
    message: str = ""
    timestamp: float = field(default_factory=time.time)
    source: str = "agent"
    runtime: AgentRuntimeEvent | None = None
    trace_id: str | None = field(default_factory=current_trace_id)
