"""Ollama sunucusunu açılışta garantiye al — kapalıysa otomatik başlat.

Jarvis'in beyni (classify/answer/synthesize) LLM için Ollama'ya (config ``brain.host``)
bağlanır. Ollama kapalıysa beyin cevap veremez — kullanıcı için "hey jarvis / Efendim? /
… / cevap yok (tık yok)" olarak görünür. Kullanıcının Ollama'yı elle açmayı hatırlaması
gerekmesin diye: açılışta erişilebilir mi diye bak, değilse başlat ve hazır olana dek bekle.

Best-effort: başlatılamazsa turu engellemez, yalnızca loglar (beyin kendi hata yoluna düşer).
Kapatmak için: config ``brain.autostart: false``.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import time
import urllib.request

from arda.platform_runtime import ollama_launch_command, system

log = logging.getLogger(__name__)

_DEFAULT_HOST = "http://localhost:11434"


def _host(cfg: dict) -> str:
    return (cfg.get("brain", {}).get("host") or _DEFAULT_HOST).rstrip("/")


def is_up(host: str, timeout: float = 2.0) -> bool:
    """Ollama HTTP ile ulaşılabiliyor mu (/api/version 200)."""
    try:
        with urllib.request.urlopen(f"{host}/api/version", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001 — bağlantı reddi/timeout → kapalı say
        return False


def _launch() -> bool:
    """Ollama'yı başlat: önce Ollama.app (kalıcı, menübar), yoksa 'ollama serve' binary.

    'open -a Ollama' → macOS uygulamasını açar; sunucu Jarvis'ten bağımsız yaşar.
    Binary fallback: detached süreç (start_new_session → Jarvis kapansa da sürebilir)."""
    command = ollama_launch_command()
    if command is None:
        return False
    if system() == "Darwin":
        try:
            subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        except Exception:  # noqa: BLE001
            # Fall through to the binary on macOS if the app is unavailable.
            exe = shutil.which("ollama")
            if not exe:
                return False
            command = [exe, "serve"]
    try:
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception:  # noqa: BLE001
        return False


def ensure_ollama(cfg: dict, *, wait_sec: float = 20.0) -> bool:
    """Ollama'yı garantiye al. Zaten açıksa hızlı döner; kapalıysa başlatıp bekler."""
    host = _host(cfg)
    if not cfg.get("brain", {}).get("autostart", True):
        return is_up(host)
    if is_up(host):
        return True

    log.warning("Ollama erişilemiyor (%s) → otomatik başlatılıyor…", host)
    if not _launch():
        log.error("Ollama başlatılamadı: ne Ollama.app ne 'ollama' binary bulundu. Elle aç.")
        return False

    deadline = time.monotonic() + wait_sec
    while time.monotonic() < deadline:
        if is_up(host):
            log.info("Ollama hazır (%s).", host)
            return True
        time.sleep(0.5)
    log.error("Ollama %.0f sn içinde hazır olmadı — beyin ilk turda cevapsız kalabilir.", wait_sec)
    return False
