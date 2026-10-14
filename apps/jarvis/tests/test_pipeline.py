import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from arda.confirm import PendingConfirmation, PendingStore
from arda.models import Intent, ProcessResult
from arda import speak
from arda.pipeline import _ACKS, Deps, process_once
from arda.research import _SPOKEN_MAX
from arda.state import AgentState


def _spoken(say) -> list[str]:
    """say() çağrılarından kısa onay (ack) ifadelerini çıkar → yalnız yönlendirme/cevap
    metni kalır. Her komutta niyet anlaşılınca bir ack söylenir (pipeline._ACKS); testler
    asıl konuşulan içeriği bundan bağımsız doğrular."""
    return [c.args[0] for c in say.call_args_list if c.args and c.args[0] not in _ACKS]

_INTENT_DATA = {
    "intent": "note",
    "category": "internship",
    "title": "ORS Staj Notu",
    "clean_text": "Üretim hattı incelendi.",
    "tags": ["staj", "ORS"],
    "follow_up": [],
}
NOTE_INTENT = Intent.from_dict(_INTENT_DATA)
TASK_INTENT = Intent.from_dict(dict(_INTENT_DATA, intent="task", title="Toplantı hazırlığı"))
ANSWERED_INTENT = Intent.from_dict(dict(_INTENT_DATA, intent="answered", clean_text="Saat 14:00."))
UNKNOWN_INTENT = Intent.from_dict(dict(_INTENT_DATA, intent="research"))

WAV = Path("/tmp/test.wav")
NOTE_PATH = Path("/vault/Internship/ors-staj-notu.md")
TASK_PATH = Path("/vault/Tasks.md")
INBOX_PATH = Path("/vault/Inbox/inbox-2026-06-18.md")


def _deps(**overrides) -> Deps:
    base = dict(
        transcribe=MagicMock(return_value="ham transkripsiyon"),
        classify=MagicMock(return_value=NOTE_INTENT),
        write_note=MagicMock(return_value=NOTE_PATH),
        append_task=MagicMock(return_value=TASK_PATH),
        save_to_inbox=MagicMock(return_value=INBOX_PATH),
        say=MagicMock(),
    )
    base.update(overrides)
    return Deps(**base)


class TestNoteRouting:
    def test_note_intent_returns_saved(self):
        result = process_once(WAV, deps=_deps())
        assert result.status == "saved"
        assert result.path == NOTE_PATH
        assert result.intent == NOTE_INTENT

    def test_note_intent_calls_write_note(self):
        deps = _deps()
        process_once(WAV, deps=deps)
        deps.write_note.assert_called_once()

    def test_note_intent_calls_say(self):
        deps = _deps()
        process_once(WAV, deps=deps)
        assert _spoken(deps.say) == ["ORS Staj Notu kaydedildi."]


class TestTaskRouting:
    def test_task_intent_returns_task(self):
        result = process_once(WAV, deps=_deps(classify=MagicMock(return_value=TASK_INTENT)))
        assert result.status == "task"
        assert result.path == TASK_PATH

    def test_task_intent_calls_append_task(self):
        deps = _deps(classify=MagicMock(return_value=TASK_INTENT))
        process_once(WAV, deps=deps)
        deps.append_task.assert_called_once()


class TestAnsweredRouting:
    def test_answered_intent_returns_answered(self):
        result = process_once(WAV, deps=_deps(classify=MagicMock(return_value=ANSWERED_INTENT)))
        assert result.status == "answered"
        assert result.path is None

    def test_answered_message_is_clean_text(self):
        # answer dep yoksa router'ın clean_text'ine düşülür (geriye uyumlu).
        result = process_once(WAV, deps=_deps(classify=MagicMock(return_value=ANSWERED_INTENT)))
        assert result.message == "Saat 14:00."


class TestAnswerEngineSeparation:
    """answered → cevap router'dan DEĞİL, ayrı serbest motordan gelir (issue #1)."""

    def test_answered_uses_free_answer_engine(self):
        answer = MagicMock(return_value="Tam olarak 14:00, dersine yetişirsin.")
        deps = _deps(classify=MagicMock(return_value=ANSWERED_INTENT), answer=answer)
        result = process_once(WAV, deps=deps)
        assert result.message == "Tam olarak 14:00, dersine yetişirsin."  # router metni DEĞİL
        answer.assert_called_once_with("ham transkripsiyon", memories=())

    def test_answer_engine_failure_falls_back_to_router_text(self):
        deps = _deps(
            classify=MagicMock(return_value=ANSWERED_INTENT),
            answer=MagicMock(side_effect=RuntimeError("ollama down")),
        )
        result = process_once(WAV, deps=deps)
        assert result.status == "answered"
        assert result.message == "Saat 14:00."   # serbest motor patladı → router cevabı

    def test_empty_answer_falls_back_to_router_text(self):
        deps = _deps(classify=MagicMock(return_value=ANSWERED_INTENT), answer=MagicMock(return_value=""))
        result = process_once(WAV, deps=deps)
        assert result.message == "Saat 14:00."


