# Kendi wake-word'ünü eğit — "Hey Arda" (openWakeWord)

Varsayılan akış push-to-talk'tır ve wake word **gerekmez**. Her-zaman-dinle istiyorsan
iki yol var:

1. **Sıfır eğitim:** openWakeWord'ün yerleşik `hey jarvis` modeli (`wake.model_path: hey_jarvis`).
   "**hey jarvis**" dersin, uyanır. Hızlı ama tetik kelimesi sabit.
2. **Kendi tetiğin ("Hey Arda"):** bu dokümandaki reçeteyle kendi `.onnx`'ini eğit.

## Neden "Hey Arda", neden tek başına "Arda" değil

"Arda" iki heceli ve kısa. Kısa wake word'ler false-trigger canavarıdır: mikrofon
gün boyu rastgele tetiklenir, model de az ayırt edici sinyalle eğitildiği için
hassasiyet/özgüllük dengesi bozulur. **İki kelimelik "hey arda"** hem eğitimi
kolaylaştırır hem günlük kullanımda temiz kalır — ve asistanın adı Arda olduğu için
ürün hissi de yerinde olur.

## Eğitim (Google Colab, ücretsiz GPU — API key yok)

openWakeWord, sentetik TTS ile binlerce pozitif örnek üretip (piper-sample-generator)
gürültü/yankı ile artırarak küçük bir sınıflandırıcı eğitir. Picovoice gibi API
anahtarı/lisans gerekmez.

1. openWakeWord deposunu aç: <https://github.com/dscripka/openWakeWord> →
   `notebooks/automatic_model_training.ipynb` (README'deki **Open in Colab** rozetiyle aç).
   Çalışma zamanı: **GPU**.
2. Yapılandırma hücresinde:
   - `target_word` / `target_phrase` → **`"hey arda"`**.
     İstersen TTS aksan dağılımını kapsamak için telaffuz varyantları ekle:
     `["hey arda", "hey ardha", "hey aarda"]`.
   - `model_name` → **`hey_arda`** (çıktı dosyası bu adı alır).
   - Pozitif örnek sayısını cömert tut (kısa ifade → daha çok veri gerekir):
     notebook'un önerdiği varsayılanın üstüne çık (ör. ~30–50k pozitif), negatif/
     doğrulama setini de defaultta bırak.
3. Eğitimi çalıştır; **recall vs. false-accept** eğrisine bak. Tepe recall'da
   false-accept hâlâ düşükse model iyi.
4. **ONNX'e export** et (notebook'ta export hücresi var; ONNX seç). `hey_arda.onnx`'i indir.
   - `.tflite` indirsen de olur — o zaman config'te `framework: tflite` ver.

> Notebook sürümleri zamanla değişir; hücre adları/parametreler birebir tutmayabilir.
> Mantık sabit: hedef ifade = "hey arda", model adı = hey_arda, çıktı = .onnx.

## Bu repoya bağlama

1. `hey_arda.onnx`'i **`models/`** klasörüne koy → `models/hey_arda.onnx`.
2. Ön-işlem modelleri (melspectrogram + embedding) inmiş olmalı:
   ```bash
   make models      # openWakeWord yardımcı modellerini de indirir
   ```
3. `config.local.yaml`:
   ```yaml
   app:
     wake_enabled: true
   wake:
     model_path: ~/arda-suite/apps/jarvis/models/hey_arda.onnx
     framework: onnx        # .tflite eğittiysen: tflite
     threshold: 0.5
   ```
   > Mutlak yol kullan (vault.path gibi). `.app` paketi farklı çalışma dizininden
   > başladığı için göreli yol kırılır.

## Doğrula ve eşiği ayarla (mikrofonsuz)

```bash
make check-wake                       # modeli yükle + çıkarım çökmüyor mu (smoke)
make check-wake WAV=~/Desktop/clip.wav   # bir kayıttaki TEPE wake skorunu ölç
```

- `clip.wav`'i **16 kHz mono 16-bit** kaydet (içinde sen "hey arda" diyorsun).
- Hedef: "hey arda" klibinde tepe skor `threshold`'un **belirgin üstünde**
  (ör. tepe 0.7 → threshold 0.5), sessizlik/alakasız konuşmada **altında**.
- Hâlâ rastgele tetikliyorsa `threshold`'u 0.6–0.7'ye çıkar; seni duymuyorsa 0.4'e
  indir ve/veya daha çok pozitif örnek + telaffuz varyantıyla yeniden eğit.

## Çalıştır

```bash
make run          # wake_enabled:true ise mic açılır, "hey arda" ile uyanır
```

İlk uyanışta openWakeWord tembel yüklenir (~1 sn gecikme), sonrası anında.
