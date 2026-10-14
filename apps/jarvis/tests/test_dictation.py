"""Dikte (sesli yazma) birim testleri — saf mantık, yan etkiler mock'lanır."""
import queue as _queue
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from arda.dictation import (
    clean_text,
    dictate_once,
    insert_text,
    make_dictation_transcriber,
    run_dictation_cycle,
)
from arda.transcribe import Transcriber


class _FakeTranscriber:
    def __init__(self, text):
        self._text = text

    def transcribe(self, wav):
        return self._text


def test_clean_text_trims_and_collapses_whitespace():
    assert clean_text("  merhaba   dünya  ") == "Merhaba dünya"


def test_clean_text_capitalizes_first_letter():
    assert clean_text("bugün hava güzel") == "Bugün hava güzel"


def test_clean_text_drops_leading_filler_words():
    assert clean_text("ee yani bir not al") == "Bir not al"


def test_clean_text_fixes_space_before_punctuation():
    assert clean_text("tamam , devam et .") == "Tamam, devam et."


def test_clean_text_empty_or_whitespace_returns_empty():
    assert clean_text("   ") == ""
    assert clean_text("") == ""


def test_clean_text_none_mode_returns_raw_stripped():
    assert clean_text("  ee yani test  ", mode="none") == "ee yani test"


def _ok_run(calls):
    def _run(args, **kw):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    return _run


def test_insert_text_empty_is_noop():
    calls = []
    assert insert_text("", run=_ok_run(calls)) is False
    assert calls == []


def test_insert_text_pastes_and_restores_clipboard():
    sets = []
    calls = []
    ok = insert_text(
        "Merhaba",
        clip_get=lambda: "ESKİ",
        clip_set=lambda t: sets.append(t),
        run=_ok_run(calls),
        sleep=lambda *_: None,
    )
    assert ok is True
    # önce yeni metin panoya, sonra eski pano geri yüklenir
    assert sets == ["Merhaba", "ESKİ"]
    # ⌘V osascript ile gönderildi
    assert any("osascript" in a[0] for a in calls)


def test_insert_text_paste_fail_preserves_new_text():
    sets = []

    def _fail_run(args, **kw):
        return SimpleNamespace(returncode=1, stdout="", stderr="izin yok")

    ok = insert_text(
        "Merhaba",
        clip_get=lambda: "ESKİ",
        clip_set=lambda t: sets.append(t),
        run=_fail_run,
        sleep=lambda *_: None,
    )
    assert ok is False
    # yapıştırma başarısız → eski pano GERİ YÜKLENMEZ, metin panoda kalır (elle ⌘V)
    assert sets == ["Merhaba"]


def test_insert_text_paste_timeout_returns_false():
    sets = []

    def _timeout_run(args, **kw):
        raise subprocess.TimeoutExpired(args, kw.get("timeout", 1))

    ok = insert_text(
        "Merhaba",
        clip_get=lambda: "ESKİ",
        clip_set=lambda t: sets.append(t),
        run=_timeout_run,
        sleep=lambda *_: None,
        paste_timeout=0.01,
    )
    assert ok is False
    assert sets == ["Merhaba"]                    # metin panoda kalır, eski pano geri yüklenmez


def test_dictate_once_records_transcribes_and_cleans():
    states = []
    cfg = {"dictation": {"cleanup": "light"}}
    out = dictate_once(
        cfg,
        _FakeTranscriber("ee bugün hava güzel"),
        record=lambda c, should_stop=None: Path("/tmp/x.wav"),
        on_state=states.append,
    )
    assert out == "Bugün hava güzel"
    assert states == ["listening", "transcribing"]


def test_dictate_once_empty_transcript_returns_empty():
    out = dictate_once(
        {"dictation": {}},
        _FakeTranscriber("   "),
        record=lambda c, should_stop=None: Path("/tmp/x.wav"),
    )
    assert out == ""


def test_dictate_once_passes_should_stop_to_record():
    seen = {}
    sentinel = lambda: True  # noqa: E731

    def _rec(c, should_stop=None):
        seen["should_stop"] = should_stop
        return Path("/tmp/x.wav")

    dictate_once(
        {"dictation": {}}, _FakeTranscriber("x"),
        record=_rec, should_stop=sentinel,
    )
    assert seen["should_stop"] is sentinel


