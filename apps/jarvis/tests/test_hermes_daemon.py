import json
import signal
import threading
import urllib.request

import pytest

from arda.core import Database
from arda.hermes import HermesCoordinator, SQLiteHermesRunStore, WorkflowDefinition, WorkflowStep
from arda.hermes_daemon import HermesDaemon, HermesDaemonConfig, HermesDaemonError


def test_daemon_rejects_non_loopback_health_binding(tmp_path):
    with pytest.raises(HermesDaemonError, match="loopback"):
        HermesDaemon(HermesDaemonConfig(tmp_path / "core.sqlite3", host="0.0.0.0"))


def test_daemon_starts_ready_and_shuts_down_gracefully(tmp_path):
    daemon = HermesDaemon(HermesDaemonConfig(tmp_path / "core.sqlite3", port=18765))
    daemon.start()
    try:
        with urllib.request.urlopen("http://127.0.0.1:18765/healthz") as response:
            assert json.load(response)["status"] == "alive"
        with urllib.request.urlopen("http://127.0.0.1:18765/readyz") as response:
            payload = json.load(response)
            assert payload["status"] == "ready"
            assert payload["recovery"] == "complete"
    finally:
        daemon.shutdown()
    assert daemon.readiness()[0] == 503


def test_daemon_recovers_active_runs_before_readiness(tmp_path):
    path = tmp_path / "core.sqlite3"
    db = Database(path)
    db.migrate()
    store = SQLiteHermesRunStore(db)
    workflow = WorkflowDefinition("daemon-test", "0.1", (WorkflowStep("route", "route_proposal", "coding.review"),))
    coordinator = HermesCoordinator(episode_exists=lambda _: True, store=store)
    run = coordinator.start(coordinator.create_run(hermes_run_id="daemon-run", episode_id="episode-1", workflow=workflow))
    db.close()

    daemon = HermesDaemon(HermesDaemonConfig(path, port=18766))
    daemon.start()
    try:
        recovered = daemon.recovered_runs()
        assert len(recovered) == 1
        assert recovered[0].hermes_run_id == run.hermes_run_id
        assert recovered[0].status.value == "RUNNING"
    finally:
        daemon.shutdown()


def test_daemon_foreground_loop_stops_on_request(tmp_path):
    daemon = HermesDaemon(HermesDaemonConfig(tmp_path / "core.sqlite3", port=18767))
    daemon.start()
    thread = threading.Thread(target=daemon.run_forever)
    thread.start()
    daemon.request_shutdown()
    thread.join(timeout=2)
    assert not thread.is_alive()
    daemon.shutdown()
