"""arda.parakeet + transcribe.make_transcriber testleri.

parakeet_mlx kurulu olmadan koşar: load_model lazy import yapar; testler ya cache'i
önceden doldurur ya da arda.parakeet.* fonksiyonlarını patch'ler → ağır dep gerekmez.
"""
from pathlib import Path
from unittest.mock import patch

import arda.parakeet as pk
from arda.constants import PARAKEET_MODEL
from arda.transcribe import ParakeetTranscriber, Transcriber, make_transcriber

WAV = Path("/tmp/test.wav")


class _FakeResult:
    def __init__(self, text):
        self.text = text


class _FakeModel:
    """transcribe(path) → .text taşıyan sonuç döndürür (parakeet API şekli)."""

    def __init__(self, text):
        self._text = text
        self.calls = []

    def transcribe(self, path):
        self.calls.append(path)
        return _FakeResult(self._text)


class TestMakeTranscriber:
    def test_returns_parakeet_for_parakeet_backend(self):
        t = make_transcriber({"stt": {"backend": "parakeet"}})
        assert isinstance(t, ParakeetTranscriber)

    def test_returns_whisper_for_whisper_backend(self):
        t = make_transcriber({"stt": {"backend": "whisper"}})
        assert isinstance(t, Transcriber)

    def test_returns_whisper_by_default(self):
        t = make_transcriber({"stt": {}})
        assert isinstance(t, Transcriber)


class TestLoadModelCache:
    def test_cache_hit_returns_same_instance_without_reimport(self):
        repo = "test/cached-model"
        sentinel = object()
        pk._models[repo] = sentinel
        try:
            # from_pretrained çağrılmamalı (cache hit) → parakeet_mlx gerekmez.
            assert pk.load_model(repo) is sentinel
        finally:
            pk._models.pop(repo, None)

    def test_default_repo_is_constant(self):
        assert pk.DEFAULT_MODEL == PARAKEET_MODEL


class TestTranscribeFile:
    def test_returns_stripped_text(self):
        model = _FakeModel("  hello world  ")
        assert pk.transcribe_file(model, WAV) == "hello world"

    def test_passes_path_as_string(self):
        model = _FakeModel("x")
        pk.transcribe_file(model, WAV)
        assert model.calls == [str(WAV)]


class TestParakeetTranscriber:
    def test_transcribe_uses_configured_repo(self):
        captured = {}

        def fake_load(repo):
            captured["repo"] = repo
            return "MODEL"

        def fake_tf(model, wav):
            captured["model"] = model
            captured["wav"] = wav
            return "metin"

        cfg = {"stt": {"parakeet_model": "custom/repo"}}
        with patch("arda.parakeet.load_model", side_effect=fake_load), \
             patch("arda.parakeet.transcribe_file", side_effect=fake_tf):
            out = ParakeetTranscriber(cfg).transcribe(WAV)

        assert out == "metin"
        assert captured["repo"] == "custom/repo"
        assert captured["model"] == "MODEL"
        assert captured["wav"] == WAV

    def test_transcribe_defaults_to_constant_repo(self):
        captured = {}
        with patch("arda.parakeet.load_model", side_effect=lambda r: captured.setdefault("repo", r)), \
             patch("arda.parakeet.transcribe_file", side_effect=lambda m, w: "ok"):
            ParakeetTranscriber({"stt": {}}).transcribe(WAV)
        assert captured["repo"] == PARAKEET_MODEL

    def test_warm_loads_model(self):
        calls = []
        with patch("arda.parakeet.load_model", side_effect=lambda r: calls.append(r)):
            ParakeetTranscriber({"stt": {"parakeet_model": "r"}}).warm()
        assert calls == ["r"]

    def test_warm_never_raises(self):
        """Isıtma best-effort: indirme/yükleme hatası startup'ı çökertmemeli."""
        with patch("arda.parakeet.load_model", side_effect=RuntimeError("indirme hatası")):
            ParakeetTranscriber({"stt": {}}).warm()  # raise etmemeli
