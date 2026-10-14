# iOS & Siri Shortcuts — Raspberry Pi 5 Shared Brain Entegrasyonu

Raspberry Pi 5'iniz evinizdeki yerel ağda (Wi-Fi) çalışırken, iPhone, iPad veya Apple Watch'unuz üzerinden Siri ile doğrudan Shared Brain'e soru sorabilir veya aklınıza gelen bir bilgiyi sesle kaydettirebilirsiniz.

Bu özellik için herhangi bir ekstra uygulama yüklemenize gerek yoktur; iOS'un yerleşik **Kestirmeler (Shortcuts)** uygulaması kullanılır.

---

## 1. "Hey Siri, Beyne Sor" (Ask Shared Brain)

Bu kestirme sesinizi metne çevirir, Pi üzerindeki Shared Brain'e sorar ve cevabı Siri'nin sesiyle size okur:

1. iPhone'unuzda **Kestirmeler (Shortcuts)** uygulamasını açın ve **+ (Yeni Kestirme)** butonuna dokunun.
2. Adını **"Beyne Sor"** yapın.
3. Şu eylemleri sırayla ekleyin:
   - **Metni Dikte Et (Dictate Text):** Dil: Türkçe.
   - **URL İçeriğini Al (Get Contents of URL):**
     - URL: `http://<RASPBERRY_PI_IP>:8899/api/assistant/chat` (örn: `http://192.168.1.50:8899/api/assistant/chat`)
     - Yöntem: `POST`
     - Üst Bilgiler (Headers): `Accept: text/plain`
     - İstek Gövdesi (Request Body): `JSON`
       - Anahtar: `question` -> Değer: `Dikte Edilen Metin`
       - Anahtar: `plain` -> Değer: `true` (Boole veya metin)
   - **Metni Seslendir (Speak Text):**
     - Metin: `URL'nin İçeriği`
4. **Bitti**'ye dokunun.

🗣️ **Kullanım:**
*"Hey Siri, Beyne Sor"* dediğinizde Siri sizi dinler (örn: *"Python projelerinde veritabanı kuralımız nedir?"*), Pi 5 hafızayı tarar ve cevabı sesli olarak söyler!

---

## 2. "Hey Siri, Bunu Hatırla" (Remember to Shared Brain)

Aklınıza gelen bir fikri, hatayı veya kararı telefonunuzdan sesle anında mem0 hafızasına kaydeder:

1. **Kestirmeler** uygulamasında yeni bir kestirme oluşturun ve adını **"Bunu Hatırla"** yapın.
2. Şu eylemleri ekleyin:
   - **Metni Dikte Et (Dictate Text)**
   - **URL İçeriğini Al (Get Contents of URL):**
     - URL: `http://<RASPBERRY_PI_IP>:8899/api/assistant/remember`
     - Yöntem: `POST`
     - İstek Gövdesi: `JSON`
       - Anahtar: `text` -> Değer: `Dikte Edilen Metin`
       - Anahtar: `scope` -> Değer: `personal`
       - Anahtar: `plain` -> Değer: `true`
   - **Bildirim Göster (Show Notification) veya Metni Seslendir:**
     - `URL'nin İçeriği`
3. **Bitti**'ye dokunun.

🗣️ **Kullanım:**
*"Hey Siri, Bunu Hatırla"* -> *"Docker konteynerlarında Qdrant portu 6333 olarak ayarlandı"* dediğinizde doğrudan merkezi hafızaya eklenir.

---

## 3. Webhook / cURL ile Test Etme

Terminalinizden veya herhangi bir otomasyondan (Home Assistant vb.) doğrudan test edebilirsiniz:

```bash
# Soru sorma (JSON yanıt)
curl -X POST http://<RASPBERRY_PI_IP>:8899/api/assistant/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "Veritabanı kuralımız nedir?"}'

# Soru sorma (Siri için sade düz metin)
curl -X POST http://<RASPBERRY_PI_IP>:8899/api/assistant/chat \
  -H "Content-Type: application/json" \
  -H "Accept: text/plain" \
  -d '{"question": "Veritabanı kuralımız nedir?"}'

# Hafıza ekleme
curl -X POST http://<RASPBERRY_PI_IP>:8899/api/assistant/remember \
  -H "Content-Type: application/json" \
  -d '{"text": "Redis yerine yerel SQLite tercih edildi", "scope": "personal"}'
```