ACTION_INTENT = Intent.from_dict(
    dict(_INTENT_DATA, intent="action", action="open_app", params={"name": "Safari"})
)


class TestActionRouting:
    def test_action_intent_calls_do_action(self):
        do_action = MagicMock(return_value="Safari açılıyor.")
        deps = _deps(classify=MagicMock(return_value=ACTION_INTENT), do_action=do_action)
        result = process_once(WAV, deps=deps)
        assert result.status == "action"
        assert result.message == "Safari açılıyor."
        do_action.assert_called_once_with("open_app", {"name": "Safari"})

    def test_action_intent_speaks_confirmation(self):
        deps = _deps(
            classify=MagicMock(return_value=ACTION_INTENT),
            do_action=MagicMock(return_value="Safari açılıyor."),
        )
        process_once(WAV, deps=deps)
        assert _spoken(deps.say) == ["Safari açılıyor."]

    def test_action_without_do_action_falls_to_inbox(self):
        # do_action enjekte edilmemişse (eski wiring) eylem güvenle inbox'a düşer.
        result = process_once(WAV, deps=_deps(classify=MagicMock(return_value=ACTION_INTENT)))
        assert result.status == "inbox"

    def test_agent_intent_with_action_name_routes_to_action(self):
        # Model 'intent: agent' dönse bile action geçerli bir macOS eylemiyse action dalına yönlendirilir
        confused_intent = Intent.from_dict(
            dict(_INTENT_DATA, intent="agent", action="lock_screen", params={})
        )
        do_action = MagicMock(return_value="Ekranı kapatıyorum.")
        deps = _deps(classify=MagicMock(return_value=confused_intent), do_action=do_action)
        result = process_once(WAV, deps=deps)
        assert result.status == "action"
        assert result.message == "Ekranı kapatıyorum."
        do_action.assert_called_once_with("lock_screen", {})


class TestLowConfidenceDeferral:
    """Düşük güvenli ACTION sessizce çalıştırılmaz: yapılmaz + sesli haber + inbox (veri
    güvenli), status='unclear'. (Tam 'X mi demek istedin?' diyalogu pending-state → Batch 3.)"""

    def _low_conf_action(self):
        return Intent.from_dict(dict(
            _INTENT_DATA, intent="action", action="open_app",
            params={"name": "Safari"}, confidence="low",
        ))

    def test_low_confidence_action_is_not_executed(self):
        do_action = MagicMock(return_value="Safari açılıyor.")
        deps = _deps(classify=MagicMock(return_value=self._low_conf_action()), do_action=do_action)
        result = process_once(WAV, deps=deps)
        assert result.status == "unclear"
        do_action.assert_not_called()                 # yan-etkili eylem YAPILMADI
        deps.save_to_inbox.assert_called_once()       # ham metin kaybolmadı

    def test_low_confidence_action_speaks_heads_up(self):
        deps = _deps(classify=MagicMock(return_value=self._low_conf_action()))
        process_once(WAV, deps=deps)
        deps.say.assert_called_once()                 # kullanıcı sessizce bırakılmaz

    def test_low_confidence_action_not_remembered(self):
        remember = MagicMock()
        deps = _deps(classify=MagicMock(return_value=self._low_conf_action()), remember=remember)
        process_once(WAV, deps=deps)
        remember.assert_not_called()                  # belirsiz tur bağlamı kirletmez

    def test_high_confidence_action_runs_normally(self):
        do_action = MagicMock(return_value="Safari açılıyor.")
        deps = _deps(classify=MagicMock(return_value=ACTION_INTENT), do_action=do_action)
        result = process_once(WAV, deps=deps)
        assert result.status == "action"              # high (vars.) → eskisi gibi
        do_action.assert_called_once()


