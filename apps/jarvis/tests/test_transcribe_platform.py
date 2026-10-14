from arda import transcribe


def test_missing_mlx_backend_fails_with_actionable_message(monkeypatch):
    missing = type("Missing", (), {
        "transcribe": staticmethod(lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("mlx-whisper bu platformda kurulu değil")))
    })()
    monkeypatch.setattr(transcribe, "mlx_whisper", missing)
    try:
        transcribe.transcribe_mlx("/tmp/missing.wav", model="m", language="tr")
    except RuntimeError as exc:
        assert "bu platformda kurulu değil" in str(exc)
    else:
        raise AssertionError("eksik MLX backend'i sessizce başarılı olmamalı")
