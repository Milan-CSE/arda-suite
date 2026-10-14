import sys
import threading
import time

from arda.process_runner import ManagedProcessRunner


def _sleep_command():
    return [sys.executable, "-c", "import time; time.sleep(5)"]


def test_cancel_terminates_owned_process_within_bound():
    cancelled = threading.Event()
    runner = ManagedProcessRunner(should_cancel=cancelled.is_set, grace_period=0.1)
    result = {}

    def execute():
        result["value"] = runner.run(_sleep_command())

    thread = threading.Thread(target=execute)
    started = time.monotonic()
    thread.start()
    time.sleep(0.1)
    cancelled.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert time.monotonic() - started < 2
    assert "process terminated: cancelled" in result["value"].stderr


def test_close_terminates_all_owned_processes():
    runner = ManagedProcessRunner(grace_period=0.1)
    result = {}
    thread = threading.Thread(target=lambda: result.setdefault("value", runner.run(_sleep_command())))
    thread.start()
    time.sleep(0.1)
    runner.close()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert result["value"].returncode is not None