class TestConfirmLowConfidence:
    """pending enjekte edilince düşük-güvenli action sessizce inbox'a düşmez: geri sorar,
    açık 'evet' ile çalışır; 'hayır'/sessizlik/bayatlama → eylem YAPILMAZ (güvenli varsayılan)."""

    def _low_conf_action(self):
        return Intent.from_dict(dict(
            _INTENT_DATA, intent="action", action="open_app",
            params={"name": "Safari"}, confidence="low",
        ))

    def test_low_conf_action_asks_instead_of_inbox(self):
        store = PendingStore()
        do_action = MagicMock(return_value="Safari açılıyor.")
        deps = _deps(classify=MagicMock(return_value=self._low_conf_action()),
                     do_action=do_action, pending=store)
        result = process_once(WAV, deps=deps)
        assert result.status == "clarify"
        do_action.assert_not_called()                  # henüz yapılmadı — soruldu
        deps.save_to_inbox.assert_not_called()         # inbox'a düşmedi
        assert store.peek() is not None and store.peek().action == "open_app"

    def test_yes_reply_executes_pending_action(self):
        store = PendingStore()
        do_action = MagicMock(return_value="Safari açılıyor.")
        deps = _deps(transcribe=MagicMock(side_effect=["safari aç", "evet"]),
                     classify=MagicMock(return_value=self._low_conf_action()),
                     do_action=do_action, pending=store)
        process_once(WAV, deps=deps)                   # tur 1: clarify
        result = process_once(WAV, deps=deps)          # tur 2: "evet"
        assert result.status == "action"
        do_action.assert_called_once_with("open_app", {"name": "Safari"})
        assert store.peek() is None                    # tüketildi

    def test_no_reply_cancels_pending_action(self):
        store = PendingStore()
        do_action = MagicMock(return_value="x")
        deps = _deps(transcribe=MagicMock(side_effect=["safari aç", "hayır"]),
                     classify=MagicMock(return_value=self._low_conf_action()),
                     do_action=do_action, pending=store)
        process_once(WAV, deps=deps)                   # clarify
        result = process_once(WAV, deps=deps)          # "hayır"
        assert result.status == "cancelled"
        do_action.assert_not_called()
        assert store.peek() is None

    def test_unclear_reply_falls_through_to_classify(self):
        store = PendingStore()
        store.set(PendingConfirmation("open_app", {"name": "Safari"}, "x", "Safari aç", time.monotonic()))
        classify = MagicMock(return_value=NOTE_INTENT)
        deps = _deps(transcribe=MagicMock(return_value="aslında not al"),
                     classify=classify, pending=store)
        result = process_once(WAV, deps=deps)
        assert result.status == "saved"                # yeni komut işlendi (note)
        classify.assert_called_once()
        assert store.peek() is None

    def test_stale_pending_is_dropped_then_classified(self):
        store = PendingStore()
        store.set(PendingConfirmation("open_app", {"name": "Safari"}, "x", "x", created_at=0.0))
        deps = _deps(transcribe=MagicMock(return_value="not al"),
                     classify=MagicMock(return_value=NOTE_INTENT), pending=store)
        result = process_once(WAV, deps=deps)
        assert result.status == "saved"                # bayat onay düştü → normal tur
        assert store.peek() is None

    def test_stop_phrase_clears_pending(self):
        store = PendingStore()
        store.set(PendingConfirmation("open_app", {"name": "Safari"}, "x", "x", time.monotonic()))
        deps = _deps(transcribe=MagicMock(return_value="görüşürüz"),
                     is_stop_phrase=lambda t: True, pending=store)
        result = process_once(WAV, deps=deps)
        assert result.status == "ended"
        assert store.peek() is None                    # oturum bitti → pending düştü

    def test_pending_none_keeps_inbox_behavior(self):
        # Özellik kapalı (pending=None) → eski davranış: inbox, status='unclear'.
        do_action = MagicMock(return_value="x")
        deps = _deps(classify=MagicMock(return_value=self._low_conf_action()), do_action=do_action)
        result = process_once(WAV, deps=deps)
        assert result.status == "unclear"
        do_action.assert_not_called()
        deps.save_to_inbox.assert_called_once()


class TestConversationMemory:
    """Başarılı turlar belleğe yazılır; inbox/hata bağlamı kirletmez."""

    def test_remembers_note_turn(self):
        remember = MagicMock()
        deps = _deps(remember=remember)
        process_once(WAV, deps=deps)
        remember.assert_called_once_with("ham transkripsiyon", "ORS Staj Notu kaydedildi.")

    def test_remembers_answered_turn(self):
        remember = MagicMock()
        deps = _deps(classify=MagicMock(return_value=ANSWERED_INTENT), remember=remember)
        process_once(WAV, deps=deps)
        remember.assert_called_once_with("ham transkripsiyon", "Saat 14:00.")

    def test_remembers_action_turn(self):
        remember = MagicMock()
        deps = _deps(
            classify=MagicMock(return_value=ACTION_INTENT),
            do_action=MagicMock(return_value="Safari açılıyor."),
            remember=remember,
        )
        process_once(WAV, deps=deps)
        remember.assert_called_once_with("ham transkripsiyon", "Safari açılıyor.")

    def test_inbox_turn_is_not_remembered(self):
        remember = MagicMock()
        deps = _deps(classify=MagicMock(return_value=UNKNOWN_INTENT), remember=remember)
        process_once(WAV, deps=deps)
        remember.assert_not_called()

    def test_remember_failure_does_not_break_pipeline(self):
        deps = _deps(remember=MagicMock(side_effect=RuntimeError("bellek dolu")))
        result = process_once(WAV, deps=deps)
        assert result.status == "saved"   # bellek kozmetik → kaydı bozmaz

    def test_no_remember_dep_is_safe(self):
        # remember enjekte edilmemişse (eski wiring) tur sessizce atlanır.
        result = process_once(WAV, deps=_deps())
        assert result.status == "saved"


