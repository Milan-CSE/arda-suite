"""AgentState enum + thread-safe Controls testleri (saf mantık)."""
from arda.state import AgentState, Controls


class TestAgentState:
    def test_has_all_lifecycle_states(self):
        names = {s.name for s in AgentState}
        assert names == {
            "LISTENING", "WOKE", "RECORDING", "TRANSCRIBING",
            "THINKING", "DONE", "ERROR",
        }

    def test_is_str_valued(self):
        assert AgentState.RECORDING == "recording"


class TestControlsManualTrigger:
    def test_request_then_consume(self):
        c = Controls()
        assert c.request_manual() is True
        assert c.consume_manual() is True
        assert c.consume_manual() is False  # tek seferlik

    def test_consume_without_request_is_false(self):
        assert Controls().consume_manual() is False

    def test_debounce_rejects_while_busy(self):
        c = Controls()
        c.set_busy_for(AgentState.RECORDING)
        assert c.request_manual() is False     # meşgulken reddet
        assert c.consume_manual() is False     # hiç set edilmedi

    def test_accepts_again_after_done(self):
        c = Controls()
        c.set_busy_for(AgentState.RECORDING)
        assert c.request_manual() is False
        c.set_busy_for(AgentState.DONE)        # meşgul bitti
        assert c.request_manual() is True

    def test_listening_is_not_busy(self):
        c = Controls()
        c.set_busy_for(AgentState.LISTENING)
        assert c.is_busy() is False
        assert c.request_manual() is True


class TestControlsBlockingWait:
    def test_wait_for_manual_true_when_already_requested(self):
        c = Controls()
        c.request_manual()
        assert c.wait_for_manual(timeout=0.1) is True
        assert c.consume_manual() is False  # bekleme tetiği tüketti

    def test_wait_for_manual_times_out_without_request(self):
        # Tetik yokken mic AÇMADAN sadece bloklar; süre dolunca False.
        assert Controls().wait_for_manual(timeout=0.01) is False

    def test_shutdown_flag_is_visible_to_daemon_loop(self):
        controls = Controls()
        assert controls.is_shutdown() is False
        controls.request_shutdown()
        assert controls.is_shutdown() is True


class TestControlsWakeToggle:
    def test_default_enabled(self):
        assert Controls().wake_enabled() is True

    def test_can_start_disabled(self):
        assert Controls(wake_enabled=False).wake_enabled() is False

    def test_toggle(self):
        c = Controls()
        c.set_wake(False)
        assert c.wake_enabled() is False
        c.set_wake(True)
        assert c.wake_enabled() is True


class TestControlsCancellation:
    """Çok-adımlı agent koşusunu durdurma sinyali (executor.should_stop'a bağlanır)."""

    def test_not_cancelled_by_default(self):
        assert Controls().is_cancelled() is False

    def test_request_cancel_sets_flag(self):
        c = Controls()
        c.request_cancel()
        assert c.is_cancelled() is True

    def test_clear_cancel_resets(self):
        c = Controls()
        c.request_cancel()
        c.clear_cancel()
        assert c.is_cancelled() is False


class TestControlsDictation:
    """Dikte (amblem / ⌘⇧D) tetiği — thread-safe, manuel tetikten ayrı."""

    def test_request_and_consume_dictation(self):
        c = Controls()
        assert c.consume_dictation() is False      # başta bekleyen yok
        assert c.request_dictation() is True
        assert c.consume_dictation() is True        # tüketildi
        assert c.consume_dictation() is False       # tekrar yok

    def test_wait_for_dictation_times_out_without_trigger(self):
        assert Controls().wait_for_dictation(timeout=0.01) is False

    def test_wait_for_dictation_returns_true_after_request(self):
        c = Controls()
        c.request_dictation()
        assert c.wait_for_dictation(timeout=0.5) is True


class TestControlsDictationToggle:
    """⌘⇧D / amblem tıklaması TOGGLE: aktif turda ikinci tetik yeni tur BAŞLATMAZ, DURDURUR."""

    def test_begin_marks_active_and_clears_stop(self):
        c = Controls()
        assert c.is_dictating() is False
        c.begin_dictation()
        assert c.is_dictating() is True
        assert c.dictation_should_stop() is False

    def test_request_while_active_signals_stop_not_new_turn(self):
        c = Controls()
        c.begin_dictation()          # tur aktif (kayıt sürüyor)
        assert c.request_dictation() is True
        assert c.dictation_should_stop() is True          # durdur sinyali kondu
        assert c.wait_for_dictation(timeout=0.01) is False  # YENİ tur kuyruğa alınmadı

    def test_request_while_idle_starts_new_turn(self):
        c = Controls()
        assert c.request_dictation() is True
        assert c.dictation_should_stop() is False
        assert c.wait_for_dictation(timeout=0.5) is True    # yeni tur tetiklendi

    def test_end_clears_active_and_stop(self):
        c = Controls()
        c.begin_dictation()
        c.request_dictation()        # stop kondu
        c.end_dictation()
        assert c.is_dictating() is False
        assert c.dictation_should_stop() is False
