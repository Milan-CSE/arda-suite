"""agent loop testleri — tüm bağımlılıklar mock'lu (donanım/model yok)."""
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

from arda.agent import _ERROR_BACKOFF_SEC, AgentDeps, run_forever, run_once
from arda.models import AgentEvent, AgentState, ProcessResult

WAV = Path("/tmp/a.wav")
RESULT = ProcessResult(status="saved", message="ok")


def _deps(**overrides) -> AgentDeps:
    base = dict(
        wait_for_wake=MagicMock(return_value=True),
        record=MagicMock(return_value=WAV),
        process=MagicMock(return_value=RESULT),
        say=MagicMock(),
        cleanup=MagicMock(),
    )
    base.update(overrides)
    return AgentDeps(**base)


class TestRunOnce:
    def test_full_cycle_on_wake(self):
        deps = _deps()
        result = run_once(deps, ack="Efendim?")
        assert result is RESULT
        deps.say.assert_called_once_with("Efendim?")
        deps.record.assert_called_once()
        deps.process.assert_called_once_with(WAV, ANY)  # (wav, on_event)
        deps.cleanup.assert_called_once_with(WAV)

    def test_no_wake_does_nothing(self):
        deps = _deps(wait_for_wake=MagicMock(return_value=False))
        result = run_once(deps, ack="Efendim?")
        assert result is None
        deps.record.assert_not_called()
        deps.process.assert_not_called()
        deps.say.assert_not_called()

    def test_cleanup_runs_even_if_process_raises(self):
        deps = _deps(process=MagicMock(side_effect=RuntimeError("boom")))
        try:
            run_once(deps, ack="x")
        except RuntimeError:
            pass
        deps.cleanup.assert_called_once_with(WAV)

    def test_say_failure_does_not_block_recording(self):
        """Sesli onay (ack) hata verse bile kayıt/işleme devam etmeli."""
        deps = _deps(say=MagicMock(side_effect=RuntimeError("ses yok")))
        result = run_once(deps, ack="Efendim?")
        assert result is RESULT
        deps.record.assert_called_once()


class TestRunOnceEvents:
    def test_emits_states_in_order(self):
        """LISTENING→WOKE→RECORDING→(process: TRANSCRIBING,THINKING)→DONE."""
        seen = []

        def proc(wav, on_event):
            on_event(AgentEvent(state=AgentState.TRANSCRIBING, source="pipeline"))
            on_event(AgentEvent(state=AgentState.THINKING, source="pipeline"))
            return RESULT

        run_once(_deps(process=proc), ack="x", on_event=seen.append)
        assert [e.state for e in seen] == [
            AgentState.LISTENING, AgentState.WOKE, AgentState.RECORDING,
            AgentState.TRANSCRIBING, AgentState.THINKING, AgentState.DONE,
        ]

    def test_done_event_carries_result_message(self):
        seen = []
        result = ProcessResult(status="task", message="Görev eklendi: X")
        run_once(_deps(process=MagicMock(return_value=result)), ack="x", on_event=seen.append)
        assert seen[-1].state is AgentState.DONE
        assert seen[-1].message == "Görev eklendi: X"

    def test_failed_status_emits_error(self):
        seen = []
        failed = ProcessResult(status="failed", message="kayıt yapılamadı")
        run_once(_deps(process=MagicMock(return_value=failed)), ack="x", on_event=seen.append)
        assert seen[-1].state is AgentState.ERROR

    def test_no_wake_emits_only_listening(self):
        seen = []
        deps = _deps(wait_for_wake=MagicMock(return_value=False))
        run_once(deps, ack="x", on_event=seen.append)
        assert [e.state for e in seen] == [AgentState.LISTENING]


class TestRunForever:
    def test_runs_until_stop(self):
        deps = _deps()
        calls = {"n": 0}

        def _stop():
            calls["n"] += 1
            return calls["n"] > 3  # 3 tur sonra dur

        run_forever(deps, ack="x", stop=_stop)
        assert deps.process.call_count == 3

    def test_stops_when_wake_signals_shutdown(self):
        deps = _deps(wait_for_wake=MagicMock(return_value=False))
        run_forever(deps, ack="x")
        deps.process.assert_not_called()

    def test_survives_iteration_error(self):
        """Bir turda record patlasa bile daemon ölmemeli, sonraki tura geçmeli."""
        deps = _deps(record=MagicMock(side_effect=[RuntimeError("mic"), WAV]))
        calls = {"n": 0}

        def _stop():
            calls["n"] += 1
            return calls["n"] > 2  # 2 tur

        with patch("arda.agent.time.sleep"):  # backoff'u hızlandır
            run_forever(deps, ack="x", stop=_stop)
        assert deps.record.call_count == 2  # ilk hata yutuldu, ikinci tur çalıştı

    def test_backoff_sleeps_on_error_to_avoid_cpu_spin(self):
        """Her turda hata olursa (örn. mic izni yok) döngü CPU'yu yakmamalı."""
        deps = _deps(wait_for_wake=MagicMock(side_effect=RuntimeError("mic izni yok")))
        calls = {"n": 0}

        def _stop():
            calls["n"] += 1
            return calls["n"] > 1  # 1 hatalı tur, sonra dur

        with patch("arda.agent.time.sleep") as mock_sleep:
            run_forever(deps, ack="x", stop=_stop)
        mock_sleep.assert_called_once_with(_ERROR_BACKOFF_SEC)

    def test_emits_error_event_on_exception(self):
        seen = []
        deps = _deps(wait_for_wake=MagicMock(side_effect=RuntimeError("patladı")))
        calls = {"n": 0}

        def _stop():
            calls["n"] += 1
            return calls["n"] > 1

        with patch("arda.agent.time.sleep"):
            run_forever(deps, ack="x", stop=_stop, on_event=seen.append)
        assert any(e.state is AgentState.ERROR for e in seen)