class TestInboxFallback:
    def test_classify_failure_falls_to_inbox(self):
        deps = _deps(classify=MagicMock(side_effect=ValueError("bozuk JSON")))
        result = process_once(WAV, deps=deps)
        assert result.status == "inbox"
        deps.save_to_inbox.assert_called_once()

    def test_write_note_failure_falls_to_inbox(self):
        deps = _deps(write_note=MagicMock(side_effect=OSError("disk dolu")))
        result = process_once(WAV, deps=deps)
        assert result.status == "inbox"
        deps.save_to_inbox.assert_called_once()

    def test_unknown_intent_falls_to_inbox(self):
        result = process_once(WAV, deps=_deps(classify=MagicMock(return_value=UNKNOWN_INTENT)))
        assert result.status == "inbox"

    def test_transcribe_failure_falls_to_inbox(self):
        deps = _deps(transcribe=MagicMock(side_effect=RuntimeError("STT çöktü")))
        result = process_once(WAV, deps=deps)
        assert result.status in ("inbox", "failed")


class TestTtsIsNonFatal:
    """TTS (say) sadece kozmetik geri bildirim — başarılı kaydı geçersiz kılmamalı."""

    def test_say_failure_keeps_saved_status(self):
        deps = _deps(say=MagicMock(side_effect=RuntimeError("ses cihazı meşgul")))
        result = process_once(WAV, deps=deps)
        assert result.status == "saved"
        assert result.path == NOTE_PATH

    def test_say_failure_does_not_trigger_inbox_duplicate(self):
        deps = _deps(say=MagicMock(side_effect=RuntimeError("ses hatası")))
        process_once(WAV, deps=deps)
        deps.write_note.assert_called_once()
        deps.save_to_inbox.assert_not_called()


class TestOnEvent:
    def test_emits_transcribing_then_thinking_on_success(self):
        seen = []
        process_once(WAV, deps=_deps(), on_event=seen.append)
        assert [e.state for e in seen] == [AgentState.TRANSCRIBING, AgentState.THINKING]
        assert all(e.source == "pipeline" for e in seen)

    def test_thinking_not_emitted_when_transcribe_fails(self):
        seen = []
        deps = _deps(transcribe=MagicMock(side_effect=RuntimeError("stt çöktü")))
        process_once(WAV, deps=deps, on_event=seen.append)
        assert [e.state for e in seen] == [AgentState.TRANSCRIBING]  # THINKING'e ulaşmadan düştü

    def test_default_no_event_callback_still_works(self):
        # on_event verilmese de eski davranış birebir korunur
        result = process_once(WAV, deps=_deps())
        assert result.status == "saved"


class TestTotalFailure:
    def test_inbox_also_fails_returns_failed(self):
        deps = _deps(
            classify=MagicMock(side_effect=ValueError("bozuk")),
            save_to_inbox=MagicMock(side_effect=OSError("disk tam dolu")),
        )
        result = process_once(WAV, deps=deps)
        assert result.status == "failed"

    def test_process_once_never_raises(self):
        deps = _deps(
            transcribe=MagicMock(side_effect=RuntimeError("STT çöktü")),
            save_to_inbox=MagicMock(side_effect=OSError("disk")),
        )
        result = process_once(WAV, deps=deps)
        assert isinstance(result, ProcessResult)


class TestActionMemory:
    """ADIM 2: do_action ActionResult döndürürse yapılandırılmış sonuç belleğe yazılır.
    str döndürürse (eski/test) 2 argümanlı remember ile geriye uyumlu kalır."""

    def test_structured_result_passed_to_remember(self):
        from arda.models import ActionResult

        remember = MagicMock()
        ar = {"action": "set_reminder", "success": True, "text": "toplantı"}
        deps = _deps(
            classify=MagicMock(return_value=ACTION_INTENT),
            do_action=MagicMock(return_value=ActionResult("Kurdum.", ar)),
            remember=remember,
        )
        process_once(WAV, deps=deps)
        remember.assert_called_once_with("ham transkripsiyon", "Kurdum.", ar)

    def test_failed_action_records_success_false(self):
        from arda.models import ActionResult

        remember = MagicMock()
        deps = _deps(
            classify=MagicMock(return_value=ACTION_INTENT),
            do_action=MagicMock(return_value=ActionResult("Kuramadım.", {"action": "set_reminder", "success": False})),
            remember=remember,
        )
        process_once(WAV, deps=deps)
        action_result = remember.call_args.args[2]
        assert action_result["success"] is False

    def test_action_result_spoken_message_used(self):
        from arda.models import ActionResult

        deps = _deps(
            classify=MagicMock(return_value=ACTION_INTENT),
            do_action=MagicMock(return_value=ActionResult("Kurdum.", {"action": "set_reminder", "success": True})),
        )
        result = process_once(WAV, deps=deps)
        assert result.message == "Kurdum."
        assert _spoken(deps.say) == ["Kurdum."]

    def test_str_do_action_is_backward_compatible(self):
        # do_action düz str döndürürse remember 2 argümanla çağrılır (mevcut davranış).
        remember = MagicMock()
        deps = _deps(
            classify=MagicMock(return_value=ACTION_INTENT),
            do_action=MagicMock(return_value="Safari açılıyor."),
            remember=remember,
        )
        result = process_once(WAV, deps=deps)
        assert result.message == "Safari açılıyor."
        remember.assert_called_once_with("ham transkripsiyon", "Safari açılıyor.")


