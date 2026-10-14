"""macOS sistem izinleri denetleyicisi — Erişilebilirlik (Accessibility) ve Mikrofon.

Jarvis'in iki kritik izne ihtiyacı vardır:
1. Mikrofon (Microphone): Ses kaydı ve VAD için.
2. Erişilebilirlik (Accessibility): Global kısayollar (⌘⇧A, ⌘⇧P, ⌘⇧D) ve UI Otomasyonu için.

Yeni macOS sürümlerinde (Sequoia, Sonoma) izinler sıfırlanabilir veya uygulama taşındığında
kaybolabilir. Bu modül izin durumunu kontrol eder ve kullanıcıya anlaşılır rehber sunar.
"""
from __future__ import annotations

import ctypes
from ctypes import util
import logging
import platform
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def is_macos() -> bool:
    """İşletim sistemi macOS mi?"""
    return platform.system() == "Darwin"


def is_accessibility_granted(*, prompt: bool = False) -> bool:
    """macOS Accessibility (Erişilebilirlik) izninin verilip verilmediğini denetle.

    Args:
        prompt: True ise ve izin verilmemişse macOS yerel sistem izin istemi iletişim kutusunu tetikler.
    """
    if not is_macos():
        return True

    try:
        app_services_path = util.find_library("ApplicationServices")
        if not app_services_path:
            return False
        app_services = ctypes.cdll.LoadLibrary(app_services_path)

        if prompt:
            # AXIsProcessTrustedWithOptions kAXTrustedCheckOptionPrompt: True
            # CoreFoundation dictionary yapısıyla çağırmak yerine yerel osascript ile güvenle prompt tetikle:
            res = subprocess.run(
                ["osascript", "-e", 'tell application "System Events" to get name of first process'],
                capture_output=True,
                check=False,
            )
            return res.returncode == 0

        ax_trusted = getattr(app_services, "AXIsProcessTrusted", None)
        if ax_trusted:
            ax_trusted.restype = ctypes.c_bool
            return bool(ax_trusted())
        return False
    except Exception as exc:  # noqa: BLE001
        log.debug("Accessibility denetimi hatası: %s", exc)
        return False


def is_microphone_available() -> bool:
    """Mikrofon giriş cihazının mevcut ve erişilebilir olup olmadığını denetle."""
    try:
        import sounddevice as sd
        device = sd.query_devices(kind="input")
        return device is not None and bool(device.get("max_input_channels", 0) > 0)
    except Exception as exc:  # noqa: BLE001
        log.debug("Mikrofon denetimi hatası: %s", exc)
        return False


def open_accessibility_settings() -> None:
    """Sistem Ayarları > Gizlilik ve Güvenlik > Erişilebilirlik panelini aç."""
    if is_macos():
        subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"], check=False)


def open_microphone_settings() -> None:
    """Sistem Ayarları > Gizlilik ve Güvenlik > Mikrofon panelini aç."""
    if is_macos():
        subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"], check=False)


def check_all_permissions(*, prompt: bool = False) -> dict[str, bool]:
    """Tüm gerekli sistem izinlerini topluca sorgula."""
    return {
        "accessibility": is_accessibility_granted(prompt=prompt),
        "microphone": is_microphone_available(),
    }


def permission_report() -> str:
    """Kullanıcıya gösterilebilecek metinsel izin durumu ve rehberi."""
    status = check_all_permissions()
    if not is_macos():
        return "\n".join([
            f"{platform.system()} İzin Durumu:",
            f"  - Mikrofon: {'✅ Erişilebilir' if status['microphone'] else '❌ Erişilemiyor'}",
            "  - Erişilebilirlik: ✅ Gerekli değil (menubar/UI otomasyonu kapalı)",
            "  - Not: systemd daemon için ses cihazı ve kullanıcı oturumu erişimini kontrol edin.",
        ])
    lines = ["macOS İzin Durumu:"]
    lines.append(f"  - Mikrofon: {'✅ Verildi' if status['microphone'] else '❌ Erişilemiyor'}")
    lines.append(f"  - Erişilebilirlik: {'✅ Verildi' if status['accessibility'] else '⚠️ İzin Gerekli (Kısayollar için)'}")

    if not status["accessibility"]:
        lines.append("\nErişilebilirlik izni vermek için:")
        lines.append("  1. Sistem Ayarları > Gizlilik ve Güvenlik > Erişilebilirlik bölümünü açın.")
        lines.append("  2. 'Jarvis' (veya Terminal / VS Code / iTerm) uygulamasını açıp izin verin.")
    if not status["microphone"]:
        lines.append("\nMikrofon izni vermek için:")
        lines.append("  1. Sistem Ayarları > Gizlilik ve Güvenlik > Mikrofon bölümünü açın.")
        lines.append("  2. 'Jarvis' (veya Terminal) yanındaki anahtarı açın.")

    return "\n".join(lines)
