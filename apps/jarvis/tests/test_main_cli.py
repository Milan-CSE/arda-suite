"""CLI smoke tests: --help/--version uygulamayı başlatmadan dönmeli."""
import pytest

from arda.__main__ import main


def test_help_exits_before_startup(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "masaüstü sesli asistan" in capsys.readouterr().out


def test_version_exits_before_startup(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "0.1.0" in capsys.readouterr().out