RESEARCH_INTENT = Intent.from_dict(dict(_INTENT_DATA, intent="research", clean_text="python nedir"))


class TestResearch:
    """research intent: ara → sentezle → kaynaklı not + kısa sesli özet. Hatalar pipeline'ı düşürmez."""

    def _sources(self):
        from arda.research import Source

        return [Source("Py", "https://python.org", "dil", "tavily")]

    def _deps_research(self, **over):
        src = self._sources()
        base = dict(
            classify=MagicMock(return_value=RESEARCH_INTENT),
            research=MagicMock(return_value=src),
            synthesize=MagicMock(return_value="Python bir dildir [1]."),
            write_research_note=MagicMock(return_value=Path("/vault/Research/python.md")),
        )
        base.update(over)
        return _deps(**base)

    def test_happy_path_speaks_and_saves(self):
        deps = self._deps_research()
        result = process_once(WAV, deps=deps)
        assert result.status == "researched"
        assert result.path == Path("/vault/Research/python.md")
        assert deps.research.call_args.args[0] == "python nedir"   # query = clean_text
        deps.say.assert_called()                                   # sesli özet okundu

    def test_voice_summary_is_capped(self):
        deps = self._deps_research(synthesize=MagicMock(return_value="kelime " * 300))
        result = process_once(WAV, deps=deps)
        assert len(result.message) <= _SPOKEN_MAX                  # tek kaynak (research._SPOKEN_MAX)

    def test_note_write_failure_still_speaks(self):
        deps = self._deps_research(write_research_note=MagicMock(side_effect=OSError("disk")))
        result = process_once(WAV, deps=deps)
        assert result.status == "researched"
        assert result.path is None                                 # not yazılamadı
        deps.say.assert_called()                                   # ama sesli cevap yine döndü

    def test_research_error_speaks_fallback(self):
        deps = self._deps_research(research=MagicMock(side_effect=RuntimeError("ağ yok")))
        result = process_once(WAV, deps=deps)
        assert result.status == "researched"
        assert "araştıramadım" in result.message.lower()

    def test_synthesize_error_speaks_fallback(self):
        deps = self._deps_research(synthesize=MagicMock(side_effect=RuntimeError("ollama")))
        result = process_once(WAV, deps=deps)
        assert "araştıramadım" in result.message.lower()

    def test_empty_results_says_not_found(self):
        deps = self._deps_research(research=MagicMock(return_value=[]))
        result = process_once(WAV, deps=deps)
        assert "bulamadım" in result.message.lower()

    def test_disabled_falls_to_inbox(self):
        # research/synthesize dep'i yoksa → query kaybolmasın, inbox'a düşer.
        deps = _deps(classify=MagicMock(return_value=RESEARCH_INTENT))
        result = process_once(WAV, deps=deps)
        assert result.status == "inbox"
        deps.save_to_inbox.assert_called_once()


AGENT_INTENT = Intent.from_dict(
    dict(_INTENT_DATA, intent="agent", clean_text="bu projeyi incele")
)


