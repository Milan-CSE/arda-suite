"""Mikrofon ses seviyesi testi — izin ve ses var mı diye kontrol eder."""
import sys
import numpy as np
import sounddevice as sd

SAMPLERATE = 16000
FRAME = 1280

print("3 saniye mikrofon dinleniyor... Konuş!\n")
try:
    with sd.InputStream(samplerate=SAMPLERATE, channels=1, dtype="int16", blocksize=FRAME) as stream:
        for i in range(int(3 * SAMPLERATE / FRAME)):
            frame, _ = stream.read(FRAME)
            rms = np.sqrt(np.mean(np.asarray(frame, dtype=np.float32) ** 2))
            bar = "█" * int(rms / 200)
            print(f"  RMS: {rms:6.0f}  {bar}")
except Exception as e:
    print(f"\nHATA: {e}")
    print("→ Sistem Tercihleri → Gizlilik → Mikrofon → Terminal'e izin ver")
    sys.exit(1)

print("\nMikrofon çalışıyor.")
