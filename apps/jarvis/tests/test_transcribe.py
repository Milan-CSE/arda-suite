"""transcribe.Transcriber testleri — mlx_whisper mock'lu."""
from pathlib import Path
from unittest.mock import patch

import pytest

from arda.transcribe import _MLX_LOCK, Transcriber, transcribe_mlx

CFG = {
    "stt": {
        "model": "mlx-community/whisper-large-v3-turbo",
        "language": "tr",
    }
}

WAV = Path("/tmp/test.wav")
_RAW = "  bugün ORS'de rulman taşlama incelendi.  "


def _mock_transcribe(path, path_or_hf_repo, language):
    return {"text": _RAW}


class TestTranscriber:
    def test_returns_stripped_text(self):
        with patch("arda.transcribe.mlx_whisper.transcribe", side_effect=_mock_transcribe):
            t = Transcriber(CFG)
            result = t.transcribe(WAV)
        assert result == _RAW.strip()

    def test_passes_model_from_cfg(self):
        calls = []
        def capture(path, path_or_hf_repo, language):
            calls.append({"model": path_or_hf_repo, "lang": language})
            return {"text": "metin"}
        with patch("arda.transcribe.mlx_whisper.transcribe", side_effect=capture):
            Transcriber(CFG).transcribe(WAV)
        assert calls[0]["model"] == "mlx-community/whisper-large-v3-turbo"

    def test_passes_language_from_cfg(self):
        calls = []
        def capture(path, path_or_hf_repo, language):
            calls.append(language)
            return {"text": "metin"}
        with patch("arda.transcribe.mlx_whisper.transcribe", side_effect=capture):
            Transcriber(CFG).transcribe(WAV)
        assert calls[0] == "tr"

    def test_passes_initial_prompt_from_profile_vocabulary(self):
        captured = {}
        def capture(path, **kw):
            captured.update(kw)
            return {"text": "metin"}
        cfg = {**CFG, "profile": {"vocabulary": "Sample User, mem0, Qdrant"}}
        with patch("arda.transcribe.mlx_whisper.transcribe", side_effect=capture):
            Transcriber(cfg).transcribe(WAV)
        assert captured.get("initial_prompt") == "Sample User, mem0, Qdrant"

    def test_no_initial_prompt_kwarg_without_vocabulary(self):
        captured = {}
        def capture(path, **kw):
            captured.update(kw)
            return {"text": "metin"}
        with patch("arda.transcribe.mlx_whisper.transcribe", side_effect=capture):
            Transcriber(CFG).transcribe(WAV)      # CFG'de profile yok → kwarg eklenmez
        assert "initial_prompt" not in captured

    def test_passes_wav_path_as_string(self):
        calls = []
        def capture(path, path_or_hf_repo, language):
            calls.append(path)
            return {"text": "metin"}
        with patch("arda.transcribe.mlx_whisper.transcribe", side_effect=capture):
            Transcriber(CFG).transcribe(WAV)
        assert calls[0] == str(WAV)

    def test_raises_on_transcribe_failure(self):
        with patch("arda.transcribe.mlx_whisper.transcribe", side_effect=RuntimeError("model yok")):
            with pytest.raises(RuntimeError):
                Transcriber(CFG).transcribe(WAV)


class TestWarm:
    def test_warm_loads_model_via_transcribe(self):
        calls = []
        def capture(path, path_or_hf_repo, language):
            calls.append(path_or_hf_repo)
            return {"text": ""}
        with patch("arda.transcribe.mlx_whisper.transcribe", side_effect=capture):
            Transcriber(CFG).warm()
        assert calls and calls[0] == "mlx-community/whisper-large-v3-turbo"

    def test_warm_never_raises(self):
        """Isıtma açılışta best-effort'tur; hata startup'ı çökertmemeli."""
        with patch("arda.transcribe.mlx_whisper.transcribe", side_effect=RuntimeError("indirme hatası")):
            Transcriber(CFG).warm()  # raise etmemeli


class TestMlxLock:
    """Kilit takılma-güvenliği: önceki bir transcribe takılırsa (kilidi tutarsa) yeni
    çağrı SONSUZA dek bloklanmamalı; sınırlı sürede TimeoutError'la başarısız olmalı.
    Kilit her durumda (başarı/hata) bırakılmalı → bir takılma tüm STT'yi kalıcı kilitlemez."""

    def test_raises_timeout_when_lock_already_held(self):
        # Takılı önceki çağrıyı taklit et: kilidi tut, sonra yeni çağrı gelsin.
        assert _MLX_LOCK.acquire(blocking=False)
        try:
            with patch("arda.transcribe.mlx_whisper.transcribe") as m:
                with pytest.raises(TimeoutError):
                    transcribe_mlx("/tmp/x.wav", model="m", language=None, lock_timeout=0.05)
                m.assert_not_called()                 # kilit alınamadı → model çağrılmadı
        finally:
            _MLX_LOCK.release()

    def test_releases_lock_after_success(self):
        with patch("arda.transcribe.mlx_whisper.transcribe", return_value={"text": "x"}):
            transcribe_mlx("/tmp/x.wav", model="m", language=None)
        assert _MLX_LOCK.acquire(blocking=False)      # başarıdan sonra kilit serbest
        _MLX_LOCK.release()

    def test_releases_lock_after_exception(self):
        with patch("arda.transcribe.mlx_whisper.transcribe", side_effect=RuntimeError("çöktü")):
            with pytest.raises(RuntimeError):
                transcribe_mlx("/tmp/x.wav", model="m", language=None)
        assert _MLX_LOCK.acquire(blocking=False)      # hatadan sonra da kilit serbest (deadlock yok)
        _MLX_LOCK.release()
