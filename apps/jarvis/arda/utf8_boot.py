"""GUI/.app/launchd açılış ortamı düzeltmeleri — locale (UTF-8) + PATH (Homebrew).

macOS'ta bir uygulama Launchpad/Finder'dan (ya da launchd'den) açıldığında ortam
FAKİRDİR: ``LANG``/``LC_*`` yoktur ve ``PATH`` yalnızca ``/usr/bin:/bin…`` içerir.
Terminalde (login shell) bunlar dolu olduğu için kod çalışır; yalnızca uygulamadan
açınca patlar. İki ayrı arıza:

1. **Locale → UTF-8**: ``open()`` varsayılan kodlaması ``US-ASCII`` olur.
   ``parakeet_mlx`` model ``config.json``'ını ``encoding`` vermeden açar → UTF-8
   baytında (0xe2 …) ``UnicodeDecodeError``. Çözüm: LC_CTYPE'ı UTF-8'e çek.
2. **PATH → Homebrew**: ``/opt/homebrew/bin`` PATH'te olmaz → ``ffmpeg`` bulunamaz
   (``shutil.which('ffmpeg') is None`` → transkripsiyon boş döner, "cevap yok").
   Çözüm: yaygın Homebrew/araç konumlarını PATH'e ekle.

Re-exec YOK → her başlatma modunda (app/launchd/terminal) çalışır. main() ve app
giriş betiği EN BAŞTA ``ensure_app_environment()`` çağırır; başka şey import edilmeden.
"""
from __future__ import annotations

import locale
import os
import sys

_UTF8_LOCALES = ("en_US.UTF-8", "C.UTF-8", "UTF-8")

# GUI açılışında eksik olan yaygın araç dizinleri (Homebrew arm64/intel + /usr/local).
_EXTRA_PATHS = ("/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin", "/usr/local/sbin")


def ensure_app_environment() -> None:
    """GUI/.app açılış ortamını onar (locale + PATH). Asla raise etmez."""
    ensure_utf8()
    ensure_path()


def ensure_path(extra: tuple[str, ...] = _EXTRA_PATHS) -> None:
    """Var olan Homebrew/araç dizinlerini PATH sonuna ekle (ffmpeg vb. bulunsun)."""
    current = os.environ.get("PATH", "")
    parts = current.split(os.pathsep) if current else []
    changed = False
    for p in extra:
        if p not in parts and os.path.isdir(p):
            parts.append(p)  # sona ekle → sistem araçları önceliğini korur
            changed = True
    if changed:
        os.environ["PATH"] = os.pathsep.join(parts)


def ensure_utf8() -> None:
    """Süreç kodlamasını UTF-8'e sabitle (zaten UTF-8 ise no-op). Asla raise etmez."""
    try:
        current = (locale.getpreferredencoding(False) or "").lower()
    except Exception:  # noqa: BLE001 — locale sorgusu bile patlarsa yine de dene
        current = ""

    not_utf8 = "utf-8" not in current and "utf8" not in current

    # (a) ÇOCUK SÜREÇLER için env'e LANG/LC_CTYPE yaz: setlocale yalnız BU sürecin C
    # kütüphanesini etkiler; pbcopy/osascript/say/ffmpeg gibi alt-süreçler os.environ'dan
    # okur. LANG yoksa (.app/Launchpad) pbcopy/yapıştırma Türkçe'yi MacRoman sanar →
    # 'ı' (UTF-8 0xC4 0xB1) → 'ƒ±' gibi bozulur. En yaygın dikte bozulmasının nedeni budur.
    if not_utf8 or not os.environ.get("LANG"):
        os.environ.setdefault("LANG", "en_US.UTF-8")
        if "utf-8" not in (os.environ.get("LC_CTYPE") or "").lower():
            os.environ["LC_CTYPE"] = "en_US.UTF-8"

    # (b) BU süreç: open() varsayılan kodlamasını UTF-8'e çek.
    if not_utf8:
        for loc in _UTF8_LOCALES:
            try:
                locale.setlocale(locale.LC_CTYPE, loc)
                break
            except locale.Error:
                continue

    # Konsol akışları yorumlayıcı açılışında (muhtemelen ascii) kurulmuş olabilir →
    # UTF-8'e yeniden yapılandır. Yoksa Türkçe log/print ascii stderr'de patlar.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 — akış reconfigure desteklemiyorsa boş ver
            pass
