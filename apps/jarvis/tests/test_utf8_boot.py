"""arda.utf8_boot — ASCII locale'de bile open() UTF-8'e dönmeli."""
from __future__ import annotations

import locale

import pytest

import os

from arda.utf8_boot import ensure_path, ensure_utf8


@pytest.fixture(autouse=True)
def _restore_locale():
    saved = locale.setlocale(locale.LC_CTYPE)
    yield
    try:
        locale.setlocale(locale.LC_CTYPE, saved)
    except locale.Error:
        pass


def _try_set(loc: str) -> bool:
    try:
        locale.setlocale(locale.LC_CTYPE, loc)
        return True
    except locale.Error:
        return False


def test_ensure_utf8_recovers_from_ascii(tmp_path):
    if not _try_set("C"):  # ASCII/POSIX locale kur (app ortamı taklidi)
        pytest.skip("C locale bu sistemde yok")
    assert "utf-8" not in (locale.getpreferredencoding(False) or "").lower()

    ensure_utf8()

    assert "utf-8" in (locale.getpreferredencoding(False) or "").lower()

    # Asıl kanıt: UTF-8 baytı içeren dosya encoding VERMEDEN açılıp okunabilmeli.
    f = tmp_path / "cfg.json"
    f.write_text('{"x": "em—dash — ş ı"}', encoding="utf-8")
    assert "—" in open(f).read()  # noqa: PLW1514 — locale varsayılanını test ediyoruz


def test_ensure_utf8_idempotent_when_already_utf8():
    if not _try_set("en_US.UTF-8"):
        pytest.skip("UTF-8 locale bu sistemde yok")
    ensure_utf8()  # patlamamalı, no-op
    assert "utf-8" in (locale.getpreferredencoding(False) or "").lower()


def test_ensure_utf8_exports_lang_for_children(monkeypatch):
    """LANG yoksa (.app durumu) env'e UTF-8 LANG yazılmalı → pbcopy/paste Türkçe'yi bozmasın."""
    monkeypatch.delenv("LANG", raising=False)
    monkeypatch.delenv("LC_CTYPE", raising=False)
    ensure_utf8()
    assert "utf-8" in os.environ.get("LANG", "").lower()


def test_ensure_utf8_keeps_existing_utf8_lang(monkeypatch):
    """Zaten UTF-8 LANG varsa ezilmemeli."""
    monkeypatch.setenv("LANG", "tr_TR.UTF-8")
    ensure_utf8()
    assert os.environ["LANG"] == "tr_TR.UTF-8"


def test_ensure_path_adds_existing_dir(tmp_path, monkeypatch):
    """Var olan araç dizini minimal PATH'e eklenmeli (GUI ffmpeg tuzağı)."""
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    ensure_path(extra=(str(tmp_path),))
    assert str(tmp_path) in os.environ["PATH"].split(os.pathsep)


def test_ensure_path_skips_missing_dir(monkeypatch):
    """Olmayan dizin PATH'e EKLENMEMELI (kirletme)."""
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    ensure_path(extra=("/definitely/not/a/real/dir/xyz",))
    assert "/definitely/not/a/real/dir/xyz" not in os.environ["PATH"].split(os.pathsep)


def test_ensure_path_no_duplicate(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", f"/usr/bin{os.pathsep}{tmp_path}")
    ensure_path(extra=(str(tmp_path),))
    assert os.environ["PATH"].split(os.pathsep).count(str(tmp_path)) == 1
