"""wake.WakeListener ve WhisperWakeListener testleri — model ve ses akışı mock'lu."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pytest

from arda.wake import (
    ParakeetWakeListener,
    VoskWakeListener,
    WakeListener,
    WhisperWakeListener,
    make_listener,
)


class _FakeStream:
    def __init__(self, frame):
        self._frame = frame

    def read(self, n):
        return self._frame, False


@contextmanager
def _stream_factory():
    yield _FakeStream(np.zeros((1280, 1), dtype="int16"))


class _FakeModel:
    """predict() çağrıldıkça sıradaki skor sözlüğünü döndürür."""

    def __init__(self, score_sequence):
        self._scores = list(score_sequence)
        self.calls = 0

    def predict(self, audio):
        self.calls += 1
        return self._scores.pop(0) if self._scores else {"arda": 0.0}


class TestTriggered:
    def test_triggers_when_any_score_at_or_above_threshold(self):
        assert WakeListener._triggered({"arda": 0.6}, 0.5) is True

    def test_no_trigger_below_threshold(self):
        assert WakeListener._triggered({"arda": 0.49}, 0.5) is False

    def test_ignores_other_low_scores(self):
        assert WakeListener._triggered({"arda": 0.1, "alexa": 0.2}, 0.5) is False


class TestWaitForWake:
    def test_returns_on_first_frame_over_threshold(self):
        model = _FakeModel([{"arda": 0.9}])
        listener = WakeListener({"wake": {"threshold": 0.5}}, model=model)
        listener.wait_for_wake(stream_factory=_stream_factory)
        assert model.calls == 1

    def test_keeps_listening_until_wake(self):
        # 3 sessiz kare, sonra tetikleyen kare → 4. çağrıda döner.
        model = _FakeModel(
            [{"arda": 0.0}, {"arda": 0.1}, {"arda": 0.2}, {"arda": 0.8}]
        )
        listener = WakeListener({"wake": {"threshold": 0.5}}, model=model)
        listener.wait_for_wake(stream_factory=_stream_factory)
        assert model.calls == 4

    def test_respects_custom_threshold(self):
        model = _FakeModel([{"arda": 0.4}, {"arda": 0.4}, {"arda": 0.4}, {"arda": 0.4}])
        listener = WakeListener({"wake": {"threshold": 0.95}}, model=model)
        # Skorlar hiç eşiği geçmez; stop callback ile sonsuz döngüyü kır.
        calls = {"n": 0}

        def _stop():
            calls["n"] += 1
            return calls["n"] > 3

        listener.wait_for_wake(stream_factory=_stream_factory, stop=_stop)
        assert calls["n"] >= 3


class TestManualTriggerAndWakeToggle:
    def test_manual_trigger_returns_without_running_model(self):
        """'Şimdi Kaydet' → wake modeli çalışmadan hemen kayda geçilir."""
        model = _FakeModel([{"arda": 0.0}])
        listener = WakeListener({"wake": {"threshold": 0.5}}, model=model)
        woke = listener.wait_for_wake(stream_factory=_stream_factory, triggered=lambda: True)
        assert woke is True
        assert model.calls == 0  # tetik kareyi/predict'i atladı

    def test_wake_off_skips_model_but_still_drains_stream(self):
        """Wake kapalıyken model çalışmaz; sadece manuel tetik beklenir."""
        model = _FakeModel([{"arda": 0.9}])  # eşik üstü ama wake kapalı → kullanılmamalı
        listener = WakeListener({"wake": {"threshold": 0.5}}, model=model)
        calls = {"n": 0}

        def _stop():
            calls["n"] += 1
            return calls["n"] > 3

        woke = listener.wait_for_wake(
            stream_factory=_stream_factory, stop=_stop, wake_on=lambda: False
        )
        assert woke is False           # tetik yok, wake kapalı → stop ile çıktı
        assert model.calls == 0        # wake kapalıyken predict hiç çağrılmadı

    def test_wake_off_still_honors_manual_trigger(self):
        model = _FakeModel([{"arda": 0.0}])
        listener = WakeListener({"wake": {"threshold": 0.5}}, model=model)
        woke = listener.wait_for_wake(
            stream_factory=_stream_factory, wake_on=lambda: False, triggered=lambda: True
        )
        assert woke is True
        assert model.calls == 0


# ---------------------------------------------------------------------------
# WhisperWakeListener testleri
# ---------------------------------------------------------------------------

class TestWhisperWakeListener:
    _cfg = {"wake": {"phrase": "hey jarvis", "chunk_sec": 0.08}, "stt": {}}

    def test_triggers_when_phrase_in_transcription(self):
        calls = {"n": 0}

        def _transcribe(audio):
            calls["n"] += 1
            return "Hey Jarvis, müzik aç"

        listener = WhisperWakeListener(self._cfg, transcribe_fn=_transcribe)
        result = listener.wait_for_wake(stream_factory=_stream_factory)
        assert result is True
        assert calls["n"] == 1

    def test_keeps_listening_until_phrase_found(self):
        responses = ["sessizlik", "gürültü", "hey jarvis"]
        calls = {"n": 0}

        def _transcribe(audio):
            calls["n"] += 1
            return responses[min(calls["n"] - 1, len(responses) - 1)]

        listener = WhisperWakeListener(self._cfg, transcribe_fn=_transcribe)
        result = listener.wait_for_wake(stream_factory=_stream_factory)
        assert result is True
        assert calls["n"] == 3

    def test_manual_trigger_skips_transcription(self):
        calls = {"n": 0}

        def _transcribe(audio):
            calls["n"] += 1
            return "hiçbir şey"

        listener = WhisperWakeListener(self._cfg, transcribe_fn=_transcribe)
        result = listener.wait_for_wake(
            stream_factory=_stream_factory, triggered=lambda: True
        )
        assert result is True
        assert calls["n"] == 0

    def test_stop_returns_false(self):
        stop_count = {"n": 0}

        def _stop():
            stop_count["n"] += 1
            return stop_count["n"] > 2

        def _transcribe(audio):
            return "sessizlik"

        listener = WhisperWakeListener(self._cfg, transcribe_fn=_transcribe)
        result = listener.wait_for_wake(stream_factory=_stream_factory, stop=_stop)
        assert result is False

    def test_wake_off_skips_transcription(self):
        calls = {"n": 0}
        stop_count = {"n": 0}

        def _transcribe(audio):
            calls["n"] += 1
            return "hey jarvis"

        def _stop():
            stop_count["n"] += 1
            return stop_count["n"] > 3

        listener = WhisperWakeListener(self._cfg, transcribe_fn=_transcribe)
        result = listener.wait_for_wake(
            stream_factory=_stream_factory, stop=_stop, wake_on=lambda: False
        )
        assert result is False
        assert calls["n"] == 0

    def test_case_insensitive_match(self):
        def _transcribe(audio):
            return "HEY JARVIS ne yapıyorsun"

        listener = WhisperWakeListener(self._cfg, transcribe_fn=_transcribe)
        assert listener.wait_for_wake(stream_factory=_stream_factory) is True

    def test_default_transcribe_uses_shared_mlx_helper(self, monkeypatch):
        calls = []

        def _fake_transcribe(audio, *, model, language):
            calls.append({"audio": audio, "model": model, "language": language})
            return {"text": " hey jarvis "}

        monkeypatch.setattr("arda.transcribe.transcribe_mlx", _fake_transcribe)
        cfg = {
            "wake": {"phrase": "hey jarvis", "whisper_model": "wake/model", "language": "tr"},
            "stt": {},
        }
        listener = WhisperWakeListener(cfg)
        audio = np.zeros(1600, dtype=np.float32)

        assert listener._transcribe(audio) == "hey jarvis"
        assert calls[0]["audio"] is audio
        assert calls[0]["model"] == "wake/model"
        assert calls[0]["language"] == "tr"


class TestWhisperWakeSpeakerVerify:
    _cfg = {"wake": {"phrase": "hey jarvis", "chunk_sec": 0.08, "speaker_verify": True, "speaker_profile": "/nonexistent"}, "stt": {}}

    def test_skips_when_speaker_does_not_match(self):
        stop_count = {"n": 0}

        def _transcribe(audio):
            return "hey jarvis"

        def _verify(audio):
            return False  # yabancı ses

        def _stop():
            stop_count["n"] += 1
            return stop_count["n"] > 2

        listener = WhisperWakeListener(self._cfg, transcribe_fn=_transcribe, verify_fn=_verify)
        result = listener.wait_for_wake(stream_factory=_stream_factory, stop=_stop)
        assert result is False

    def test_triggers_when_speaker_matches(self):
        def _transcribe(audio):
            return "hey jarvis"

        def _verify(audio):
            return True  # kayıtlı ses

        listener = WhisperWakeListener(self._cfg, transcribe_fn=_transcribe, verify_fn=_verify)
        result = listener.wait_for_wake(stream_factory=_stream_factory)
        assert result is True


class TestParakeetWakeListener:
    _cfg = {"wake": {"phrase": "hey jarvis", "chunk_sec": 0.08, "backend": "parakeet"}, "stt": {}}

    def test_triggers_when_phrase_in_transcription(self):
        calls = {"n": 0}

        def _transcribe(audio):
            calls["n"] += 1
            return "Hey Jarvis, play music"

        listener = ParakeetWakeListener(self._cfg, transcribe_fn=_transcribe)
        assert listener.wait_for_wake(stream_factory=_stream_factory) is True
        assert calls["n"] == 1

    def test_keeps_listening_until_phrase_found(self):
        responses = ["silence", "noise", "hey jarvis"]
        calls = {"n": 0}

        def _transcribe(audio):
            calls["n"] += 1
            return responses[min(calls["n"] - 1, len(responses) - 1)]

        listener = ParakeetWakeListener(self._cfg, transcribe_fn=_transcribe)
        assert listener.wait_for_wake(stream_factory=_stream_factory) is True
        assert calls["n"] == 3

    def test_manual_trigger_skips_transcription(self):
        calls = {"n": 0}

        def _transcribe(audio):
            calls["n"] += 1
            return "nothing"

        listener = ParakeetWakeListener(self._cfg, transcribe_fn=_transcribe)
        result = listener.wait_for_wake(stream_factory=_stream_factory, triggered=lambda: True)
        assert result is True
        assert calls["n"] == 0

    def test_resolves_model_repo_from_stt_when_wake_unset(self):
        cfg = {"wake": {"backend": "parakeet"}, "stt": {"parakeet_model": "from/stt"}}
        listener = ParakeetWakeListener(cfg, transcribe_fn=lambda a: "")
        assert listener._model_repo == "from/stt"

    def test_wake_specific_model_overrides_stt(self):
        cfg = {"wake": {"backend": "parakeet", "parakeet_model": "from/wake"}, "stt": {"parakeet_model": "from/stt"}}
        listener = ParakeetWakeListener(cfg, transcribe_fn=lambda a: "")
        assert listener._model_repo == "from/wake"


class _FakeVoskRec:
    """Enjekte edilebilir sahte KaldiRecognizer: AcceptWaveform final/partial'ı kontrol eder."""

    def __init__(self, *, accept, text="", partial=""):
        self._accept = accept
        self._text = text
        self._partial = partial
        self.reset_called = False

    def AcceptWaveform(self, _data):
        return self._accept

    def Result(self):
        import json
        return json.dumps({"text": self._text})

    def PartialResult(self):
        import json
        return json.dumps({"partial": self._partial})

    def Reset(self):
        self.reset_called = True


