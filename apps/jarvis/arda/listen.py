"""Mikrofon kaydı: enerji tabanlı VAD ile sessizlikte otomatik dur.

Üç giriş: `record_until_silence` (tetik sonrası tek kayıt), `record_follow_up`
(cevap sonrası takip dinlemesi) ve `record_barge_in` (TTS konuşurken araya girme).
Ortak VAD/yazma mantığı yardımcılarda toplanır (DRY)."""
from __future__ import annotations

import logging
import tempfile
import time
import wave
from pathlib import Path
from typing import Callable

import numpy as np
import sounddevice as sd


log = logging.getLogger(__name__)

_SAMPLERATE = 16000
_CHUNK_SEC = 0.1  # 100ms parçalar halinde oku
_DEFAULT_MAX_SEC = 60.0  # gürültülü ortamda sonsuz kayıt koruması
_DEFAULT_FOLLOW_UP_SEC = 8.0  # cevap sonrası konuşma başlangıcını bu kadar bekle
_BARGE_IN_WAIT_SEC = 30.0  # TTS başlamasını bu kadar bekle (speaking öncesi gecikme)


_DEFAULT_THRESHOLD = 0.01  # kalibrasyon yapılamadığında (TTS açık) güvenli varsayılan
_calibrated: float | None = None  # süreç ömrü boyunca tek ölçüm (bkz. _resolve_threshold)


def _calibrate_threshold(samplerate: int) -> float:
    """1 saniyelik gürültü tabanını ölçerek enerji eşiğini belirle."""
    noise = sd.rec(samplerate, samplerate=samplerate, channels=1, dtype="float32")
    sd.wait()
    return float(np.abs(noise).mean()) * 3.0


def _resolve_threshold(samplerate: int) -> float:
    """Kalibre eşiği süreç başına BİR kez ölç ve önbelleğe al.

    Neden önbellek: kalibrasyon 1 saniye mikrofon kaydı. Her kayıt/takip turu/barge-in
    öncesi tekrarlanınca tetik ile kaydın başlaması arasına 1s ölü zaman giriyor ve
    kullanıcının ilk hecesi kaybolabiliyor.

    Neden TTS açıkken ölçmüyoruz: hoparlörden sızan asistan sesi 'gürültü tabanı' sanılır,
    eşik şişer ve barge-in bir daha tetiklenmez. O durumda ölçmeyip güvenli varsayılana
    düşer ve önbelleğe YAZMAZ → TTS bitince gerçek ölçüm yapılır."""
    global _calibrated
    if _calibrated is not None:
        return _calibrated
    from arda import speak  # lazy: döngüsel import önle

    if speak.is_active():
        return _DEFAULT_THRESHOLD
    _calibrated = _calibrate_threshold(samplerate)
    return _calibrated


def _vad_params(cfg: dict) -> tuple[float, int, int, int]:
    """cfg.vad'dan (eşik, chunk_size, silence_limit, max_chunks) türet; eşik yoksa kalibre et."""
    vad = cfg.get("vad", {})
    silence_sec: float = vad.get("silence_sec", 1.2)
    threshold = vad.get("energy_threshold")
    max_sec: float = vad.get("max_sec", _DEFAULT_MAX_SEC)

    chunk_size = int(_SAMPLERATE * _CHUNK_SEC)
    silence_limit = int(silence_sec / _CHUNK_SEC)
    max_chunks = int(max_sec / _CHUNK_SEC)
    if threshold is None:
        threshold = _resolve_threshold(_SAMPLERATE)
    return float(threshold), chunk_size, silence_limit, max_chunks


def _drain_until_silence(
    stream, frames: list[np.ndarray], *, chunk_size: int, threshold: float,
    silence_limit: int, max_chunks: int, should_stop: "Callable[[], bool]" = lambda: False,
) -> list[np.ndarray]:
    """`frames`'e ekleyerek sessizliğe (silence_limit) ya da max_chunks'a kadar kaydet.

    should_stop() True olursa (UI 'Durdur') kayıt hemen biter → sonsuz 'Kaydediyor...'
    takılması durdurulabilir."""
    silence_count = 0
    while True:
        if should_stop():           # UI 'Durdur' → kaydı hemen kes
            break
        chunk, _ = stream.read(chunk_size)
        frames.append(chunk.copy())
        if float(np.abs(chunk).mean()) < threshold:
            silence_count += 1
            if silence_count >= silence_limit:
                break
        else:
            silence_count = 0
        if len(frames) >= max_chunks:  # sonsuz döngü koruması
            break
    return frames


def _write_wav(frames: list[np.ndarray]) -> Path:
    """float32 kareleri 16-bit PCM WAV'a yaz; Path döndür."""
    audio = np.concatenate(frames, axis=0)
    # Kaydın gerçek enerjisini logla → mikrofon sinyali geliyor mu (izin/cihaz teşhisi).
    # ~0.0 = app'e ses gelmiyor (TCC mikrofon izni yok → macOS sessizlik besler).
    rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2))) if audio.size else 0.0
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    log.info("kayıt: dur=%.2fs rms=%.5f peak=%.4f", audio.size / _SAMPLERATE, rms, peak)
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    with wave.open(tmp.name, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_SAMPLERATE)
        wf.writeframes((audio * 32767).astype(np.int16).tobytes())
    return Path(tmp.name)


