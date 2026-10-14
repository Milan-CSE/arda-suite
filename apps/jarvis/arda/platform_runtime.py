"""Small platform command adapters shared by the runtime and agent tools.

The core agent remains platform-neutral; OS-specific commands live here so a
headless Raspberry Pi can use the daemon path without pretending to support
macOS-only UI automation.
"""
from __future__ import annotations

import platform
import re
import shutil


def system() -> str:
    return platform.system()


def open_url_command(url: str) -> list[str] | None:
    if system() == "Darwin":
        return ["open", url]
    if system() == "Linux" and shutil.which("xdg-open"):
        return ["xdg-open", url]
    return None


def open_app_command(name: str) -> list[str] | None:
    if system() == "Darwin":
        return ["open", "-a", name]
    # There is no portable application-bundle equivalent on Raspberry Pi.
    # Keep this fail-closed instead of guessing a desktop launcher command.
    return None


def tts_command(text: str, voice: str) -> list[str] | None:
    if system() == "Darwin":
        return ["say", "-v", voice, text]
    if system() == "Linux":
        binary = shutil.which("espeak-ng") or shutil.which("espeak")
        if binary:
            # macOS voice names (e.g. Yelda) are not portable; use the
            # configured name only when it is a simple espeak voice id.
            safe_voice = voice if re.fullmatch(r"[a-z]{2,3}(?:[-_][A-Z]{2})?", voice or "") else "en"
            return [binary, "-v", safe_voice, text]
    return None


def tts_render_command(text: str, voice: str, output_path: str, sample_rate: int = 22050) -> list[str] | None:
    """Return a command that renders speech to a WAV file on this platform."""
    if system() == "Darwin":
        return [
            "say", "-v", voice, "--file-format=WAVE",
            f"--data-format=LEI16@{sample_rate}", "-o", output_path, text,
        ]
    if system() == "Linux":
        binary = shutil.which("espeak-ng") or shutil.which("espeak")
        if binary:
            safe_voice = voice if re.fullmatch(r"[a-z]{2,3}(?:[-_][A-Z]{2})?", voice or "") else "en"
            return [binary, "-v", safe_voice, "-w", output_path, text]
    return None


def ollama_launch_command() -> list[str] | None:
    if system() == "Darwin":
        return ["open", "-a", "Ollama"]
    binary = shutil.which("ollama")
    return [binary, "serve"] if binary else None


def runtime_report() -> str:
    """Return a dependency-light report suitable for ``make doctor``."""
    lines = [f"Platform: {system()}"]
    lines.append(f"  - URL açma: {'✅' if open_url_command('https://example.com') else '⚠️ yok'}")
    lines.append(f"  - TTS: {'✅' if tts_command('test', 'tr') else '⚠️ espeak/say yok'}")
    lines.append(f"  - Ollama: {'✅ binary/app bulundu' if ollama_launch_command() else '⚠️ bulunamadı'}")
    lines.append(
        "  - RPi önerisi: stt.backend=vosk ve app.mode=daemon"
        if system() == "Linux" else
        "  - STT: MLX backend'leri bu platform için etkin"
    )
    return "\n".join(lines)