class TestVoskWakeMatch:
    """Vosk wake yalnız FİNAL sonuçta tetikler — partial (yarım) tetiklemez.

    Regresyon: keyword-grammar 'hey' derken sesi erkenden 'hey jarvis'e snap edip
    partial'da tam phrase üretebiliyordu → 'hey' yanlış uyandırıyordu."""

    def _listener(self, rec):
        listener = VoskWakeListener({"wake": {"phrase": "hey jarvis", "vosk_model": "models/vosk-en"}})
        listener._rec = rec
        return listener

    def test_partial_result_does_not_trigger(self):
        rec = _FakeVoskRec(accept=False, partial="hey jarvis")  # partial tam phrase olsa bile
        listener = self._listener(rec)
        n = {"i": 0}

        def _stop():
            n["i"] += 1
            return n["i"] > 3

        assert listener.wait_for_wake(stream_factory=_stream_factory, stop=_stop) is False
        assert rec.reset_called is False

    def test_final_result_triggers(self):
        rec = _FakeVoskRec(accept=True, text="hey jarvis")
        listener = self._listener(rec)
        assert listener.wait_for_wake(stream_factory=_stream_factory) is True
        assert rec.reset_called is True

    def test_final_result_without_phrase_does_not_trigger(self):
        rec = _FakeVoskRec(accept=True, text="hey")  # final ama tam phrase değil
        listener = self._listener(rec)
        n = {"i": 0}

        def _stop():
            n["i"] += 1
            return n["i"] > 3

        assert listener.wait_for_wake(stream_factory=_stream_factory, stop=_stop) is False

    def test_model_load_error_contains_configured_path(self, monkeypatch):
        class _BrokenModel:
            def __init__(self, _path):
                raise OSError("bad model")

        monkeypatch.setitem(__import__("sys").modules, "vosk", type("Vosk", (), {
            "Model": _BrokenModel,
            "KaldiRecognizer": object,
        })())
        listener = VoskWakeListener({"wake": {"vosk_model": "/tmp/missing-vosk"}})
        with pytest.raises(RuntimeError, match="/tmp/missing-vosk"):
            listener._get_recognizer()


