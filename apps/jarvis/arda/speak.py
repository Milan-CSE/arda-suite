"""Sesli geri bildirim: metni macOS 'say' ile WAV'e RENDER edip TEK kalıcı ses
çıkış akışında (sounddevice) çalar.

Neden 'say'i doğrudan hoparlöre değil de render+akış?
  - Cümle cümle akan cevapta her cümle için ayrı `say` süreci ses cihazını açıp
    kapatıyordu → cümleler arası KLİK/POP. Tek kalıcı OutputStream açık kalır → churn yok.
  - Barge-in/Durdur eskiden `say`'i SIGTERM ile TAM ortasından kesiyordu → sert POP.
    Artık kesme anında kısa bir rampa ile sıfıra iner (graceful) → pop yok.
  - Seviye limiter + giriş/çıkış fade → çatlama/distortion önlenir.

Genel API korunur: say(text, voice), interrupt()->bool, is_speaking()->bool,
iter_sentences, _strip_markdown → pipeline/__main__/listen değişmez.
"""
from __future__ import annotations

import atexit
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from typing import Iterable, Iterator

from arda.constants import TTS_VOICE
from arda.platform_runtime import tts_command, tts_render_command
from arda.trace import duration_ms, emit as trace

log = logging.getLogger(__name__)

# --- Ses parametreleri ---
_SR = 22050            # say --data-format ile SABİTLENİR → tek akış hep aynı hızda
_BLOCK = 1024          # ~46ms yazma bloğu → interrupt <~100ms'te devreye girer
_FADE_MS = 8           # giriş/çıkış rampası (onset/offset klik önler)
_PEAK_LIMIT = 0.95     # tepe seviye sınırı (clipping/distortion önler)
_IDLE_CLOSE_SEC = 3.0  # bu kadar sessizlikten sonra akışı kapat (idle hışırtı olmasın)

# --- Durum ---
_stream = None                      # kalıcı sd.OutputStream (lazy)
_stream_lock = threading.RLock()    # akış aç/kapa + yazma serileştirme
_stop = threading.Event()           # oynatmayı kes sinyali (blok döngüsü bunu okur)
_interrupted = threading.Event()    # TUR seviyesinde kesildi (barge-in / 'Durdur') — say() SİLMEZ
_session = threading.Event()        # akan cevap oturumu açık mı (cümleler arası boşlukta da True)
_speaking = threading.Event()       # şu an çalıyor mu
_idle_timer: threading.Timer | None = None

# Sesli okumadan önce temizlenecek markdown işaretleri
_MARKDOWN_RE = re.compile(r"[*#`]|^\s*[-•]\s+", re.MULTILINE)

# Cümle sonu: [.!?…] (+ kapanış tırnak/parantez) + boşluk. Boşluk şartı ondalık
# sayıyı korur ('3.14' içindeki nokta rakamla devam eder → cümle sonu değil).
_SENTENCE_END_RE = re.compile(r"[.!?…]+[\"'»)\]]*\s")


def iter_sentences(chunks: Iterable[str]) -> Iterator[str]:
    """Akan metin parçalarını (LLM token'ları) TAM cümlelere böl; her cümle hazır
    olunca yield et → çağıran cümle cümle seslendirip ilk-sese-kadar gecikmeyi düşürür.

    Cümle sonu = [.!?…] + (boşluk / akış sonu). Ondalık ('3.14') bölünmez (nokta
    boşlukla değil rakamla devam eder). Kalan tampon akış sonunda son cümle olarak
    verilir; yalnızca boşluk kalmışsa hiçbir şey verilmez."""
    buf = ""
    for chunk in chunks:
        if not chunk:
            continue
        buf += chunk
        while True:
            m = _SENTENCE_END_RE.search(buf)
            if not m:
                break
            sentence = buf[: m.end()].strip()
            if sentence:
                yield sentence
            buf = buf[m.end():]
    tail = buf.strip()
    if tail:
        yield tail


def _strip_markdown(text: str) -> str:
    """Markdown işaretçilerini sesli okuma için temizle."""
    return _MARKDOWN_RE.sub("", text).strip()


