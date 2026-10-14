# models/ — özel wake-word modelleri

openWakeWord ile eğittiğin tetik-kelime modelleri (`.onnx` / `.tflite`) buraya gelir.

- Önerilen ad: `hey_arda.onnx`
- Bağlamak için `config.local.yaml`:
  ```yaml
  app:
    wake_enabled: true
  wake:
    model_path: ~/arda-suite/apps/jarvis/models/hey_arda.onnx
    framework: onnx        # .tflite eğittiysen: tflite
  ```
- Eğitim reçetesi (adım adım): [`../docs/wake-word-training.md`](../docs/wake-word-training.md)
- Doğrulama / eşik ayarı: `make check-wake`  (veya `make check-wake WAV=klip.wav`)

Model dosyaları büyük binary ve makineye/sürüme özeldir. Git kullanmaya başlarsan
`models/*.onnx` ve `models/*.tflite`'ı `.gitignore`'a ekle; yalnızca bu README izlensin.