class TestPathResolution:
    """F2: göreli model/profil yolları repo köküne göre çözülür (.app cwd'den bağımsız)."""

    def test_vosk_relative_model_resolved_to_absolute(self):
        listener = VoskWakeListener({"wake": {"vosk_model": "models/vosk-en"}})
        assert Path(listener._model_path).is_absolute()
        assert listener._model_path.endswith("models/vosk-en")

    def test_vosk_absolute_model_unchanged(self):
        listener = VoskWakeListener({"wake": {"vosk_model": "/abs/vosk-en"}})
        assert listener._model_path == "/abs/vosk-en"

    def test_whisper_relative_speaker_profile_resolved(self):
        cfg = {"wake": {"speaker_verify": False, "speaker_profile": "models/p.npy"}, "stt": {}}
        listener = WhisperWakeListener(cfg, transcribe_fn=lambda a: "")
        assert Path(listener._speaker_profile_path).is_absolute()
        assert listener._speaker_profile_path.endswith("models/p.npy")


class TestSpeakerVerifyWarning:
    """speaker_verify desteklenmeyen backend'de fail-safe: wake kurulmaz, PTT fallback'e düşer."""

    def test_rejects_when_speaker_verify_on_vosk(self, caplog):
        with caplog.at_level(logging.WARNING):
            with pytest.raises(RuntimeError, match="speaker_verify=true"):
                make_listener({"wake": {"backend": "vosk", "speaker_verify": True}})
        assert "DESTEKLEMEZ" in caplog.text

    def test_no_warn_when_speaker_verify_on_parakeet(self, caplog):
        cfg = {"wake": {"backend": "parakeet", "speaker_verify": True, "speaker_profile": "/nonexistent"}, "stt": {}}
        with caplog.at_level(logging.WARNING):
            make_listener(cfg, transcribe_fn=lambda a: "")
        assert "DESTEKLEMEZ" not in caplog.text


class TestMakeListener:
    def test_returns_whisper_listener_for_whisper_backend(self):
        cfg = {"wake": {"backend": "whisper"}, "stt": {}}
        listener = make_listener(cfg, transcribe_fn=lambda a: "")
        assert isinstance(listener, WhisperWakeListener)

    def test_returns_parakeet_listener_for_parakeet_backend(self):
        cfg = {"wake": {"backend": "parakeet"}, "stt": {}}
        listener = make_listener(cfg, transcribe_fn=lambda a: "")
        assert isinstance(listener, ParakeetWakeListener)

    def test_returns_oww_listener_for_default(self):
        from arda.wake import WakeListener
        model = _FakeModel([{"arda": 0.0}])
        cfg = {"wake": {}}
        listener = make_listener(cfg, model=model)
        assert isinstance(listener, WakeListener)

    def test_returns_oww_listener_for_explicit_oww(self):
        model = _FakeModel([{"arda": 0.0}])
        cfg = {"wake": {"backend": "oww"}}
        listener = make_listener(cfg, model=model)
        assert isinstance(listener, WakeListener)
