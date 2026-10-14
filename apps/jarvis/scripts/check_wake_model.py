"""openWakeWord modelini YEREL doğrula — eğitim sonrası akıl-sağlığı + eşik ayarı.

Mikrofon/eğitim gerektirmez: modelin yüklendiğini ve çıkarımın çökmeden çalıştığını
kanıtlar. --wav verilirse o ses boyunca anahtar başına EN YÜKSEK wake skorunu yazar
→ threshold'u ona göre seç.

Kullanım:
  python scripts/check_wake_model.py                       # config.local.yaml'daki modeli + smoke
  python scripts/check_wake_model.py --model models/hey_arda.onnx
  python scripts/check_wake_model.py --wav ~/Desktop/hey-arda.wav
  make check-wake                  # = ilk biçim
  make check-wake WAV=clip.wav     # = --wav
  make check-wake MODEL=models/hey_arda.onnx
"""
from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

import numpy as np

# Repo kökünü path'e ekle (script doğrudan çalıştırılınca `arda` import edilsin).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arda.config import ConfigError, load_config  # noqa: E402
from arda.wake import _FRAME, _SAMPLERATE, WakeListener  # noqa: E402


def _build_cfg(model: str | None, framework: str | None) -> dict:
    """config.local.yaml'daki wake ayarını al; --model/--framework verilirse ez."""
    try:
        cfg = dict(load_config())
    except ConfigError:
        cfg = {}  # vault.path yoksa bile model testi yapılabilsin
    wake = dict(cfg.get("wake", {}))
    if model:
        wake["model_path"] = model
    if framework:
        wake["framework"] = framework
    cfg["wake"] = wake
    return cfg


def _read_wav_int16(path: Path) -> np.ndarray:
    """16 kHz mono 16-bit PCM wav oku → int16 dizi (değilse uyar/çık)."""
    with wave.open(str(path), "rb") as w:
        ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if width != 2:
        sys.exit(f"Hata: {path.name} 16-bit PCM değil (sampwidth={width}). 16 kHz mono 16-bit kaydet.")
    data = np.frombuffer(raw, dtype=np.int16)
    if ch > 1:
        data = data[::ch]  # kaba mono indirgeme (ilk kanal)
    if rate != _SAMPLERATE:
        print(f"UYARI: örnekleme {rate} Hz, openWakeWord {_SAMPLERATE} Hz bekler — skor sapabilir.")
    return data


def _peak_scores(model, audio: np.ndarray) -> dict:
    """Ses'i 80 ms (_FRAME) karelere böl; her anahtar için tepe (max) skoru topla."""
    peaks: dict[str, float] = {}
    for i in range(0, len(audio) - _FRAME + 1, _FRAME):
        for key, val in model.predict(audio[i:i + _FRAME]).items():
            peaks[key] = max(peaks.get(key, 0.0), float(val))
    return peaks


def main() -> None:
    ap = argparse.ArgumentParser(description="openWakeWord modelini doğrula (yükleme + smoke + opsiyonel wav skoru).")
    ap.add_argument("--model", help="Model yolu ya da yerleşik ad (vermezsen config.local.yaml → wake.model_path).")
    ap.add_argument("--framework", choices=["onnx", "tflite"], help="Çıkarım motoru (dosya tipiyle eşleşmeli).")
    ap.add_argument("--wav", help="Test sesi (16 kHz mono 16-bit) — tepe wake skorunu yazar.")
    args = ap.parse_args()

    cfg = _build_cfg(args.model, args.framework)
    wake = cfg["wake"]
    threshold = float(wake.get("threshold", 0.5))
    print(f"Model     : {wake.get('model_path') or '(openWakeWord yerleşik kelimeler)'}")
    print(f"Framework : {wake.get('framework', 'onnx')}")
    print(f"Eşik      : {threshold}")

    try:
        model = WakeListener(cfg)._model
    except Exception as exc:  # noqa: BLE001 — yükleme hatasını okunur mesaja çevir
        sys.exit(
            f"\nMODEL YÜKLENEMEDİ: {exc}\n"
            "→ .onnx yolu doğru mu? framework dosya tipiyle eşleşiyor mu? `make models` çalıştı mı?"
        )
    keys = list(model.models.keys())
    print(f"Yüklendi  : {keys}\n")

    if args.wav:
        peaks = _peak_scores(model, _read_wav_int16(Path(args.wav)))
        print(f"'{Path(args.wav).name}' boyunca TEPE skorlar:")
        for key in keys:
            score = peaks.get(key, 0.0)
            verdict = "TETİKLER ✓" if score >= threshold else "tetiklemez ✗"
            print(f"  {key:24s} {score:.3f}   (eşik {threshold} → {verdict})")
        print("\nİpucu: 'hey arda' klibinde tepe eşiğin belirgin üstünde, sessizlik/alakasız konuşmada altında olmalı.")
        return

    # Smoke: çökmeden çıkarım yapabiliyor mu? (sessizlik + gürültü → skorlar düşük olmalı)
    silence = np.zeros(_SAMPLERATE, dtype=np.int16)
    noise = np.random.default_rng(0).normal(0, 2000, _SAMPLERATE).astype(np.int16)
    s_peak = _peak_scores(model, silence)
    if hasattr(model, "reset"):
        model.reset()  # iki klip arası akış durumunu sıfırla
    n_peak = _peak_scores(model, noise)
    print("Smoke (çıkarım çalışıyor mu?):")
    for key in keys:
        print(f"  {key:24s} sessizlik {s_peak.get(key, 0.0):.3f}   gürültü {n_peak.get(key, 0.0):.3f}")
    print("\n✓ Model yüklendi ve çıkarım çökmedi. Gerçek tetiği ölçmek için:  make check-wake WAV=klip.wav")


if __name__ == "__main__":
    main()
