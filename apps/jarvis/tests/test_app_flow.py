"""Entegrasyon: gerçek Controls + WakeListener + agent.run_once birlikte.

menubar/__main__ wiring'inin dayandığı yolu doğrular (UI'sız): manuel "Şimdi Kaydet"
ile wake-word olmadan tam tur, ve kayıt sürerken debounce. IO (record/process/say)
mock'lu; wake modeli ve ses akışı sahte.
"""
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

from arda.agent import AgentDeps, run_once
from arda.models import AgentState, ProcessResult
from arda.state import Controls
from arda.wake import WakeListener

WAV = Path("/tmp/x.wav")


@contextmanager
def _fake_stream():
    class _S:
        def read(self, n):
            return np.zeros((n, 1), dtype="int16"), False
    yield _S()


def _wire(controls, listener):
    """__main__._build_agent_deps ile aynı bağlama (IO mock)."""
    return AgentDeps(
        wait_for_wake=lambda: listener.wait_for_wake(
            stream_factory=_fake_stream,
            triggered=controls.consume_manual,
            wake_on=controls.wake_enabled,
            stop=lambda: False,
        ),
        record=MagicMock(return_value=WAV),
        process=MagicMock(return_value=ProcessResult(status="saved", message="ORS notu kaydedildi.")),
        say=MagicMock(),
        cleanup=MagicMock(),
    )


def test_manual_trigger_drives_full_cycle_without_wake_word():
    # Wake kapalı: model asla tetiklenmez; sadece manuel tetik kayda geçirmeli.
    controls = Controls(wake_enabled=False)
    model = MagicMock()
    model.predict.return_value = {"arda": 0.99}  # eşik üstü AMA wake kapalı → kullanılmamalı
    listener = WakeListener({"wake": {"threshold": 0.5}}, model=model)
    deps = _wire(controls, listener)
    seen = []

    assert controls.request_manual() is True   # UI "Şimdi Kaydet"
    result = run_once(deps, ack="Efendim?", on_event=seen.append)

    assert result.status == "saved"
    deps.record.assert_called_once()
    model.predict.assert_not_called()          # wake kapalı → STT/predict yok
    states = [e.state for e in seen]
    assert states[0] is AgentState.LISTENING
    assert AgentState.RECORDING in states
    assert states[-1] is AgentState.DONE       # dinliyor → … → yazıldı


def test_trigger_is_debounced_while_busy():
    controls = Controls()
    controls.set_busy_for(AgentState.RECORDING)   # kayıt sürüyor
    assert controls.request_manual() is False     # tekrar tetik yok sayılır
    controls.set_busy_for(AgentState.DONE)         # bitince
    assert controls.request_manual() is True