def test_cycle_threads_should_stop_to_dictate():
    seen = {}
    sentinel = lambda: True  # noqa: E731

    def _dictate(cfg, t, on_state=None, should_stop=None):
        seen["should_stop"] = should_stop
        return "Merhaba"

    run_dictation_cycle(
        {}, object(), lambda s: None,
        should_stop=sentinel, dictate=_dictate, insert=lambda text: True,
    )
    assert seen["should_stop"] is sentinel


def test_cycle_transcribe_timeout_sets_error():
    states = []
    never = threading.Event()

    class _SlowTranscriber:
        def transcribe(self, wav):
            never.wait(1.0)
            return "geç dönen metin"

    with pytest.raises(TimeoutError):
        run_dictation_cycle(
            {"dictation": {"transcribe_timeout_sec": 0.01}},
            _SlowTranscriber(),
            states.append,
            dictate=lambda cfg, t, on_state=None, should_stop=None: dictate_once(
                cfg, t, record=lambda c, should_stop=None: Path("/tmp/x.wav"), on_state=on_state
            ),
            insert=lambda text: True,
        )
    assert states == ["listening", "transcribing", "error"]


def test_cycle_pastes_and_sets_done():
    states = []
    pasted = []
    out = run_dictation_cycle(
        {}, object(), states.append,
        dictate=lambda cfg, t, on_state=None, should_stop=None: "Merhaba",
        insert=lambda text: pasted.append(text) or True,
    )
    assert out == "Merhaba"
    assert pasted == ["Merhaba"]
    assert states[-2:] == ["pasting", "done"]
    assert states[-1] == "done"


def test_cycle_empty_text_goes_idle_no_paste():
    states = []
    pasted = []
    out = run_dictation_cycle(
        {}, object(), states.append,
        dictate=lambda cfg, t, on_state=None, should_stop=None: "",
        insert=lambda text: pasted.append(text) or True,
    )
    assert out == ""
    assert pasted == []
    assert states[-1] == "idle"


def test_cycle_paste_failure_sets_error():
    states = []
    out = run_dictation_cycle(
        {}, object(), states.append,
        dictate=lambda cfg, t, on_state=None, should_stop=None: "Merhaba",
        insert=lambda text: False,
    )
    assert out == ""
    assert states[-2:] == ["pasting", "error"]


def test_cycle_insert_exception_sets_error():
    states = []

    def _boom(text):
        raise RuntimeError("osascript takıldı")

    with pytest.raises(RuntimeError):
        run_dictation_cycle(
            {}, object(), states.append,
            dictate=lambda cfg, t, on_state=None, should_stop=None: "Merhaba",
            insert=_boom,
        )
    assert states[-2:] == ["pasting", "error"]


def test_dictation_transcriber_is_whisper_with_auto_language():
    cfg = {"dictation": {"language": "auto"}, "stt": {"backend": "parakeet"}}
    t = make_dictation_transcriber(cfg)
    assert isinstance(t, Transcriber)          # parakeet değil → whisper'a zorlandı
    assert t._language is None                  # auto → dil sabitlenmez


def test_dictation_transcriber_explicit_language():
    t = make_dictation_transcriber({"dictation": {"language": "tr"}})
    assert t._language == "tr"


def test_dictation_worker_runs_one_cycle(monkeypatch):
    from arda import menubar

    q = _queue.Queue()

    class _C:
        def __init__(self):
            self._n = 0
            self.began = 0
            self.ended = 0

        def wait_for_dictation(self, timeout=None):
            self._n += 1
            return self._n == 1            # ilk çağrı tetik, sonra dur

        def begin_dictation(self):
            self.began += 1

        def end_dictation(self):
            self.ended += 1

        def dictation_should_stop(self):
            return False

    captured = {}

    def _fake_cycle(cfg, transcriber, put, should_stop=None):
        captured["called"] = True
        captured["should_stop"] = should_stop
        put("done")
        raise SystemExit                    # worker döngüsünü temiz kes (Exception değil → except'i atlar)

    monkeypatch.setattr(menubar.dictation, "run_dictation_cycle", _fake_cycle)
    c = _C()
    try:
        menubar._dictation_worker(controls=c, cfg={}, transcriber=object(), overlay_q=q)
    except SystemExit:
        pass
    assert captured.get("called") is True
    assert callable(captured.get("should_stop"))   # should_stop worker'dan cycle'a geçti
    assert c.began == 1 and c.ended == 1           # begin/end (finally) çağrıldı
    assert q.get_nowait() == "done"
