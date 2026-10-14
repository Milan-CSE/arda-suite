"""Tek kaynak (single source of truth) — kimlik + tekrar eden varsayılan değerler.

Asistan adı, uyandırma kelimesi, sesli onaylar, model adları gibi kod genelinde
TEKRARLAYAN sabitler BURADA tanımlanır. Modüller literal yazmak yerine bu sabitleri
import eder → bir yeri değiştirince HER YER değişir (DRY). Örnek: asistanın adını
değiştirmek için yalnızca ``ASSISTANT_NAME``'i düzenle; promptlar, menü çubuğu,
bildirimler ve onay dialog'u otomatik güncellenir.

Katman ayrımı:
- Bu dosyadaki değerler KOD VARSAYILANLARIDIR (fallback). Çalışma zamanında
  ``config.yaml`` / ``config.local.yaml`` bunların ÇOĞUNU ezebilir
  (model, ses, wake.phrase, wake_ack, stop_phrases…). Yani "uyandırma kelimesini
  kalıcı değiştir" = config.local.yaml ``wake.phrase``; "varsayılanı/ kimliği
  değiştir" = buradaki sabit.
- ``ASSISTANT_NAME`` config'te YOKTUR (promptlara/UI'ya gömülü kimliktir) → onu
  değiştirmenin tek yeri burasıdır.

Bağımlılık yok (yalnızca stdlib) → her modülden güvenle import edilebilir,
döngüsel import riski yoktur.
"""
from __future__ import annotations

# --- Kimlik ---
# Kullanıcıya görünen ad. Promptlar, menü çubuğu başlığı, bildirimler ve
# onay dialog'u bunu kullanır. Tek değiştirme noktası.
ASSISTANT_NAME = "Jarvis"

# Uyandırma kelimesi (kod varsayılanı). Tek kelime daha güvenilir tetiklenir.
# Varsayılan olarak asistan adından türetilir; istenirse bağımsız verilebilir
# (ör. ad "Jarvis" ama wake "computer"). Çalışma zamanı ezme: config wake.phrase.
WAKE_PHRASE = ASSISTANT_NAME.lower()

# --- Sesli geri bildirim ---
WAKE_ACK = "Efendim?"          # tetik (⌘⇧A / wake) sonrası sesli yanıt
GOODBYE = "Görüşürüz."         # sohbet oturumu bitince söylenen kısa onay
TTS_VOICE = "Yelda"            # macOS Türkçe sesi (say -v <voice>)

# Menü çubuğu "Ses" alt menüsündeki seçenekler (ilki varsayılan).
TTS_VOICE_OPTIONS = (TTS_VOICE, "Samantha", "Daniel", "Alex", "Karen")

# --- Oturumu bitiren ifadeler (kod varsayılanı; config app.stop_phrases ezebilir) ---
STOP_PHRASES = (
    "kapat kendini",
    "kendini kapat",
    "konuşmayı bitir",
    "görüşürüz",
)

# --- Modeller / LLM varsayılanları (config.brain / config.stt ezebilir) ---
STT_MODEL = "mlx-community/whisper-large-v3-turbo"   # mlx-whisper STT modeli (whisper backend)
# NVIDIA Parakeet (parakeet-mlx) STT/wake modeli — Apache 2.0, çok dilli (İngilizce; Türkçe YOK).
# config.stt.parakeet_model / config.wake.parakeet_model ezebilir.
PARAKEET_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"
LLM_MODEL = "qwen3:8b"                                # Ollama beyin modeli
LLM_KEEP_ALIVE = "10m"                               # modeli sıcak tut (ilk istek hızlı)
ANSWER_TEMPERATURE = 0.7                              # serbest sohbet/sentez sıcaklığı (router 0.1)