class TestContinuousSession:
    """Sürekli sohbet: cevaptan sonra takip turları (ack'siz); sessizlik/'kapat kendini'/
    stop ile standby. Turlar wait_for_wake side_effect [True, False] ile sınırlanır."""

    FOLLOW_WAV = Path("/tmp/f.wav")
    ENDED = ProcessResult(status="ended", message="")

    def test_follow_up_turn_runs_without_ack(self):
        deps = _deps(
            wait_for_wake=MagicMock(side_effect=[True, False]),       # 1 cold tur, sonra kapat
            follow_up=MagicMock(side_effect=[self.FOLLOW_WAV, None]),  # 1 takip, sonra sessizlik
        )
        run_forever(deps, ack="Efendim?")
        assert deps.process.call_count == 2                 # cold + 1 takip turu
        deps.say.assert_called_once_with("Efendim?")        # ack yalnız cold turda

    def test_silence_returns_to_standby(self):
        deps = _deps(
            wait_for_wake=MagicMock(side_effect=[True, False]),
            follow_up=MagicMock(return_value=None),           # hiç konuşma yok
        )
        run_forever(deps, ack="x")
        assert deps.process.call_count == 1                 # ek tur yok → standby

    def test_stop_phrase_says_goodbye_and_ends_session(self):
        deps = _deps(
            wait_for_wake=MagicMock(side_effect=[True, False]),
            process=MagicMock(side_effect=[RESULT, self.ENDED]),  # cold normal, takip 'ended'
            follow_up=MagicMock(side_effect=[self.FOLLOW_WAV, self.FOLLOW_WAV]),
        )
        run_forever(deps, ack="x", goodbye="Görüşürüz.")
        deps.say.assert_any_call("Görüşürüz.")
        assert deps.follow_up.call_count == 1               # 'ended' sonrası takip durur

    def test_cold_stop_phrase_skips_follow_up(self):
        follow = MagicMock()
        deps = _deps(
            wait_for_wake=MagicMock(side_effect=[True, False]),
            process=MagicMock(return_value=self.ENDED),       # cold turun kendisi bitirme ifadesi
            follow_up=follow,
        )
        run_forever(deps, ack="x", goodbye="Bay.")
        follow.assert_not_called()                          # 'ended' → takip oturumu açılmaz
        deps.say.assert_any_call("Bay.")

    def test_default_no_follow_up_is_single_turn(self):
        # follow_up verilmezse (default) eski tek-tur döngüsü birebir korunur.
        deps = _deps(wait_for_wake=MagicMock(side_effect=[True, False]))
        run_forever(deps, ack="x")
        assert deps.process.call_count == 1


class TestInterruptResetPerTurn:
    """Her tur barge-in bayrağını SIFIRDAN başlatır.

    Bayrak tur boyunca yaşadığı için (say() silmez), yeni tur başında temizlenmezse
    bir kez araya girdikten sonra asistan BİR DAHA HİÇ konuşmaz — sessiz kalma tuzağı."""

    def teardown_method(self):
        from arda import speak
        speak.reset_interrupt()

    def test_previous_barge_in_does_not_silence_next_turn(self):
        from arda import speak

        speak.interrupt()                      # önceki turda kullanıcı araya girdi
        assert speak.was_interrupted() is True

        seen: list[bool] = []
        deps = _deps(process=MagicMock(
            side_effect=lambda wav, on_event: (seen.append(speak.was_interrupted()), RESULT)[1]
        ))
        run_once(deps, ack="Efendim?")

        assert seen == [False]   # tur işleme BAŞLARKEN bayrak temiz

    def test_reset_happens_before_barge_thread_starts(self):
        """Sıfırlama barge-in dinleyicisinden ÖNCE olmalı; sonra olsa taze kesmeyi silerdi."""
        from arda import speak

        order: list[str] = []

        def _barge():
            order.append("barge")
            return None

        speak.interrupt()
        deps = _deps(
            barge_in=_barge,
            process=MagicMock(side_effect=lambda wav, on_event: (
                order.append(f"process:interrupted={speak.was_interrupted()}"), RESULT)[1]),
        )
        run_once(deps, ack="Efendim?")
        assert "process:interrupted=False" in order
