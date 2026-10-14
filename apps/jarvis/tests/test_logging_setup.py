"""arda.logging_setup için testler — dosyaya gerçekten log yazılıyor mu?"""
from __future__ import annotations

import logging

import pytest

from arda import logging_setup


@pytest.fixture(autouse=True)
def _reset_logging():
    """Her test kendi handler'larıyla başlasın; sızıntı olmasın."""
    root = logging.getLogger()
    saved = list(root.handlers)
    saved_level = root.level
    for h in list(root.handlers):
        root.removeHandler(h)
    logging_setup._configured = False
    yield
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in saved:
        root.addHandler(h)
    root.setLevel(saved_level)
    logging_setup._configured = False


def _cfg(tmp_path):
    return {"app": {"log_dir": str(tmp_path), "log_level": "INFO"}}


def test_log_path_uses_config_dir(tmp_path):
    assert logging_setup.log_path(_cfg(tmp_path)) == tmp_path / "jarvis.log"


def test_setup_writes_to_file(tmp_path):
    path = logging_setup.setup_logging(_cfg(tmp_path))
    logging.getLogger("arda.demo").info("selam log")
    for h in logging.getLogger().handlers:
        h.flush()
    text = path.read_text(encoding="utf-8")
    assert "selam log" in text
    assert "arda.demo" in text


def test_exception_traceback_captured(tmp_path):
    """Kök şikâyet: hata olunca traceback dosyaya yazılmalı (exc_info=True)."""
    path = logging_setup.setup_logging(_cfg(tmp_path))
    try:
        raise ValueError("bilerek patlattım")
    except ValueError as exc:
        logging.getLogger("arda.pipeline").warning("İşleme hatası: %s", exc, exc_info=True)
    for h in logging.getLogger().handlers:
        h.flush()
    text = path.read_text(encoding="utf-8")
    assert "bilerek patlattım" in text
    assert "Traceback" in text  # exc_info tam yığın izini yazar


def test_idempotent_no_duplicate_handlers(tmp_path):
    logging_setup.setup_logging(_cfg(tmp_path))
    n = len(logging.getLogger().handlers)
    logging_setup.setup_logging(_cfg(tmp_path))  # ikinci çağrı no-op olmalı
    assert len(logging.getLogger().handlers) == n