def _render(text: str, voice: str):
    """Metni `say` ile WAV'e render et → int16 mono numpy dizisi. Hata → None."""
    import numpy as np  # lazy: modül importunu hafif tut (.app fakir ortam)
    import soundfile as sf

    trace("tts.synthesis", "started", chars=len(text))
    synthesis_started = time.perf_counter()
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        command = tts_render_command(text, voice, path, _SR)
        if not command:
            trace("tts.synthesis", "failed", duration_ms=duration_ms(synthesis_started), error="provider_unavailable")
            log.warning("Bu platformda WAV TTS sağlayıcısı bulunamadı")
            return None
        proc = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if proc.returncode != 0:
            trace("tts.synthesis", "failed", duration_ms=duration_ms(synthesis_started), error="render_failed")
            log.warning("TTS render başarısız (rc=%s): %s", proc.returncode,
                        proc.stderr.decode(errors="ignore")[:200])
            return None
        data, sr = sf.read(path, dtype="int16")
        if data.ndim > 1:      # stereo gelirse tek kanala indir
            data = data[:, 0]
        if sr != _SR:          # --data-format sabitledi; yine de uyar
            log.warning("TTS örnekleme hızı beklenmedik: %s (beklenen %s)", sr, _SR)
        samples = np.ascontiguousarray(data)
        trace("tts.synthesis", "completed", duration_ms=duration_ms(synthesis_started), samples=len(samples))
        return samples
    except Exception as exc:
        trace("tts.synthesis", "failed", duration_ms=duration_ms(synthesis_started), error=type(exc).__name__)
        raise
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _shape(samples):
    """Tepe seviye sınırı + giriş/çıkış fade → clipping ve onset/offset klik önle."""
    import numpy as np
    f = samples.astype(np.float32)
    if not f.size:
        return samples
    peak = float(np.abs(f).max())
    limit = _PEAK_LIMIT * 32767.0
    if peak > limit:
        f *= limit / peak
    nf = min(int(_SR * _FADE_MS / 1000), f.size // 2)
    if nf > 0:
        up = np.linspace(0.0, 1.0, nf, dtype=np.float32)
        f[:nf] *= up
        f[-nf:] *= up[::-1]
    return f.astype(np.int16)


def _get_stream():
    """Kalıcı çıkış akışını döndür (yoksa aç). Çağıran _stream_lock'u tutmalı."""
    global _stream
    if _stream is None:
        import sounddevice as sd
        _stream = sd.OutputStream(samplerate=_SR, channels=1,
                                  dtype="int16", blocksize=_BLOCK)
        _stream.start()
    return _stream


def _play(samples) -> None:
    """Örnekleri kalıcı akışa blok blok yaz. interrupt gelirse mevcut bloğu sıfıra
    rampala (graceful) ve dur → sert kesme POP'u olmaz."""
    import numpy as np
    trace("audio.playback", "started", samples=len(samples))
    playback_started = time.perf_counter()
    try:
        stream = _get_stream()
        n = len(samples)
        i = 0
        while i < n:
            if _stop.is_set():
                tail = samples[i:i + _BLOCK]
                if len(tail):
                    ramp = np.linspace(1.0, 0.0, len(tail), dtype=np.float32)
                    stream.write((tail.astype(np.float32) * ramp).astype(np.int16))
                trace("audio.playback", "skipped", duration_ms=duration_ms(playback_started), reason="interrupted")
                return
            stream.write(samples[i:i + _BLOCK])
            i += _BLOCK
        trace("audio.playback", "completed", duration_ms=duration_ms(playback_started), physical_confirmation="manual_required")
    except Exception as exc:
        trace("audio.playback", "failed", duration_ms=duration_ms(playback_started), error=type(exc).__name__)
        raise


def _cancel_idle() -> None:
    global _idle_timer
    if _idle_timer is not None:
        _idle_timer.cancel()
        _idle_timer = None


def _schedule_idle_close() -> None:
    """Konuşma bitince kısa süre sonra akışı kapat → uzun sessizlikte idle hışırtı yok.
    Cümleler arası (<_IDLE_CLOSE_SEC) yeni say() akışı yeniden kullanır → churn/klik yok."""
    global _idle_timer
    _cancel_idle()
    _idle_timer = threading.Timer(_IDLE_CLOSE_SEC, _close_stream)
    _idle_timer.daemon = True
    _idle_timer.start()


def _close_stream() -> None:
    global _stream
    with _stream_lock:
        if _stream is not None and not _speaking.is_set():
            try:
                _stream.stop()
                _stream.close()
            except Exception as exc:  # noqa: BLE001 — kapanış hatası önemsiz
                log.debug("Akış kapatma hatası (yok sayıldı): %s", exc)
            _stream = None


def _legacy_say(text: str, voice: str) -> None:
    """Render yolu başarısızsa: düz `say` ile yine de konuş (asistan sessiz kalmasın)."""
    try:
        command = tts_command(text, voice)
        if command is None:
            log.warning("Bu platformda kullanılabilir TTS komutu yok")
            return
        subprocess.run(command, check=False)
    except Exception as exc:  # noqa: BLE001
        log.warning("Yedek 'say' de başarısız: %s", exc)


def _stop_playback() -> bool:
    """Yalnızca ÇALAN sesi durdur — tur bayrağını KİRLETME.

    say() kendi içinde 'önceki sesi kes' için bunu kullanır. Public interrupt() ile
    aynı olsaydı her yeni cümle turu 'kesilmiş' işaretler, barge-in anlamsızlaşırdı."""
    speaking = _speaking.is_set()
    _stop.set()
    return speaking


def interrupt() -> bool:
    """Devam eden TTS'i durdur (graceful rampa) ve TURU kesik işaretle.

    Barge-in (kullanıcı araya girdi) ve UI 'Durdur' bunu çağırır. Bayrak reset_interrupt()
    ile yeni turda temizlenir — araya say() girse bile silinmez, yoksa asistan bir cümle
    susup kaldığı yerden devam eder.

    True = konuşuyordu, False = boştaydı."""
    speaking = _stop_playback()
    _interrupted.set()
    return speaking


def was_interrupted() -> bool:
    """Bu tur barge-in/'Durdur' ile kesildi mi? (pipeline should_stop bunu okur)"""
    return _interrupted.is_set()


def reset_interrupt() -> None:
    """Yeni turdan önce kesme durumunu sıfırla (eski kesme yeni turu susturmasın)."""
    _interrupted.clear()
    _stop.clear()


def begin_stream() -> None:
    """Akan cevap oturumunu aç: cümleler arası render boşluğunda da is_active() True kalır
    → barge-in dinleyicisi ilk cümleden sonra ölmez."""
    _session.set()


def end_stream() -> None:
    """Akan cevap oturumunu kapat."""
    _session.clear()


def is_active() -> bool:
    """Konuşuyor VEYA akan cevap oturumu sürüyor mu? (barge-in penceresinin koşulu)"""
    return _speaking.is_set() or _session.is_set()


def is_speaking() -> bool:
    """TTS şu an konuşuyor mu?"""
    return _speaking.is_set()


def say(text: str, *, voice: str = TTS_VOICE) -> None:
    """Metni seslendir. Render→tek kalıcı akış; barge-in interrupt() ile araya girebilir."""
    text = _strip_markdown(text)
    if not text:
        return
    if _interrupted.is_set():   # tur kesildi → yeni cümle BAŞLATMA (savunma katmanı:
        return                  # pipeline should_stop'u kaçırsa bile asistan susar)
    _stop_playback()     # varsa önceki sesi durdur (tur bayrağına dokunmadan)
    _stop.clear()
    samples = _render(text, voice)
    if samples is None:  # render/format hatası → düz say ile yedekle
        _legacy_say(text, voice)
        return
    if not len(samples):
        return
    samples = _shape(samples)
    with _stream_lock:
        _cancel_idle()
        _speaking.set()
        try:
            _play(samples)
        finally:
            _speaking.clear()
            _schedule_idle_close()


# Uygulama kapanınca devam eden sesi kes ve akışı kapat.
# atexit LIFO çalışır → SON kayıt İLK çalışır: önce sesi kes (_speaking düşsün),
# sonra akış kapansın. Ters sırada _close_stream 'konuşuyor' görüp akışı kapatamıyordu.
atexit.register(_close_stream)
atexit.register(interrupt)
