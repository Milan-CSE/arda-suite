"""Dikte pano okuması geçersiz UTF-8'de ÇÖKMEMELİ (0xd1 gibi).

Regresyon: pano MacRoman/latin baytı içerince pbpaste strict UTF-8 decode
patlıyordu → 'Dikte turu hatası: utf-8 codec can't decode byte 0xd1'. Tüm dikte
turunu düşürüyordu. Artık errors='replace' ile dayanıklı okunur.
"""
from __future__ import annotations

import types

from arda import dictation


def test_clip_get_survives_invalid_utf8(monkeypatch):
    # pbpaste geçersiz UTF-8 bayt dizisi döndürsün (0xd1 = MacRoman '—', UTF-8 değil).
    bad = b"merhaba \xd1 dunya"

    def fake_run(cmd, capture_output=False, check=False, timeout=None, **kw):
        assert cmd == ["pbpaste"]
        # text=True KULLANILMAMALI (byte + explicit decode olmalı) → stdout bytes döner.
        assert not kw.get("text"), "pbpaste artık text=True ile strict decode etmemeli"
        return types.SimpleNamespace(stdout=bad, returncode=0)

    monkeypatch.setattr(dictation.subprocess, "run", fake_run)
    out = dictation._clip_get()  # patlamamalı
    assert isinstance(out, str)
    assert "merhaba" in out and "dunya" in out  # geri kalan metin korunur


def test_insert_text_restores_clipboard_even_with_weird_previous():
    """insert_text önce yeni metni panoya koyar, sonra eski panoyu geri yükler."""
    events = []

    def fake_run(cmd, capture_output=False, text=False, check=False, timeout=None, **kw):
        return types.SimpleNamespace(returncode=0, stdout="")

    ok = dictation.insert_text(
        "yeni metin",
        clip_get=lambda: "önceki � içerik",
        clip_set=lambda t: events.append(t),
        run=fake_run,
        sleep=lambda s: None,
    )
    assert ok is True
    assert events[0] == "yeni metin"        # önce yeni metin panoya
    assert events[-1] == "önceki � içerik"  # sonra eski pano geri
