from arda import platform_runtime as runtime


def test_macos_commands_are_preserved(monkeypatch):
    monkeypatch.setattr(runtime, "system", lambda: "Darwin")
    assert runtime.open_url_command("https://example.com") == ["open", "https://example.com"]
    assert runtime.open_app_command("Safari") == ["open", "-a", "Safari"]
    assert runtime.tts_command("merhaba", "Yelda") == ["say", "-v", "Yelda", "merhaba"]


def test_linux_uses_xdg_open_when_available(monkeypatch):
    monkeypatch.setattr(runtime, "system", lambda: "Linux")
    monkeypatch.setattr(runtime.shutil, "which", lambda name: "/usr/bin/xdg-open" if name == "xdg-open" else None)
    assert runtime.open_url_command("https://example.com") == ["xdg-open", "https://example.com"]
    assert runtime.open_app_command("Firefox") is None


def test_linux_tts_uses_espeak_and_sanitizes_macos_voice(monkeypatch):
    monkeypatch.setattr(runtime, "system", lambda: "Linux")
    monkeypatch.setattr(runtime.shutil, "which", lambda name: "/usr/bin/espeak-ng" if name == "espeak-ng" else None)
    assert runtime.tts_command("merhaba", "Yelda") == ["/usr/bin/espeak-ng", "-v", "en", "merhaba"]
    assert runtime.tts_command("merhaba", "tr") == ["/usr/bin/espeak-ng", "-v", "tr", "merhaba"]


def test_linux_tts_render_uses_wav_output(monkeypatch):
    monkeypatch.setattr(runtime, "system", lambda: "Linux")
    monkeypatch.setattr(runtime.shutil, "which", lambda name: "/usr/bin/espeak-ng" if name == "espeak-ng" else None)
    command = runtime.tts_render_command("merhaba", "Yelda", "/tmp/out.wav")
    assert command == ["/usr/bin/espeak-ng", "-v", "en", "-w", "/tmp/out.wav", "merhaba"]


def test_linux_ollama_uses_local_binary(monkeypatch):
    monkeypatch.setattr(runtime, "system", lambda: "Linux")
    monkeypatch.setattr(runtime.shutil, "which", lambda name: "/usr/bin/ollama" if name == "ollama" else None)
    assert runtime.ollama_launch_command() == ["/usr/bin/ollama", "serve"]