class TestAgentRouting:
    """Yeni 'agent' intent'i: çok-adımlı bilgisayar/proje komutu run_agent'a yönlenir.
    Mevcut note/task/answered/action/research dalları DEĞİŞMEDEN kalır."""

    def _run_agent(self, spoken="3 adım çalıştı.", status="done"):
        from arda.agent_models import AgentRunResult

        return MagicMock(return_value=AgentRunResult(status=status, spoken=spoken))

    def test_agent_intent_calls_run_agent_with_request(self):
        run_agent = self._run_agent()
        deps = _deps(classify=MagicMock(return_value=AGENT_INTENT), run_agent=run_agent)
        result = process_once(WAV, deps=deps)
        assert result.status == "agent"
        assert result.message == "3 adım çalıştı."
        run_agent.assert_called_once()
        assert run_agent.call_args.args[0] == "bu projeyi incele"  # clean_text = istek
        assert callable(run_agent.call_args.args[1])               # on_event geçildi (canlı ilerleme)

    def test_explicit_skill_selector_survives_router_clean_text(self):
        run_agent = self._run_agent()
        raw = "skill:meeting_notes_extractor\nToplantıyı özetle"
        result = process_once(WAV, deps=_deps(
            classify=MagicMock(return_value=Intent.from_dict(dict(_INTENT_DATA, intent="agent", clean_text="Toplantıyı özetle"))),
            run_agent=run_agent,
            transcribe=MagicMock(return_value=raw),
        ))
        assert result.status == "agent"
        assert run_agent.call_args.args[0] == raw

    def test_agent_intent_speaks_summary(self):
        deps = _deps(classify=MagicMock(return_value=AGENT_INTENT), run_agent=self._run_agent("özet"))
        process_once(WAV, deps=deps)
        assert _spoken(deps.say) == ["özet"]

    def test_agent_turn_is_remembered(self):
        remember = MagicMock()
        deps = _deps(
            classify=MagicMock(return_value=AGENT_INTENT),
            run_agent=self._run_agent("bitti"),
            remember=remember,
        )
        process_once(WAV, deps=deps)
        remember.assert_called_once_with("ham transkripsiyon", "bitti")

    def test_agent_without_run_agent_falls_to_inbox(self):
        # run_agent enjekte edilmemişse (eski wiring) agent intent güvenle inbox'a düşer.
        result = process_once(WAV, deps=_deps(classify=MagicMock(return_value=AGENT_INTENT)))
        assert result.status == "inbox"


class TestStreamingAnswer:
    """answered → answer_stream varsa parçalar tamamlanır, cevap tek seferde seslendirilir."""

    def test_streams_and_speaks_complete_text_once(self):
        say = MagicMock()
        deps = _deps(
            classify=MagicMock(return_value=ANSWERED_INTENT),
            answer_stream=MagicMock(return_value=iter(["Merhaba. ", "Nasılsın?"])),
            say=say,
        )
        result = process_once(WAV, deps=deps)
        assert result.status == "answered"
        assert result.message == "Merhaba. Nasılsın?"
        assert _spoken(say) == ["Merhaba. Nasılsın?"]

    def test_empty_stream_does_not_speak_when_no_fallback_exists(self):
        say = MagicMock()
        intent = Intent.from_dict(dict(_INTENT_DATA, intent="answered", clean_text=""))
        deps = _deps(
            classify=MagicMock(return_value=intent),
            answer_stream=MagicMock(return_value=iter([])),
            answer=MagicMock(return_value=""),
            say=say,
        )
        result = process_once(WAV, deps=deps)
        assert result.message == ""
        assert _spoken(say) == []

    def test_streamed_full_text_is_remembered(self):
        remember = MagicMock()
        deps = _deps(
            classify=MagicMock(return_value=ANSWERED_INTENT),
            answer_stream=MagicMock(return_value=iter(["Tek cümle."])),
            remember=remember,
        )
        process_once(WAV, deps=deps)
        remember.assert_called_once_with("ham transkripsiyon", "Tek cümle.")

    def test_tts_failure_does_not_corrupt_complete_response(self):
        say = MagicMock(side_effect=RuntimeError("ses çıkışı yok"))
        deps = _deps(
            classify=MagicMock(return_value=ANSWERED_INTENT),
            answer_stream=MagicMock(return_value=iter(["Tam ", "cevap."])),
            say=say,
        )
        result = process_once(WAV, deps=deps)
        assert result.message == "Tam cevap."
        assert _spoken(say) == ["Tam cevap."]

    def test_stream_failure_falls_back_to_full_answer(self):
        say = MagicMock()
        deps = _deps(
            classify=MagicMock(return_value=ANSWERED_INTENT),
            answer_stream=MagicMock(side_effect=RuntimeError("akış koptu")),
            answer=MagicMock(return_value="Tam cevap."),
            say=say,
        )
        result = process_once(WAV, deps=deps)
        assert result.message == "Tam cevap."
        assert _spoken(say) == ["Tam cevap."]            # tam cevap bir kez seslendirildi

    def test_empty_stream_falls_back_to_router_text(self):
        deps = _deps(
            classify=MagicMock(return_value=ANSWERED_INTENT),
            answer_stream=MagicMock(return_value=iter([])),
        )
        result = process_once(WAV, deps=deps)
        assert result.message == "Saat 14:00."           # boş akış → router clean_text

    def test_stop_before_playback_skips_complete_response(self):
        """UI 'Durdur' → stream tamamlanmadan TTS başlatılmaz."""
        say = MagicMock()
        checks = iter([False, True])
        deps = _deps(
            classify=MagicMock(return_value=ANSWERED_INTENT),
            answer_stream=MagicMock(return_value=iter(["Bir. ", "İki. ", "Üç."])),
            should_stop=lambda: next(checks, True),
            say=say,
        )
        result = process_once(WAV, deps=deps)
        assert _spoken(say) == []
        assert result.message == "Bir."

    def test_stop_before_first_sentence_skips_fallback_speech(self):
        """İlk cümleden önce Durdur → tam-cevap yedeği SESLENDİRİLMEZ (yoksa konuşmaya devam eder)."""
        say = MagicMock()
        answer = MagicMock(return_value="TAM CEVAP")
        deps = _deps(
            classify=MagicMock(return_value=ANSWERED_INTENT),
            answer_stream=MagicMock(return_value=iter(["Bir. ", "İki."])),
            answer=answer,
            should_stop=lambda: True,
            say=say,
        )
        result = process_once(WAV, deps=deps)
        assert _spoken(say) == []                         # hiçbir cümle seslendirilmedi
        assert result.message == ""                       # durduruldu → yedek yok
        answer.assert_not_called()                        # tam-cevaba düşülmedi


