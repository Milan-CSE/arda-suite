from pathlib import Path

import pytest

from arda import mcp_stdio


def test_stdio_entrypoint_rejects_missing_core_database(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.argv", ["arda-mcp", "--db", str(tmp_path / "missing.sqlite3")])
    with pytest.raises(SystemExit, match="does not exist"):
        mcp_stdio.main()

