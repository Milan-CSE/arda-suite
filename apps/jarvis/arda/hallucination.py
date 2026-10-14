"""Whisper sessizlik-halüsinasyonu filtresi + ses enerji (RMS) geçidi.

Whisper (YouTube ağırlıklı eğitim) SESSİZLİKTE stok kalıplar uydurur: "İzlediğiniz
için teşekkür ederim", "Altyazı M.K.", "Thank you for watching" gibi. Kayıt gerçekten
sessizse bu metin ekrana yazılır/işlenir (dikte) veya komut sanılır. Üstelik whisper
bu uydurmaya EMİNDİR (no_speech_prob=0), o yüzden whisper'ın kendi sinyaline güvenilmez.

İki savunma:
  1. `is_silent(wav)` — ses enerjisi (RMS) eşiğin altındaysa hiç transkribe etme.
     Ölçüm: sessizlik ~0.008, konuşma ~0.12 → ~16x ayrım; eşik 0.02 güvenli.
  2. `is_hallucination(text)` — enerji geçse bile (hafif gürültü) bilinen stok
     kalıpları yakala (tam-metin eşleşme → gerçek konuşmayı yanlış elemez).
"""
from __future__ import annotations

import re
import wave
from pathlib import Path

import numpy as np

_DEFAULT_SILENCE_RMS = 0.02

# Bilinen Whisper sessizlik-uydurmaları (normalize edilmiş: küçük harf, yalnız harf+boşluk).
_HALLUCINATIONS = frozenset(
    {
        "izlediğiniz için teşekkür ederim",
        "izlediğiniz için teşekkürler",
        "teşekkürler",
        "abone olmayı unutmayın",
        "kanalıma abone olmayı unutmayın",
        "bir sonraki videoda görüşmek üzere",
        "bir sonraki videoda görüşürüz",
        "thank you for watching",
        "thanks for watching",
        "please subscribe",
        "altyazı mk",
        "altyazı m k",
    }
)


def _normalize(text: str) -> str:
    """Küçük harf + Türkçe İ→i düzeltme (combining dot sil) + yalnız harf/boşluk bırak."""
    low = text.lower().replace("̇", "")  # 'İ'.lower() = 'i̇' (i + U+0307) → dot'u at
    only = re.sub(r"[^a-zçğıöşü ]", "", low)
    return re.sub(r"\s+", " ", only).strip()


def is_hallucination(text: str) -> bool:
    """Metin boş ya da bilinen bir sessizlik-uydurmasıysa True (işlenmemeli/yazılmamalı)."""
    n = _normalize(text)
    if not n:
        return True
    if n in _HALLUCINATIONS:
        return True
    if n.startswith("altyazı"):  # 'Altyazı: ...', 'Altyazı M.K.' kredileri
        return True
    return False


def audio_rms(wav: Path | str) -> float:
    """WAV dosyasının normalize (0..1) RMS enerjisini döndür. Okunamıyorsa 1.0 (güvenli:
    yüksek → sessiz sayılmaz → transkribe edilir; halüsinasyon filtresi yine devrede)."""
    try:
        with wave.open(str(wav), "rb") as wf:
            frames = wf.readframes(wf.getnframes())
        if not frames:
            return 0.0
        samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(samples**2)))
    except Exception:  # noqa: BLE001 — okunamayan ses transkripsiyona bırakılır
        return 1.0


def is_silent(wav: Path | str, threshold: float = _DEFAULT_SILENCE_RMS) -> bool:
    """Kayıt enerjisi eşiğin altında mı (konuşma yok → whisper uydurmasın diye atla)."""
    return audio_rms(wav) < threshold
