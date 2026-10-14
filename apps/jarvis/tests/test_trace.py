import logging
from pathlib import Path
from unittest.mock import MagicMock

from arda.models import AgentEvent, Intent
from arda.pipeline import Deps, process_once
from arda.trace import trace_scope


def test_pipeline_trace_has_shared_id_and_core_stages(caplog):
    intent = Intent.from_dict({
        "intent": "answered", "category": "chat", "title": "", "clean_text": "Merhaba",
        "tags": [], "follow_up": [],
    })
    observed = []
    events = []
    deps = Deps(
        transcribe=MagicMock(return_value="Merhaba"),
        classify=MagicMock(return_value=intent),
        write_note=MagicMock(), append_task=MagicMock(),
        save_to_inbox=MagicMock(), say=MagicMock(),
        answer=MagicMock(return_value="Merhaba, nasılsın?"),
        observe=observed.append,
    )

    with caplog.at_level(logging.INFO, logger="arda.trace"), trace_scope("trace-test"):
        result = process_once(Path("/tmp/test.wav"), deps=deps, on_event=events.append)

    assert result.status == "answered"
    messages = [record.getMessage() for record in caplog.records if "[trace=trace-test]" in record.getMessage()]
    assert any("stage=stt.transcription status=completed" in message for message in messages)
    assert any("stage=llm.intent status=completed" in message for message in messages)
    assert any("stage=llm.response status=completed" in message for message in messages)
    assert any("duration_ms=" in message for message in messages)
    assert observed[0]["trace_id"] == "trace-test"
    assert events and all(event.trace_id == "trace-test" for event in events)


def test_trace_records_stage_failure_with_same_id(caplog):
    deps = Deps(
        transcribe=MagicMock(side_effect=RuntimeError("vosk unavailable")),
        classify=MagicMock(),
        write_note=MagicMock(), append_task=MagicMock(),
        save_to_inbox=MagicMock(return_value=Path("/tmp/inbox.md")), say=MagicMock(),
    )

    with caplog.at_level(logging.INFO, logger="arda.trace"), trace_scope("trace-failure"):
        result = process_once(Path("/tmp/test.wav"), deps=deps)

    assert result.status == "inbox"
    failure = [record.getMessage() for record in caplog.records
               if "[trace=trace-failure]" in record.getMessage()
               and "stage=stt.transcription status=failed" in record.getMessage()]
    assert failure
    assert "duration_ms=" in failure[0]
