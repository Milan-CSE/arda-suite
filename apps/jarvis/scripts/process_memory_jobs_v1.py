#!/usr/bin/env python3
"""Compatibility wrapper for the packaged memory-job processor."""
from __future__ import annotations

from pathlib import Path


def main() -> None:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from arda.memory_jobs import main as packaged_main
    raise SystemExit(packaged_main())


if __name__ == "__main__":
    main()
