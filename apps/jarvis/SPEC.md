# Arda — Teknik Spesifikasyon (V1)

**Kaynak:** `Arda-Asistan-Proje-Plani.md` (v1.0) + tasarım incelemesi (16 Haz 2026, onaylı).
**Durum:** Faz 1 çekirdek tamam (TDD) + Faz 2/3 kuruldu — her-zaman-dinleyen "Arda" wake word (openWakeWord) ve menü çubuğu/daemon app'i. 78 test yeşil.
**Platform:** macOS 26.5.1 · Apple M5 · 24 GB · Python 3.11+ (yerelde 3.14).

> **Güncel durum (bu spec'ten sonra evrildi — kurulum/çalışan yapı için `README.md` esastır):**
> kullanıcıya görünen ad **Jarvis**; wake word artık 4 backend (`oww` / `whisper` / `vosk` /
> `parakeet`), STT varsayılanı **parakeet** (Türkçe için `whisper`); çok-adımlı **desktop
> agent** + macOS **UI-otomasyon** araçları eklendi. Aşağısı tarihsel karar günlüğüdür
> (yukarıdaki "Durum"/"78 test"/"Arda wake" satırları yazıldığı ana aittir, güncel değildir).

> Bu belge plandan **bilinçli sapan** kararları ve gerekçelerini kayıt altına alır. Plandaki genel
> vizyon, fazlar, veri modeli ve niyet tasarımı geçerliliğini korur; burada yalnızca farklar ve
> netleştirmeler işlenir.

---

## 1. Karar Günlüğü (plandan sapmalar — onaylı)

| # | Karar | Plandaki hâli | Yeni hâli | Gerekçe |
|---|-------|---------------|-----------|---------|
| D1 | **Paket yapısı** | Düz `.py` dosyaları (kök dizinde) | `arda/` Python paketi + `tests/` | Düz dosyalar test edilemezdi; paket içe-aktarma + mock sınırları sağlar |
| D2 | **Asla veri kaybetme** | Bozuk JSON → "İşlenmemiş" klasörü | Her hata yolunda ham metin → `vault/Inbox/` | Projenin en kritik güvenilirlik ilkesi: STT boş, JSON bozuk, modül çökse bile konuşma **asla** kaybolmaz |
| D3 | **STT runtime** | `whisper.cpp` subprocess (temp WAV + stdout parse) | **`mlx-whisper` in-process**, model bir kez yüklenir ve sıcak tutulur | M5'te ~2× hızlı; ağırlıklar aynı → doğruluk birebir; her çağrıda ~1.6 GB reload bedeli yok; derleme yok; API daha temiz. (Taşınabilirlik tek avantajıydı, proje Apple-Silicon-only) |
| D4 | **LLM çıktı sözleşmesi** | "Yalnızca geçerli JSON" promptu + try/except | **Ollama structured output — JSON schema** ile `Intent`'in tam şekli zorlanır | "Alan eksik" hata modunu kökten kapatır; retry/Inbox'a düşmeden doğru çıktı |
| D5 | **Test felsefesi** | %80 kapsam (genel) | Saf mantıkta %80 hedef; **ham ses/donanım I/O kapsam dışı (sınırda mock)** | Mikrofon/STT'de %80 kovalamak sahte güvence olurdu; değer saf mantıkta (parse, format, routing, slug) |
| D6 | **`pipeline.process_once` dönüşü** | (belirsiz) | `None` yerine küçük **`ProcessResult` nesnesi** | Testte sonucu assert edebilmek + ne olduğunu loglamak için |
| D7 | **Wake word motoru** | Porcupine (Faz 2) | **openWakeWord** (yerel, ücretsiz, API key yok) | Picovoice access key + console'dan özel .ppn üretme zorunluluğu yok; tamamen yerelde çalışır. "Arda" için özel `.onnx` modeli `wake.model_path` ile takılır; model verilmezse yerleşik kelimelerle test edilir |
| D8 | **Çalışma biçimi** | Faz 1 Enter/tuş ile tetik | **Her zaman dinleyen ajan** (`agent.run_forever`) + `app.mode` ile `menubar` (rumps) / `daemon` (launchd) seçimi | Hedef "arkada sürekli dinleyen, 'Arda' deyince uyanan" macOS app'i; döngü hatadan izole (tur hatası daemon'u öldürmez, backoff'lu), modeller açılışta ısıtılır |

### Netleştirmeler (değişmeyen ama sabitlenen kararlar)
- **STT modeli:** `mlx-community/whisper-large-v3-turbo`, `language="tr"`. Tam `large-v3`'e gerek yok; turbo Türkçe + teknik terimde yeterli ve M5'te hızlı.
- **TTS:** macOS `say -v Yelda` (Türkçe voice). Config'ten değiştirilebilir.
- **Ollama:** `think: false` (qwen3 düşünme modu kapalı — hızlı yapılandırılmış çıktı için). `keep_alive` ile model sıcak tutulur (ilk gerçek istek yavaş olmasın).
- **Brain modeli:** `qwen3:8b` (24 GB için V1 önerisi; whisper + OS'e yer kalsın). 30B-A3B MoE V1'de değil.

### Çözülen açık uçlar (uygulandı)
- **R1 — Modül imzaları** (§6): ✅ uygulandı ve testlerle sabitlendi (aşağıda güncel imzalar).
- **R2 — Config split** (§7): ✅ uygulandı; `arda/config.py` `load_config()` birleştirir + doğrular (`vault.path` zorunlu).

### Gelecek (V1'de yapma, not düş)
- **VAD Plan B:** Enerji-tabanlı + gürültü tabanı kalibrasyonuyla başla. Eşik kayması yaşanırsa → **Silero VAD (ONNX, derleme yok)** veya whisper.cpp dahili VAD'ine geç. V1'de kurma.
- **Lessons Loop (hata→ders→tekrarlama):** Spec `~/arda-suite/services/memory/docs/lessons-loop-spec.md`'de yaşıyor. Jarvis tüketicidir; mem0 `type:lesson` kayıtlarını okur/yazar. Detay orada.

---

## 2. Mimari Akış (V1)

```
"Arda" (wake: openWakeWord) → ajan uyanır + sesli onay (app.wake_ack)
Mikrofon → [listen: kayıt + enerji-VAD ile dur, max_sec güvenlik kapağı] → WAV
        → [transcribe: mlx-whisper (sıcak model)] → ham metin
        → [brain: Ollama + JSON schema] → Intent{intent,category,title,clean_text,tags,follow_up}
        → [pipeline: intent'e göre yönlendir]
              ├─ note     → notes.write_note()   → vault/<kategori>/<slug>.md  (çakışmada -2,-3)
              ├─ task     → notes.append_task()  → vault/Tasks.md
              ├─ answered → doğrudan cevap (clean_text seslendirilir)
              └─ diğer (research/bilinmeyen) → notes.save_to_inbox() → vault/Inbox/
        → [speak: say -v Yelda] → sesli onay (hata verirse yutulur, kaydı bozmaz)
        → ProcessResult döner → ajan yeniden "Arda" bekler
   (HERHANGİ bir adım hata verirse → notes.save_to_inbox(ham metin) → vault/Inbox/)
```

> **Güncel:** "Arda" wake word **aktif** (openWakeWord, Faz 2). Ajan sürekli dinler; "Arda" deyince
> uyanır. `app.mode` ile menü çubuğu (rumps) veya görünmez daemon (launchd) olarak çalışır.
> ("Arda" için özel wake modeli verilene kadar yerleşik kelimelerle test edilir — bkz. README.)

---

## 3. Teknoloji Yığını (V1, güncel)

| Katman | Seçim |
|--------|-------|
| Dil | Python 3.11+ |
| Wake word | **`openWakeWord`** (yerel, ücretsiz; "Arda" için özel `.onnx`, yoksa yerleşik kelimeler) |
| Dinleme | `sounddevice` + enerji-tabanlı VAD (saf Python/numpy) + `max_sec` güvenlik kapağı |
| STT | **`mlx-whisper`** (in-process, `whisper-large-v3-turbo`, tr; açılışta `warm()` ile ısıtılır) |
| Beyin | Ollama · `qwen3:8b` · JSON schema · `think:false` · `keep_alive` · `host` config'ten |
| Not | Markdown + YAML frontmatter → Obsidian vault |
| TTS | macOS `say -v Yelda` |
| Test | `pytest` (saf mantık + ajan/wake döngüleri), donanım/model sınırları mock'lanır |
| Arayüz | `rumps` menü çubuğu app'i **veya** launchd daemon (`app.mode` ile) |

---

## 4. Klasör Yapısı

```
apps/jarvis/                      # Jarvis component root in ARDA Suite
├── SPEC.md
├── README.md
├── requirements.txt
├── pyproject.toml                # paket + pytest config
├── config.yaml                   # COMMIT: model, voice, vad, vault iç yapısı
├── config.local.example.yaml     # şablon (kopyala → config.local.yaml)
├── .gitignore                    # config.local.yaml, .venv, vault/ (opsiyonel)
├── Makefile                      # geliştirici komutları (test/run/install/clean)
├── arda/                         # Python paketi
│   ├── __init__.py
│   ├── __main__.py               # giriş: config yükle, deps kur, app.mode'a göre dispatch
│   ├── config.py                 # config.yaml + config.local.yaml yükle/merge/doğrula (ConfigError)
│   ├── models.py                 # Intent, ProcessResult
│   ├── listen.py                 # mic kayıt + enerji-VAD + max_sec kapağı
│   ├── transcribe.py             # mlx-whisper sıcak model + warm()
│   ├── brain.py                  # Ollama structured output (JSON schema, host config'ten)
│   ├── notes.py                  # slugify_tr, frontmatter, write_note (çakışma-güvenli), append_task, save_to_inbox
│   ├── speak.py                  # say -v Yelda
│   ├── pipeline.py               # process_once() -> ProcessResult (asla raise etmez)
│   ├── wake.py                   # openWakeWord "Arda" wake-word dinleyici (WakeListener)
│   ├── agent.py                  # her zaman dinleyen döngü (run_once/run_forever, AgentDeps)
│   ├── menubar.py                # rumps menü çubuğu app'i (ajan arka plan thread'inde)
│   └── research.py               # (Faz 4) Tavily — henüz yok
├── tests/                        # test_models, test_notes, test_pipeline, test_brain,
│   │                             # test_speak, test_transcribe, test_config, test_wake, test_agent
│   └── ...                       # saf mantık + ajan/wake döngüleri; donanım/model mock'lu
├── deploy/
│   └── com.example.jarvis.plist # launchd LaunchAgent şablonu (daemon modu)
└── vault/
    ├── Daily/  Internship/  Research/  Ideas/  Inbox/
    └── Tasks.md
```

---

## 5. Veri Modeli

### 5.1 Not formatı (Obsidian frontmatter) — plandaki gibi
```markdown
---
type: internship
date: 2026-06-16
tags: [staj, ORS, rulman]
---
# <title>
## Ham Not
<orijinal transkript>
## Düzenlenmiş Not
<clean_text>
## Anahtar Kelimeler
#etiket1 #etiket2
## Sonradan Araştırılacaklar
- <follow_up...>
```

### 5.2 Intent (LLM JSON schema ile zorlanır)
```json
{
  "intent":    "note | task | answered | research",
  "category":  "internship | research | idea | daily",
  "title":     "string",
  "clean_text":"string",
  "tags":      ["string"],
  "follow_up": ["string"]
}
```
> `brain.py` Ollama'ya bu şemayı `format=<schema>` olarak verir (enum'lar `brain._SCHEMA` ile birebir).
> Şema dışı/eksik alan **imkânsız**; yine de parse/doğrulama başarısızsa → `vault/Inbox/` (D2).
> Yönlendirme: `note`→not, `task`→Tasks.md, `answered`→sesli cevap, `research`/bilinmeyen→Inbox.
> (`tags`/`follow_up` Intent'te `tuple[str, ...]` olarak saklanır — frozen + içerik değişmez.)

---

## 6. Modül İmzaları  ✅ (R1 — uygulandı; testlerle sabitlendi)

```python
# models.py
@dataclass(frozen=True)
class Intent:
    intent: str; category: str; title: str
    clean_text: str; tags: tuple[str, ...]; follow_up: tuple[str, ...]
    @classmethod
    def from_dict(cls, data: object) -> "Intent": ...  # doğrula; hatalıysa ValueError → Inbox

@dataclass(frozen=True)
class ProcessResult:
    status: str            # "saved" | "task" | "answered" | "inbox" | "failed"
    message: str           # sesli onay metni
    path: Path | None      # yazılan dosya (varsa)
    intent: Intent | None

# listen.py
def record_until_silence(cfg) -> Path: ...          # WAV yolu döner

# transcribe.py
class Transcriber:                                   # model bir kez yüklenir, sıcak kalır
    def __init__(self, cfg): ...
    def transcribe(self, wav: Path) -> str: ...
    def warm(self) -> None: ...                      # açılışta modeli ısıt (best-effort, raise etmez)

# brain.py
def classify(text: str, *, cfg) -> Intent: ...       # ollama.Client(host).chat + JSON schema; başarısızsa raise

# notes.py
def slugify_tr(text: str) -> str: ...                # ç→c ğ→g ı→i ö→o ş→s ü→u, lower, " "→"-"
def build_frontmatter(intent: Intent, date: str) -> str: ...
def render_note(intent: Intent, raw_text: str, date: str) -> str: ...
def write_note(intent: Intent, raw_text: str, *, cfg, date: str) -> Path: ...
def append_task(intent: Intent, *, cfg) -> Path: ...
def save_to_inbox(raw_text: str, *, cfg, date: str) -> Path: ...   # D2 fallback

# speak.py
def say(text: str, *, voice: str = "Yelda") -> None: ...

# pipeline.py
def process_once(wav: Path | None, *, deps: Deps) -> ProcessResult: ...  # tüm zinciri bağlar, asla raise etmez

# wake.py  (openWakeWord — "Arda")
class WakeListener:
    def __init__(self, cfg, *, model=None): ...      # model enjekte edilebilir (test)
    def wait_for_wake(self, *, stream_factory=..., stop=lambda: False) -> bool: ...  # uyanınca True

# agent.py  (her zaman dinleyen döngü)
@dataclass
class AgentDeps:
    wait_for_wake: Callable[[], bool]; record: Callable[[], Path]
    process: Callable[[Path], ProcessResult]; say: Callable[[str], None]
    cleanup: Callable[[Path], None]
def run_once(deps: AgentDeps, *, ack: str) -> ProcessResult | None: ...   # bir tur; kapanışta None
def run_forever(deps: AgentDeps, *, ack: str, stop=lambda: False) -> None: ...  # tur hatası izole + backoff

# menubar.py
def run(deps: AgentDeps, *, ack: str) -> None: ...   # rumps app; ajan daemon thread'inde
```

---

## 7. Config Split  ✅ (R2 — uygulandı; `arda/config.py` birleştirir + doğrular)

```yaml
# config.yaml  (COMMIT — sırsız varsayılanlar)
brain:   { model: "qwen3:8b", host: "http://localhost:11434", think: false, keep_alive: "10m" }
stt:     { model: "mlx-community/whisper-large-v3-turbo", language: "tr" }
tts:     { voice: "Yelda" }
vad:     { silence_sec: 1.2, energy_threshold: null, max_sec: 60.0 }  # null → kalibre; max_sec → güvenlik
app:     { mode: "menubar", wake_ack: "Efendim?", warm_on_start: true }  # mode: menubar | daemon
wake:    { model_path: null, threshold: 0.5, framework: "onnx" }     # null → yerleşik kelimeler
vault:   { categories: { internship: "Internship", research: "Research",
                         idea: "Ideas", daily: "Daily", task: "Tasks.md", inbox: "Inbox" } }
```
```yaml
# config.local.yaml  (GITIGNORE — yola/sırlara özel)
vault:   { path: "$HOME/.../ObsidianVault" }   # zorunlu; yoksa ConfigError
wake:    { model_path: "/.../arda.onnx" }                       # "Arda" özel modeli (opsiyonel)
research:{ tavily_api_key: "tvly-..." }                         # Faz 4
```

---

## 8. Test Stratejisi (D5)

- **Saf mantık (hedef %80+):** `slugify_tr`, `build_frontmatter`, `render_note`, Intent schema
  parse/validate, `pipeline.process_once` yönlendirme + **hata→inbox** yolu (modüller mock'lu).
- **Sınırda mock:** `listen` (mic), `transcribe` (mlx-whisper), `brain` (Ollama HTTP), `speak` (say).
- **Kapsam dışı (kasıtlı):** ham ses yakalama, gerçek STT doğruluğu, gerçek LLM çıktısı — entegrasyon/elle.

**TDD sırası (Faz 1):** `slugify_tr` → `render_note`/frontmatter → Intent schema → `process_once` routing+inbox → (mock'lu) brain → gerçek modüller.

---

## 9. Fazlar (özet — detay planda)

- **Faz 0:** `brew install portaudio`; venv + `requirements.txt`; `ollama pull qwen3:8b`; `say -v Yelda` testi; mic izni. **Kabul:** Ollama'ya soru sorulabiliyor + Python'dan mic okunuyor.
- **Faz 1 (öncelik):** 1.1 listen → 1.2 transcribe → 1.3 brain → 1.4 notes → 1.5 speak → 1.6 pipeline/main. **Kabul:** konuşulan staj notu, biçimli `.md` olarak doğru klasöre düşüyor + "Not kaydedildi" sesli yanıtı.
- **Faz 2 ✅:** "Arda" wake word — **openWakeWord** (Porcupine değil, D7). **Faz 3 ✅:** `rumps` menü çubuğu + launchd daemon (`app.mode`). **Faz 4:** Tavily research + append + Piper. **Faz 5:** proje hafızası.

---

## 10. Riskler (plandaki + yeni)

| Risk | Önlem |
|------|-------|
| LLM bozuk/eksik çıktı | JSON schema (D4) + parse fallback → Inbox (D2) |
| STT ilk çağrı yavaş | mlx-whisper sıcak model (D3) + Ollama `keep_alive` |
| VAD eşik kayması | Kalibrasyon; gerekirse Silero/whisper-VAD'e geç (Plan B) |
| Veri kaybı | Inbox fallback (D2); vault'u git/iCloud ile yedekle |
| Handoff'ta iş kaybı | **Bu spec + kod diske işlenir; save-session bu projeye yazılır** |
```