class TestObservability:
    """Yapısal gözlem: her tur TEK bir kayıt üretir (status, intent, aşama latency'leri).
    'asla raise etmez → inbox' özelliği SESSİZ başarısızlığa dönmesin diye her tur loglanır."""

    def test_successful_turn_emits_one_record(self):
        seen = []
        process_once(WAV, deps=_deps(observe=seen.append))
        assert len(seen) == 1
        rec = seen[0]
        assert rec["status"] == "saved"
        assert rec["intent"] == "note"
        assert "stt_ms" in rec and "classify_ms" in rec and "total_ms" in rec

    def test_inbox_fallback_carries_error_signal(self):
        seen = []
        deps = _deps(classify=MagicMock(side_effect=ValueError("bozuk JSON")), observe=seen.append)
        process_once(WAV, deps=deps)
        assert len(seen) == 1
        assert seen[0]["status"] == "inbox"
        assert seen[0]["error"]                 # hata sinyali yapısal olarak kayıtlı

    def test_unknown_intent_inbox_has_no_error(self):
        # Bilinmeyen intent (hata DEĞİL) ile hata-kaynaklı inbox ayırt edilebilmeli.
        seen = []
        deps = _deps(classify=MagicMock(return_value=UNKNOWN_INTENT), observe=seen.append)
        process_once(WAV, deps=deps)
        assert seen[0]["status"] == "inbox"
        assert seen[0]["error"] is None

    def test_observe_failure_does_not_break_pipeline(self):
        deps = _deps(observe=MagicMock(side_effect=RuntimeError("sink çöktü")))
        result = process_once(WAV, deps=deps)
        assert result.status == "saved"         # gözlem kozmetik → turu bozmaz

    def test_no_observe_dep_is_backward_compatible(self):
        result = process_once(WAV, deps=_deps())
        assert result.status == "saved"


class TestStopPhrase:
    """Sürekli sohbet: transkripsiyon bir bitirme ifadesiyse (kapat kendini) classify/act
    ÇALIŞMADAN status='ended' döner → içerik olarak işlenmez, belleğe yazılmaz."""

    def test_stop_phrase_ends_without_classifying(self):
        classify = MagicMock()
        deps = _deps(
            transcribe=MagicMock(return_value="kapat kendini"),
            classify=classify,
            is_stop_phrase=lambda t: True,
        )
        result = process_once(WAV, deps=deps)
        assert result.status == "ended"
        classify.assert_not_called()        # içerik olarak işlenmedi

    def test_ended_turn_is_not_remembered_or_spoken(self):
        remember, say = MagicMock(), MagicMock()
        deps = _deps(
            transcribe=MagicMock(return_value="kapat kendini"),
            is_stop_phrase=lambda t: True,
            remember=remember,
            say=say,
        )
        process_once(WAV, deps=deps)
        remember.assert_not_called()
        say.assert_not_called()             # bitirme onayını agent döngüsü söyler

    def test_non_stop_phrase_processes_normally(self):
        result = process_once(WAV, deps=_deps(is_stop_phrase=lambda t: False))
        assert result.status == "saved"

    def test_no_is_stop_phrase_dep_is_backward_compatible(self):
        result = process_once(WAV, deps=_deps())   # is_stop_phrase verilmedi
        assert result.status == "saved"


DURABLE_ANSWERED = Intent.from_dict(dict(
    _INTENT_DATA, intent="answered", clean_text="Merhaba Example User.",
    mem_durable=True, mem_scope="personal"))