def record_until_silence(cfg: dict, *, should_stop: "Callable[[], bool]" = lambda: False) -> Path:
    """Mikrofonu dinler; konuşma başlayınca kaydeder, sessizlikte durur; WAV Path döner.

    Önce `app.listen_onset_sec` (varsayılan 6s) kadar konuşma başlangıcı bekler —
    'Efendim?' bittikten sonra kullanıcı konuşmaya başlayana kadar kayıt açık kalır.
    Onset gelmezse yine de kaydeder (tampon zaten elimizde).

    should_stop() True olursa (UI 'Durdur') kayıt hemen kesilir → eldeki (kısa/boş) ses
    yazılır; transkript sessizlik geçidinden geçip boş dönebilir (pipeline sessiz no-op)."""
    onset_sec = float(cfg.get("app", {}).get("listen_onset_sec", 6.0))
    threshold, chunk_size, silence_limit, max_chunks = _vad_params(cfg)
    onset_chunks = int(onset_sec / _CHUNK_SEC)
    with sd.InputStream(samplerate=_SAMPLERATE, channels=1, dtype="float32") as stream:
        # Konuşma başlangıcını bekle
        pre_frames: list[np.ndarray] = []
        for _ in range(onset_chunks):
            if should_stop():        # UI 'Durdur' → konuşma başlamadan iptal
                return _write_wav(pre_frames or [np.zeros((1, 1), dtype=np.float32)])
            chunk, _ = stream.read(chunk_size)
            pre_frames.append(chunk.copy())
            if float(np.abs(chunk).mean()) >= threshold:
                # Konuşma başladı — biriktirilen pre_frames'le devam et
                frames = _drain_until_silence(
                    stream, pre_frames, chunk_size=chunk_size, threshold=threshold,
                    silence_limit=silence_limit, max_chunks=max_chunks, should_stop=should_stop,
                )
                return _write_wav(frames)
        # Onset gelmedi ama yine de elindeki sesi yaz (kısa tepki olabilir)
        frames = _drain_until_silence(
            stream, pre_frames, chunk_size=chunk_size, threshold=threshold,
            silence_limit=silence_limit, max_chunks=max_chunks, should_stop=should_stop,
        )
    return _write_wav(frames)


def record_follow_up(cfg: dict) -> Path | None:
    """Cevap sonrası takip dinlemesi: pencere içinde konuşma başlarsa kaydet, yoksa None.

    `app.follow_up_window_sec` boyunca konuşma onset'i (eşik üstü) beklenir; gelirse o
    karelerden itibaren `record_until_silence` mantığıyla kaydedip WAV döner, gelmezse
    None (oturum biter → standby). `follow_up_window_sec <= 0` ise özellik kapalı → None."""
    window_sec = float(cfg.get("app", {}).get("follow_up_window_sec", _DEFAULT_FOLLOW_UP_SEC))
    if window_sec <= 0:
        return None  # kapalı: mic açma, oturumu hemen bitir
    threshold, chunk_size, silence_limit, max_chunks = _vad_params(cfg)
    window_chunks = int(window_sec / _CHUNK_SEC)
    with sd.InputStream(samplerate=_SAMPLERATE, channels=1, dtype="float32") as stream:
        for _ in range(window_chunks):
            chunk, _ = stream.read(chunk_size)
            if float(np.abs(chunk).mean()) >= threshold:  # konuşma başladı
                frames = _drain_until_silence(
                    stream, [chunk.copy()], chunk_size=chunk_size, threshold=threshold,
                    silence_limit=silence_limit, max_chunks=max_chunks,
                )
                return _write_wav(frames)
    return None  # pencere doldu, konuşma yok


def record_barge_in(cfg: dict, *, should_stop: Callable[[], bool] = lambda: False) -> Path | None:
    """TTS konuşurken mikrofonu dinle; ses algılanırsa TTS'i kes ve kaydet.

    TTS başlamasını _BARGE_IN_WAIT_SEC kadar bekler; başlamazsa veya should_stop() True
    olursa None döner. Speaking biterse barge-in olmadan None döner. Kullanıcı konuşursa
    TTS kesilir ve ses _drain_until_silence ile kaydedilip WAV Path olarak döner.

    Barge-in eşiği normal VAD eşiğinden yüksek tutulur (vad.barge_in_multiplier, default 4.0):
    hoparlörden sızan TTS sesi yanlışlıkla tetiklemesin; kullanıcı sesi daha yüksek olduğu
    için yine de algılanır."""
    from arda import speak  # döngüsel import önlemek için lazy

    base_threshold, chunk_size, silence_limit, max_chunks = _vad_params(cfg)
    # Hoparlör-mikrofon yansımasını geçirmemek için daha yüksek eşik
    multiplier = float(cfg.get("vad", {}).get("barge_in_multiplier", 4.0))
    barge_threshold = base_threshold * multiplier

    # TTS başlayana kadar bekle (transcribe + Ollama + sentez süresi). Single-shot
    # pipeline LLM üretirken `speak.is_active()` açılmaz; kapı yalnız gerçek playback
    # sırasında açılır ve mikrofon bu aşamada barge-in için okunur.
    deadline = time.monotonic() + _BARGE_IN_WAIT_SEC
    while not speak.is_active():
        if should_stop() or time.monotonic() >= deadline:
            return None
        time.sleep(0.05)

    with sd.InputStream(samplerate=_SAMPLERATE, channels=1, dtype="float32") as stream:
        while speak.is_active() and not should_stop():
            chunk, _ = stream.read(chunk_size)
            if float(np.abs(chunk).mean()) >= barge_threshold:
                speak.interrupt()
                frames = _drain_until_silence(
                    stream, [chunk.copy()],
                    chunk_size=chunk_size,
                    threshold=base_threshold,  # kayıt sırasında normal eşik
                    silence_limit=silence_limit,
                    max_chunks=max_chunks,
                    should_stop=should_stop,
                )
                return _write_wav(frames)
    return None  # akış bitti, barge-in olmadı
