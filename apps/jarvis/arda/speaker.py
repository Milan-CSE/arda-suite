"""Konuşmacı doğrulama — sadece kayıtlı sesin wake word'ü tetiklemesi için.

Kullanım:
  1. `scripts/enroll_speaker.py` ile profil oluştur → models/speaker_profile.npy
  2. Config'de `wake.speaker_verify: true` yap
  3. WhisperWakeListener her tetiklemede verify() çağırır

resemblyzer GE2E encoder: 256-d embedding, cosine similarity ile karşılaştırır.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

_SAMPLERATE = 16000


def _encoder():
    from resemblyzer import VoiceEncoder
    return VoiceEncoder()


def embed(audio_float32: np.ndarray) -> np.ndarray:
    """Float32 [-1, 1] audio → 256-d L2-normalized embedding vektörü."""
    enc = _encoder()
    # resemblyzer preprocess_wav bekliyor: float64, [-1,1]
    wav = audio_float32.astype(np.float64)
    return enc.embed_utterance(wav)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    a = a / (np.linalg.norm(a) + 1e-9)
    b = b / (np.linalg.norm(b) + 1e-9)
    return float(np.dot(a, b))


def save_profile(embeddings: list[np.ndarray], path: Path) -> None:
    """Birden fazla embedding'i ortalamasını alarak kaydet."""
    mean_emb = np.mean(embeddings, axis=0)
    mean_emb = mean_emb / (np.linalg.norm(mean_emb) + 1e-9)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(str(path), mean_emb)


def load_profile(path: Path) -> np.ndarray:
    return np.load(str(path))


def verify(audio_float32: np.ndarray, profile: np.ndarray, threshold: float = 0.75) -> bool:
    """Ses kaydının profille eşleşip eşleşmediğini döndürür."""
    emb = embed(audio_float32)
    sim = cosine_similarity(emb, profile)
    return sim >= threshold
