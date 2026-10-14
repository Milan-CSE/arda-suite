"""py2app giriş betiği — Jarvis.app çift tıklanınca bunu çalıştırır.

Menü çubuğu app'i olarak başlar (config.app.mode). Asıl mantık `arda` paketinde
(iç ad tarihsel olarak korunur); bu dosya yalnızca bundle'ın giriş noktasıdır.
"""
# EN ÖNCE: GUI açılış ortamını onar (LANG yok → ascii; PATH'te Homebrew yok → ffmpeg yok).
# Ağır importlardan önce çalışmalı; yoksa parakeet config.json ascii'de / ffmpeg'te patlar.
from arda.utf8_boot import ensure_app_environment

ensure_app_environment()

import importlib  # noqa: E402 — ortam onarımından sonra bilinçli

# Paketlenmiş arda paketini runtime'da yükle; py2app modulegraph'ına tüm uygulama
# import grafiğini giriş dosyasından statik olarak taratmak standalone build'i
# gereksiz yere kırılganlaştırıyor.
main = importlib.import_module("arda.__main__").main

if __name__ == "__main__":
    main()
