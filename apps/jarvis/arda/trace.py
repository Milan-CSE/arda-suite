"""Tek konuşma turu için hafif, yapılandırılmış E2E trace bağlamı.

Trace yalnızca gözlemlenebilirlik içindir; bir aşamadaki log hatası konuşma
akışını değiştirmez. ContextVar kullanıldığı için aynı turdaki pipeline, TTS ve
playback kayıtları tek ``trace_id`` ile eşleşir.
"""
from __future__ import annotations

import contextvars
import logging
import time
import uuid
from contextlib import contextmanager
from typing import Iterator

log = logging.getLogger(__name__)

_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "arda_trace_id", default=None
)


def new_trace_id() -> str:
    """Kısa ve log aramaya uygun yeni tur kimliği üret."""
    return uuid.uuid4().hex[:12]


def current_trace_id() -> str | None:
    return _trace_id.get()


def duration_ms(started_at: float) -> float:
    """Monotonic elapsed time, rounded for readable journal logs."""
    return round((time.perf_counter() - started_at) * 1000, 1)


@contextmanager
def trace_scope(trace_id: str | None = None) -> Iterator[str]:
    """Bir konuşma turu boyunca trace kimliğini bağlama yerleştir."""
    value = trace_id or new_trace_id()
    token = _trace_id.set(value)
    try:
        yield value
    finally:
        _trace_id.reset(token)


def emit(stage: str, status: str = "completed", **metadata: object) -> None:
    """Bir E2E aşamasını logla; metadata hassas içerik taşımamalı."""
    trace_id = current_trace_id()
    if not trace_id:
        return
    details = " ".join(f"{key}={value!r}" for key, value in metadata.items())
    suffix = f" {details}" if details else ""
    log.info("[trace=%s] stage=%s status=%s%s", trace_id, stage, status, suffix)
