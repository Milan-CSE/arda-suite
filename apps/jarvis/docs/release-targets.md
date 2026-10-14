# Release hedefleri

Jarvis iki farklı çalışma ortamı için iki farklı dağıtım biçiminde hazırlanır.

| Hedef | Kullanıcı deneyimi | Dağıtım biçimi | Başlatma |
|---|---|---|---|
| macOS | Menü çubuğunda görünen masaüstü uygulaması | `Jarvis.app` veya `.dmg` | Finder/Launchpad |
| Raspberry Pi/Linux | GUI’siz headless sesli daemon | Repo + `.venv` + systemd user service | `systemctl --user` |

## macOS çıktısı

```bash
make app       # dist/Jarvis.app
make dmg       # dist/Jarvis.dmg
make release-macos  # test + compile sonrası ikisini üretir ve doğrular
```

Mevcut macOS çıktısı `py2app` alias build’idir: bu geliştirme Mac’inin repo ve
`.venv` yollarına bağlıdır. Kişisel bu Mac’te Finder/Launchpad kullanımı için
uygundur; başka Mac’e taşınabilir bağımsız paket değildir. Taşınabilir standalone
build ayrı bir release fazıdır çünkü MLX/ONNX/STT modelleri ve Ollama runtime’ı
uygulama bundle’ının dışında yönetilir.

Kontrollü standalone denemesi mevcut py2app/modulegraph kombinasyonunda MLX ve
opsiyonel backend import grafiğini tararken `RecursionError` ile durdu. Bu nedenle
standalone paket şu an yayınlanmış bir hedef değildir; alias release bilinçli olarak
tek geliştirme Mac’i için kullanılmaktadır.

Bu hedefte menubar, macOS mikrofon izinleri, AppleScript ve `say` TTS
özellikleri kullanılabilir.

## Raspberry Pi çıktısı

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-rpi.txt
make rpi-check
make install-rpi-service
```

Pi’de `.app`/menubar paketi üretilmez. Servis `daemon` modunda çalışır; Vosk
wake-word ve `espeak-ng` ile yerel ses akışı kullanır. Yönetim komutları:

```bash
make rpi-status
make rpi-logs
make rpi-restart
```

## Ortak çekirdek

İki hedef aynı Python çekirdeğini, config yapısını, Ollama beynini, agent
izinlerini ve test suite’ini paylaşır. Platforma özel katmanlar yalnızca UI,
STT/TTS ve servis başlatma tarafında ayrılır.
