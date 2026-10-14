"""Merkezi loglama kurulumu — dosya + konsol.

main() en başında BİR KEZ çağrılır. Root logger'a döner (rotating) dosya handler'ı
ve konsol handler'ı bağlar; tüm modüllerdeki ``logging.getLogger(__name__)``
çağrıları buraya akar. Bu kurulmazsa Python varsayılanı devreye girer: yalnızca
WARNING+ stderr'e düşer, INFO/DEBUG sessizce yutulur ve HİÇBİR dosya tutulmaz —
"log tutmuyor" şikâyetinin kök nedeni budur.

Log yolu: ``app.log_dir`` (config) → yoksa ``~/Library/Logs/Jarvis`` (macOS
standart konumu; Console.app'te görünür). ``agent.log_dir`` (agent denetim
günlükleri) ile karıştırılmamalı — o ayrı bir şeydir.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_DEFAULT_LOG_DIR = Path.home() / "Library" / "Logs" / "Jarvis"
_FMT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_MAX_BYTES = 2_000_000
_BACKUP_COUNT = 5

# Aynı süreçte iki kez çağrılırsa handler kopyalanmasın (log satırları çiftlenmesin).
_configured = False


def log_path(cfg: dict | None = None) -> Path:
    """Aktif log dosyasının tam yolunu döndür (config'e göre)."""
    app = (cfg or {}).get("app", {})
    raw = app.get("log_dir")
    base = Path(raw).expanduser() if raw else _DEFAULT_LOG_DIR
    return base / "jarvis.log"


def _level(cfg: dict | None) -> int:
    name = str((cfg or {}).get("app", {}).get("log_level", "INFO")).upper()
    return getattr(logging, name, logging.INFO)


def setup_logging(cfg: dict | None = None, *, force: bool = False) -> Path:
    """Root logger'ı dosya + konsol handler'larıyla yapılandır; log yolunu döndür.

    Idempotent: tekrar çağrılırsa (force=False) hiçbir şey yapmaz. force=True
    önceki kurulumun handler'larını söküp yeniden bağlar (config değişince kullanışlı).
    """
    global _configured
    path = log_path(cfg)
    root = logging.getLogger()

    if _configured and not force:
        return path

    # force: bu modülün daha önce eklediği handler'ları temizle (başkalarınınkine dokunma).
    for h in list(root.handlers):
        if getattr(h, "_jarvis_handler", False):
            root.removeHandler(h)
            h.close()

    path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = RotatingFileHandler(
        path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(_FMT))
    file_handler._jarvis_handler = True  # type: ignore[attr-defined]

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(_FMT))
    console._jarvis_handler = True  # type: ignore[attr-defined]

    level = _level(cfg)
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(console)

    _configured = True
    logging.getLogger(__name__).info(
        "Loglama başladı → %s (seviye=%s)", path, logging.getLevelName(level)
    )
    return path
