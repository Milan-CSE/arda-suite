"""Konuşmacı profili oluştur — 5 kez 'Hey Jarvis' de, embedding kaydedilir.

Kullanım:
  .venv/bin/python scripts/enroll_speaker.py
  .venv/bin/python scripts/enroll_speaker.py --samples 7 --out models/benim_sesim.npy
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import sounddevice as sd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arda.speaker import embed, save_profile

_SAMPLERATE = 16000
_RECORD_SEC = 3  # her örnek için kayıt süresi


def _record(label: str) -> np.ndarray:
    input(f"  {label} — hazır olunca Enter'a bas, sonra konuş...")
    print(f"  Kaydediliyor ({_RECORD_SEC}s)... 'Hey Jarvis' de!")
    frames = sd.rec(
        int(_RECORD_SEC * _SAMPLERATE),
        samplerate=_SAMPLERATE,
        channels=1,
        dtype="float32",
    )
    sd.wait()
    audio = frames.flatten()
    rms = np.sqrt(np.mean(audio ** 2))
    if rms < 0.001:
        print("  ⚠️  Ses çok düşük — mikrofon iznini ve seviyeyi kontrol et.")
    else:
        print(f"  ✓  Kaydedildi (RMS: {rms:.4f})")
    return audio


def main() -> None:
    ap = argparse.ArgumentParser(description="Konuşmacı profili oluştur.")
    ap.add_argument("--samples", type=int, default=5, help="Kaç örnek kaydedilsin (varsayılan: 5)")
    ap.add_argument("--out", default="models/speaker_profile.npy", help="Çıktı dosyası")
    args = ap.parse_args()

    out = Path(args.out)
    print(f"\n=== Jarvis Konuşmacı Kaydı ===")
    print(f"Toplam {args.samples} örnek kaydedilecek.")
    print("Her seferinde net bir şekilde 'Hey Jarvis' de.\n")

    embeddings = []
    for i in range(args.samples):
        audio = _record(f"Örnek {i + 1}/{args.samples}")
        emb = embed(audio)
        embeddings.append(emb)
        print(f"  Embedding oluşturuldu.\n")

    save_profile(embeddings, out)
    print(f"✅ Profil kaydedildi: {out}")
    print(f"\nconfig.local.yaml'a şunu ekle:")
    print(f"  wake:")
    print(f"    speaker_verify: true")
    print(f"    speaker_profile: {out.resolve()}")


if __name__ == "__main__":
    main()
