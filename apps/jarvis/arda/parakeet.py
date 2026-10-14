"""NVIDIA Parakeet (parakeet-mlx) — paylaşımlı model yükleyici + transkripsiyon yardımcıları.

Hem wake listener hem ana STT aynı modeli kullanır: ``load_model`` repo bazlı cache
tutar → ~2.5 GB model RAM'e **tek sefer** alınır (yoksa wake + STT iki kopya yükler).

Modeller Apache 2.0; default ``mlx-community/parakeet-tdt-0.6b-v3`` (çok dilli, İngilizce
dahil — Türkçe DESTEKLENMEZ, bu yüzden ses-giriş İngilizce). Apple Silicon (MLX) gerekir.

Lazy import: ``parakeet_mlx`` / ``mlx`` yalnızca gerçek çağrıda gerekir → testler ve
``parakeet`` backend'i kapalı kurulumlar bu paketleri zorunlu kılmaz.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from arda.constants import PARAKEET_MODEL as DEFAULT_MODEL

log = logging.getLogger(__name__)

# repo → yüklü model. Modül-seviyesi cache → wake ve STT aynı instance'ı paylaşır.
_models: dict[str, object] = {}


def load_model(repo: str = DEFAULT_MODEL):
    """Parakeet modelini yükle; aynı repo için ikinci çağrıda cache'ten döner.

    Lazy import sayesinde ``parakeet_mlx`` yalnızca burada gerekir.
    """
    model = _models.get(repo)
    if model is None:
        from parakeet_mlx import from_pretrained

        model = from_pretrained(repo)
        _models[repo] = model
    return model


def transcribe_file(model, wav: Path) -> str:
    """WAV/ses dosyasını metne çevir (baş/son boşluk temizlenir).

    ``model.transcribe`` yalnızca dosya yolu kabul eder → kayıtlı WAV için bu yol.
    """
    return model.transcribe(str(wav)).text.strip()


def transcribe_array(model, audio: np.ndarray) -> str:
    """Bellekteki float32 PCM array'i (16 kHz, mono, [-1, 1]) metne çevir.

    Low-level API: önce log-mel spektrum (`get_logmel`), sonra `generate`. Wake
    döngüsü diske WAV yazmadan tamponu doğrudan değerlendirir (gecikme/IO düşük).
    """
    import mlx.core as mx
    from parakeet_mlx import DecodingConfig
    from parakeet_mlx.audio import get_logmel

    mel = get_logmel(mx.array(audio), model.preprocessor_config)
    results = model.generate(mel, decoding_config=DecodingConfig())
    if not results:
        return ""
    return results[0].text.strip()
