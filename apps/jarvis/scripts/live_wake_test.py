"""Canlı wake word testi — Whisper STT ile ne duyduğunu gösterir.

Kullanım (kendi terminalinde):
  .venv/bin/python scripts/live_wake_test.py

Her 2 saniyede bir kaydeder ve ne transcribe ettiğini yazar.
"jarvis" geçince TETİKLEDİ yazar. Ctrl+C ile çık.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import sounddevice as sd
import mlx_whisper

SR = 16000
WINDOW = 2.0   # saniye
STEP = 0.5     # saniye

def main():
    from arda.config import load_config
    cfg = load_config()
    wake = cfg.get("wake", {})
    phrase = wake.get("phrase", "jarvis").lower()
    model = wake.get("whisper_model") or cfg.get("stt", {}).get("model", "mlx-community/whisper-small-mlx")
    language = cfg.get("stt", {}).get("language", "tr")

    print(f"Model: {model}  |  Dil: {language}  |  Phrase: '{phrase}'")
    print("Konuş — her 0.5 sn'de transcribe edilir. Ctrl+C ile çık.\n")

    window_samples = int(WINDOW * SR)
    step_samples = int(STEP * SR)
    buf = np.zeros(0, dtype=np.int16)
    step_acc = 0

    with sd.InputStream(samplerate=SR, channels=1, dtype="int16", blocksize=1280) as stream:
        while True:
            frame, _ = stream.read(1280)
            chunk = np.asarray(frame).flatten()
            buf = np.concatenate([buf, chunk])
            if len(buf) > window_samples:
                buf = buf[-window_samples:]
            step_acc += len(chunk)
            if step_acc < step_samples:
                continue
            step_acc = 0
            if len(buf) < step_samples * 2:
                continue

            rms = np.sqrt(np.mean(buf.astype(np.float32) ** 2))
            audio_f32 = buf.astype(np.float32) / 32768.0
            result = mlx_whisper.transcribe(
                audio_f32,
                path_or_hf_repo=model,
                language=language,
                condition_on_previous_text=False,
            )
            text = result.get("text", "").strip()
            marker = " ← TETİKLEDİ! 🎯" if phrase in text.lower() else ""
            print(f"  RMS:{rms:5.0f}  {repr(text)}{marker}", flush=True)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBitti.")
