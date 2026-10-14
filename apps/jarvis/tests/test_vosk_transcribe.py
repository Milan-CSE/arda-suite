import sys
import types
import wave
from pathlib import Path

from arda import transcribe


def test_make_transcriber_selects_vosk(tmp_path):
    result = transcribe.make_transcriber({"stt": {"backend": "vosk", "vosk_model": str(tmp_path)}})
    assert isinstance(result, transcribe.VoskTranscriber)
    assert result._model_path == str(tmp_path)


def test_vosk_transcriber_reads_final_text(monkeypatch, tmp_path):
    wav = Path(tmp_path) / "sample.wav"
    with wave.open(str(wav), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"\x01\x00" * 16000)

    class FakeRecognizer:
        def __init__(self, _model, _rate):
            pass
        def AcceptWaveform(self, _chunk):
            return True
        def FinalResult(self):
            return '{"text":"merhaba jarvis"}'

    fake_vosk = types.SimpleNamespace(
        Model=lambda path: {"path": path}, KaldiRecognizer=FakeRecognizer
    )
    monkeypatch.setitem(sys.modules, "vosk", fake_vosk)
    monkeypatch.setattr(transcribe, "is_silent", lambda *_args: False)
    transcribe._VOSK_MODELS.clear()
    result = transcribe.VoskTranscriber({"stt": {"vosk_model": str(tmp_path)}}).transcribe(wav)
    assert result == "merhaba jarvis"
