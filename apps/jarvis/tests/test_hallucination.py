"""arda.hallucination — sessizlik geçidi + whisper uydurma filtresi."""
from __future__ import annotations

import wave

import numpy as np

from arda import hallucination


def _wav(tmp_path, rms_level: float):
    """Belirtilen RMS'e yakın int16 WAV üret."""
    p = tmp_path / "a.wav"
    n = 16000
    if rms_level <= 0:
        data = np.zeros(n, dtype=np.int16)
    else:
        data = (np.random.default_rng(0).normal(0, rms_level, n) * 32768).astype(np.int16)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(data.tobytes())
    return p


def test_silent_wav_is_silent(tmp_path):
    assert hallucination.is_silent(_wav(tmp_path, 0.0)) is True


def test_loud_wav_not_silent(tmp_path):
    assert hallucination.is_silent(_wav(tmp_path, 0.12)) is False


def test_known_hallucinations_filtered():
    for t in [
        "İzlediğiniz için teşekkür ederim.",
        "izlediğiniz için teşekkür ederim",
        "Altyazı M.K.",
        "Altyazı: Bir Film",
        "Thank you for watching",
        "",
        "   ",
    ]:
        assert hallucination.is_hallucination(t) is True, t


def test_real_speech_not_filtered():
    for t in ["nasılsın bugün", "yarın toplantı var mı", "hava durumu nedir"]:
        assert hallucination.is_hallucination(t) is False, t


def test_missing_file_not_silent():
    # Okunamayan dosya → 1.0 RMS → sessiz DEĞİL (transkripsiyona bırak, filtre yine devrede).
    assert hallucination.is_silent("/yok/olmayan.wav") is False
