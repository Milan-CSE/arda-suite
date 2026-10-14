"""Foreground Hermes daemon lifecycle without Core authority.

The daemon owns process lifecycle and recovery observation only. It does not
create episodes, call providers, make policy decisions, or write evaluations.
"""
from __future__ import annotations

import argparse
import json
import signal
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from arda.core import Database
from arda.hermes import HermesError, HermesRun, HermesRunStatus, SQLiteHermesRunStore


class HermesDaemonError(RuntimeError):
    """The daemon cannot satisfy its lifecycle contract."""


@dataclass(frozen=True)
class HermesDaemonConfig:
    db_path: Path
    host: str = "127.0.0.1"
    port: int = 8765
    poll_interval_seconds: float = 1.0

    def validate(self) -> None:
        if self.host not in {"127.0.0.1", "::1", "localhost"}:
            raise HermesDaemonError("health server must bind to loopback")
        if not 1 <= self.port <= 65535:
            raise HermesDaemonError("health port must be between 1 and 65535")
        if self.poll_interval_seconds <= 0:
            raise HermesDaemonError("poll interval must be positive")


class _HealthHandler(BaseHTTPRequestHandler):
    daemon: "HermesDaemon"

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path not in {"/healthz", "/readyz"}:
            self._write(404, {"status": "not_found"})
            return
        if self.path == "/healthz":
            self._write(200, self.daemon.liveness())
            return
        status, payload = self.daemon.readiness()
        self._write(status, payload)

    def log_message(self, *_args: Any) -> None:
        return

    def _write(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class HermesDaemon:
    """Own startup, readiness, recovery observation and graceful shutdown."""

    def __init__(self, config: HermesDaemonConfig):
        config.validate()
        self.config = config
        self._db: Database | None = None
        self._store: SQLiteHermesRunStore | None = None
        self._runs: dict[str, HermesRun] = {}
        self._server: ThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = False
        self._started = False
        self._shutdown_lock = threading.Lock()

    def start(self) -> None:
        if self._started:
            raise HermesDaemonError("daemon already started")
        try:
            self._db = Database(self.config.db_path)
            self._db.migrate()
            self._store = SQLiteHermesRunStore(self._db)
            self._runs = self._recover_runs()
            self._server = ThreadingHTTPServer((self.config.host, self.config.port), _HealthHandler)
            self._server.RequestHandlerClass.daemon = self  # type: ignore[attr-defined]
            self._server_thread = threading.Thread(target=self._server.serve_forever, name="hermes-health", daemon=True)
            self._server_thread.start()
            self._started = True
            self._ready = True
        except Exception:
            self.shutdown()
            raise

    def run_forever(self) -> None:
        if not self._started:
            self.start()
        self._stop.wait()

    def request_shutdown(self) -> None:
        self._stop.set()

    def shutdown(self) -> None:
        with self._shutdown_lock:
            self._ready = False
            self._stop.set()
            if self._server is not None:
                self._server.shutdown()
                self._server.server_close()
                self._server = None
            if self._server_thread is not None:
                self._server_thread.join(timeout=2)
                self._server_thread = None
            if self._db is not None:
                self._db.close()
                self._db = None
            self._store = None
            self._started = False

    def liveness(self) -> dict[str, Any]:
        return {"status": "alive", "service": "hermes", "version": "0.1"}

    def readiness(self) -> tuple[int, dict[str, Any]]:
        if not self._ready or self._db is None or self._store is None:
            return 503, {"status": "not_ready", "service": "hermes", "recovery": "incomplete"}
        return 200, {
            "status": "ready",
            "service": "hermes",
            "db": "ok",
            "recovery": "complete",
            "recovered_runs": len(self._runs),
        }

    def recovered_runs(self) -> tuple[HermesRun, ...]:
        return tuple(self._runs.values())

    def _recover_runs(self) -> dict[str, HermesRun]:
        assert self._db is not None
        run_ids = tuple(row[0] for row in self._db.execute("SELECT DISTINCT hermes_run_id FROM hermes_run_events").fetchall())
        recovered: dict[str, HermesRun] = {}
        assert self._store is not None
        for run_id in run_ids:
            try:
                run = self._store.load(run_id)
            except HermesError as exc:
                raise HermesDaemonError(f"cannot recover Hermes run {run_id}") from exc
            if run.status in {HermesRunStatus.CREATED, HermesRunStatus.RUNNING, HermesRunStatus.WAITING}:
                recovered[run_id] = run
        return recovered


def _install_signal_handlers(daemon: HermesDaemon) -> None:
    def handle(_signum: int, _frame: Any) -> None:
        daemon.request_shutdown()

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m arda.hermes_daemon")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args(argv)
    daemon = HermesDaemon(HermesDaemonConfig(args.db, args.host, args.port))
    _install_signal_handlers(daemon)
    try:
        daemon.run_forever()
    finally:
        daemon.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