class TestSharedMemoryHooks:
    """mem0 paylaşımlı bellek kancaları: recall serbest cevaba enjekte edilir; kalıcı
    turlar (mem_durable) mem_enqueue'a alınır; oturum bitişi (ended) mem_flush tetikler.
    Tüm kancalar opsiyonel (None → özellik kapalı) → mevcut testler etkilenmez."""

    def test_durable_turn_enqueued_and_recall_injected(self):
        recall = MagicMock(return_value=("ben example-user",))
        answer = MagicMock(return_value="merhaba example-user")
        mem_enqueue = MagicMock()
        deps = _deps(
            classify=MagicMock(return_value=DURABLE_ANSWERED),
            answer=answer, recall=recall, mem_enqueue=mem_enqueue,
        )
        res = process_once(WAV, deps=deps)
        assert res.status == "answered"
        recall.assert_called_once_with("ham transkripsiyon")
        # answer recall sonucunu memories olarak almalı
        assert answer.call_args.kwargs.get("memories") == ("ben example-user",)
        # durable tur kuyruğa scope=personal ile alınmalı
        assert mem_enqueue.call_args.kwargs.get("scope") == "personal"
        assert mem_enqueue.call_args.args[0] == "ham transkripsiyon"

    def test_provenance_recall_is_rendered_as_attributed_memory_data(self):
        from arda.memory import MemoryHit

        answer = MagicMock(return_value="kaynaklı cevap")
        deps = _deps(
            classify=MagicMock(return_value=ANSWERED_INTENT),
            answer=answer,
            recall_hits=MagicMock(return_value=(MemoryHit(
                text="MODÜ-GRID kararı", memory_id="m-1", scope="example-project", agent="claude"
            ),)),
        )

        process_once(WAV, deps=deps)

        assert answer.call_args.kwargs["memories"] == ("[scope=example-project id=m-1] MODÜ-GRID kararı",)

    def test_non_durable_turn_not_enqueued(self):
        mem_enqueue = MagicMock()
        deps = _deps(
            classify=MagicMock(return_value=ANSWERED_INTENT),   # mem_durable=False varsayılan
            answer=MagicMock(return_value="ok"),
            recall=MagicMock(return_value=()),
            mem_enqueue=mem_enqueue,
        )
        process_once(WAV, deps=deps)
        mem_enqueue.assert_not_called()

    def test_stop_phrase_flushes_memory(self):
        mem_flush = MagicMock()
        deps = _deps(
            transcribe=MagicMock(return_value="kapat"),
            is_stop_phrase=lambda t: True,
            mem_flush=mem_flush,
        )
        res = process_once(WAV, deps=deps)
        assert res.status == "ended"
        mem_flush.assert_called_once()


class TestBargeInHaltsStreaming:
    """Barge-in (kullanıcı araya girdi) akan cevabı DURDURUR ve oturum bayrağı yönetilir.

    Barge-in, yanıt playback'e başlamadan önce stream'i keserse tek-shot TTS başlatılmaz.
    Playback başladıktan sonraki kesme speak katmanının sorumluluğundadır."""

    def teardown_method(self):
        speak.reset_interrupt()
        speak.end_stream()

    def test_interrupt_before_playback_skips_tts(self):
        speak.reset_interrupt()
        say = MagicMock()
        checks = iter([False, True])
        deps = _deps(
            classify=MagicMock(return_value=ANSWERED_INTENT),
            answer_stream=MagicMock(return_value=iter(["Bir. ", "İki. ", "Üç."])),
            should_stop=lambda: next(checks, True), say=say,
        )
        result = process_once(WAV, deps=deps)
        assert _spoken(say) == []
        assert result.message == "Bir."

    def test_stream_generation_does_not_open_speech_session(self):
        """LLM üretimi gerçek playback gibi görünmez; barge-in erken kesemez."""
        speak.reset_interrupt()

        active_during_generation: list[bool] = []

        def _stream():
            active_during_generation.append(speak.is_active())
            yield "Bir. "
            active_during_generation.append(speak.is_active())
            yield "İki."

        deps = _deps(
            classify=MagicMock(return_value=ANSWERED_INTENT),
            answer_stream=MagicMock(return_value=_stream()),
            say=MagicMock(),
        )
        process_once(WAV, deps=deps)
        assert active_during_generation == [False, False]
        assert speak.is_active() is False

    def test_stream_session_closed_even_on_stream_error(self):
        """Akış patlarsa oturum SIZDIRILMAZ (yoksa barge-in thread'i sonsuz yaşar)."""
        speak.reset_interrupt()

        def _boom():
            raise RuntimeError("akış koptu")
            yield ""

        deps = _deps(
            classify=MagicMock(return_value=ANSWERED_INTENT),
            answer_stream=MagicMock(side_effect=lambda *a, **k: _boom()),
            answer=MagicMock(return_value="TAM CEVAP"),
            say=MagicMock(),
        )
        process_once(WAV, deps=deps)
        assert speak.is_active() is False
