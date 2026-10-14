"""Her zaman dinleyen ajan döngüsü: 'Arda'/manuel tetik → kaydet → işle → tekrar.

Döngü, gözlemlenebilir durumları `on_event(AgentEvent)` ile yayınlar; UI bunu
(queue üzerinden) tüketir. Tüm bağımlılıklar AgentDeps ile enjekte edilir →
donanım/model olmadan test edilebilir. Tek bir tur hatası daemon'u öldürmez:
loglanır, ERROR yayınlanır, backoff sonrası döngü sürer.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from arda.constants import GOODBYE
from arda.models import AgentEvent, AgentState, ProcessResult
from arda.trace import duration_ms, emit as trace, new_trace_id, trace_scope

log = logging.getLogger(__name__)

# Üst üste hata durumunda (örn. mikrofon izni yok) CPU/log selini önleyen bekleme.
_ERROR_BACKOFF_SEC = 2.0

# Sürekli sohbet oturumu "kapat kendini" ile bitince söylenen kısa onay (tek kaynak: constants).
_DEFAULT_GOODBYE = GOODBYE


def _noop_event(_event: AgentEvent) -> None:
    """Varsayılan on_event — olay yayını istemeyen çağrılar için."""


def _safe_say(say: Callable, msg: str) -> None:
    """Sesli onay kozmetiktir; başarısızlığı kaydı/işlemeyi engellememeli."""
    try:
        say(msg)
    except Exception as exc:  # noqa: BLE001 — TTS hatası kasıtlı yutulur
        log.warning("Sesli geri bildirim başarısız (yok sayıldı): %s", exc)


@dataclass
class AgentDeps:
    wait_for_wake: Callable[[], bool]                      # () -> bool (False = kapatma)
    record: Callable[[], Path]                             # () -> kaydedilen WAV yolu
    process: Callable[[Path, Callable], ProcessResult]     # (wav, on_event) -> ProcessResult
    say: Callable[[str], None]                             # (text) -> None
    cleanup: Callable[[Path], None]                        # (wav) -> None  (geçici dosyayı sil)
    # Cevap sonrası takip dinlemesi: konuşma gelirse WAV, gelmezse None (oturum biter).
    # Default None → takip yok = eski tek-tur döngüsü (geriye uyumlu).
    follow_up: Callable[[], "Path | None"] = lambda: None
    # Barge-in: TTS konuşurken mikrofonu dinle, ses gelirse kes + WAV döndür.
    # None → barge-in devre dışı (mevcut davranış korunur).
    barge_in: "Callable[[], Path | None] | None" = None


def _run_turn(
    deps: AgentDeps, wav: Path, *, on_event: Callable[[AgentEvent], None]
) -> "tuple[ProcessResult, Path | None]":
    """Kaydedilmiş wav'ı işle (wake/ack YOK): process + cleanup + sonuç olayı.

    Hem cold tur (run_once) hem takip turları bunu kullanır → işleme/temizleme/olay
    mantığı tek yerde. process_once TRANSCRIBING/THINKING yayınlar; burada DONE/ERROR.
    deps.barge_in varsa TTS sırasında mikrofon paralel dinlenir; ses gelirse (barge_wav,)
    döner — caller bunu sonraki tur girişi olarak işler."""
    from arda import speak  # lazy: döngüsel import önle

    # Yeni tur → önceki turun barge-in/'Durdur' kesmesini SIFIRLA. Bayrak tur boyunca
    # yaşar (say() silmez) — burada temizlenmezse bir kez araya girildikten sonra asistan
    # bir daha hiç konuşmaz. Barge-in thread'i BAŞLAMADAN önce yapılır ki taze kesme silinmesin.
    speak.reset_interrupt()

    barge: list[Path | None] = [None]
    barge_thread: threading.Thread | None = None
    stop_barge = threading.Event()

    if deps.barge_in is not None:
        def _listen_barge():
            try:
                barge[0] = deps.barge_in(should_stop=stop_barge.is_set)
            except TypeError:
                barge[0] = deps.barge_in()

        barge_thread = threading.Thread(target=_listen_barge, daemon=True)
        barge_thread.start()

    try:
        result = deps.process(wav, on_event)
    finally:
        stop_barge.set()
        deps.cleanup(wav)

    state = AgentState.ERROR if result.status == "failed" else AgentState.DONE
    on_event(AgentEvent(state=state, message=result.message, source="agent"))

    if barge_thread is not None:
        barge_thread.join(timeout=1.0)

    return result, barge[0]


def run_once(
    deps: AgentDeps,
    *,
    ack: str,
    on_event: Callable[[AgentEvent], None] = _noop_event,
    _barge_out: "list | None" = None,
) -> "ProcessResult | None":
    """Bir turu tek trace_id altında çalıştır."""
    with trace_scope(new_trace_id()):
        return _run_once(deps, ack=ack, on_event=on_event, _barge_out=_barge_out)


def _run_once(
    deps: AgentDeps,
    *,
    ack: str,
    on_event: Callable[[AgentEvent], None] = _noop_event,
    _barge_out: "list | None" = None,
) -> "ProcessResult | None":
    """Bir COLD tur: tetik bekle → ack → kaydet → işle. Kapatma sinyali gelirse None.

    Durum akışı: LISTENING → (tetik) → WOKE → RECORDING → [process: TRANSCRIBING,
    THINKING] → DONE/ERROR. _barge_out verilirse barge-in WAV (varsa) oraya yazılır."""
    trace("audio.capture", "waiting")
    on_event(AgentEvent(state=AgentState.LISTENING, source="agent"))
    if not deps.wait_for_wake():
        trace("audio.capture", "skipped", reason="wake_stopped")
        return None

    on_event(AgentEvent(state=AgentState.WOKE, source="agent"))
    _safe_say(deps.say, ack)

    on_event(AgentEvent(state=AgentState.RECORDING, source="agent"))
    trace("audio.capture", "started")
    capture_started = time.perf_counter()
    try:
        wav = deps.record()
    except Exception as exc:
        trace("audio.capture", "failed", duration_ms=duration_ms(capture_started), error=type(exc).__name__)
        raise
    trace("audio.capture", "completed", duration_ms=duration_ms(capture_started), wav="recorded")
    trace("agent.invocation", "started")
    invocation_started = time.perf_counter()
    try:
        result, barge_wav = _run_turn(deps, wav, on_event=on_event)
    except Exception as exc:
        trace("agent.invocation", "failed", duration_ms=duration_ms(invocation_started), error=type(exc).__name__)
        raise
    trace("agent.invocation", "completed", duration_ms=duration_ms(invocation_started), result=result.status)
    if _barge_out is not None:
        _barge_out.append(barge_wav)
    return result


def _follow_up_session(
    deps: AgentDeps,
    *,
    goodbye: str,
    stop: Callable[[], bool],
    on_event: Callable[[AgentEvent], None],
    initial_wav: "Path | None" = None,
) -> None:
    """Cevap sonrası takip turları (wake/ack YOK) → sürekli sohbet.

    Çıkış: konuşma gelmezse (follow_up None → sessizlik), `stop()`, ya da bitirme
    ifadesi (status='ended' → kısa 'goodbye' + dur). initial_wav verilirse (cold tur
    barge-in) ilk iterasyonda o kullanılır. Barge-in aktifse her turda paralel dinleme
    yapılır; ses gelirse bir sonraki turda hemen işlenir (follow_up beklenmez)."""
    pending: "Path | None" = initial_wav
    while True:
        on_event(AgentEvent(state=AgentState.LISTENING, source="agent"))
        if pending is not None:
            wav, pending = pending, None
        else:
            wav = deps.follow_up()
        if wav is None or stop():
            return
        result, barge_wav = _run_turn(deps, wav, on_event=on_event)
        pending = barge_wav  # barge-in olduysa bir sonraki iterasyonda direkt işle
        if result.status == "ended":
            _safe_say(deps.say, goodbye)
            return


def run_forever(
    deps: AgentDeps,
    *,
    ack: str,
    goodbye: str = _DEFAULT_GOODBYE,
    stop: Callable[[], bool] = lambda: False,
    on_event: Callable[[AgentEvent], None] = _noop_event,
) -> None:
    """Sürekli dinle. Her cevaptan sonra takip oturumu açılır (sürekli sohbet);
    sessizlik / 'kapat kendini' / stop ile standby'a döner. `stop()` veya wake kapatma → çık.

    Bir turdaki beklenmeyen hata loglanır, ERROR yayınlanır, backoff uygulanır; daemon
    tek bir hatayla ölmez (cold + takip oturumu aynı korumalı bloktadır)."""
    while not stop():
        try:
            barge_out: list = []
            result = run_once(deps, ack=ack, on_event=on_event, _barge_out=barge_out)
            if result is None:        # wake döngüsü kapatma sinyali aldı
                return
            log.info("[%s] %s", result.status, result.message)
            initial_barge = barge_out[0] if barge_out else None
            if result.status == "ended":
                _safe_say(deps.say, goodbye)   # cold turun kendisi bitirme ifadesiyse
            else:
                _follow_up_session(
                    deps, goodbye=goodbye, stop=stop, on_event=on_event,
                    initial_wav=initial_barge,
                )
        except Exception as exc:  # noqa: BLE001 — tur izole; daemon ayakta kalsın
            log.warning("Tur hatası, döngü sürüyor: %s", exc)
            on_event(AgentEvent(state=AgentState.ERROR, message=str(exc), source="agent"))
            time.sleep(_ERROR_BACKOFF_SEC)  # CPU spin / log seli önle
