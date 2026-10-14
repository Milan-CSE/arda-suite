"""Pipeline: tüm zinciri bağlar. process_once asla raise etmez."""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from arda.actions import ACTION_NAMES
from arda.confirm import PendingConfirmation, PendingStore, interpret_reply
from arda.models import ActionResult, AgentEvent, AgentState, Intent, ProcessResult
from arda.research import spoken_summary
from arda.speak import was_interrupted
from arda.trace import current_trace_id, duration_ms, emit as trace

log = logging.getLogger(__name__)

# Niyet anlaşıldıktan sonra HER komutta söylenen kısa onay ("duyuldu, işliyorum").
# Değişen ifadeler → robotik durmaz. Stop-phrase turu classify'dan önce döndüğü için
# kapsam dışıdır (oturum bitişinde 'goodbye' söylenir, onay değil).
_ACKS = ("Tamam.", "Anladım.", "Tamam, hallediyorum.", "Oldu, bakıyorum.", "Peki.")
_last_ack = [-1]  # ardışık aynı onayı tekrarlamamak için son seçilen index


def _pick_ack() -> str:
    """Onay havuzundan bir öncekiyle aynı olmayan kısa bir ifade seç (değişen onay)."""
    i = random.randrange(len(_ACKS))
    if i == _last_ack[0]:
        i = (i + 1) % len(_ACKS)
    _last_ack[0] = i
    return _ACKS[i]


def _noop_event(_event: AgentEvent) -> None:
    """Varsayılan on_event — durum yayını istemeyen çağrılar için (geriye uyumlu)."""


@dataclass
class Deps:
    transcribe: Callable    # (wav: Path) -> str
    classify: Callable      # (text: str) -> Intent
    write_note: Callable    # (intent: Intent, raw_text: str, date: str) -> Path
    append_task: Callable   # (intent: Intent) -> Path
    save_to_inbox: Callable # (raw_text: str, date: str) -> Path
    say: Callable           # (text: str) -> None
    do_action: Callable | None = None  # (name: str, params: dict) -> str (sesli onay)
    remember: Callable | None = None   # (user: str, assistant: str) -> None (sohbet belleği)
    answer: Callable | None = None     # (user_text: str) -> str (serbest sohbet cevabı)
    answer_stream: Callable | None = None  # (user_text: str) -> Iterator[str] (akan cevap; tamamlanınca tek TTS)
    is_stop_phrase: Callable | None = None  # (text: str) -> bool (oturumu bitir → status='ended')
    should_stop: Callable | None = None  # () -> bool (UI 'Durdur' → playback öncesi TTS'i engelle)
    research: Callable | None = None             # (query: str) -> list[Source] (web arama)
    synthesize: Callable | None = None           # (query, sources) -> str (kaynaklı [n] özet)
    write_research_note: Callable | None = None  # (query, summary, sources) -> Path
    run_agent: Callable | None = None            # (request: str) -> AgentRunResult (çok-adımlı desktop agent)
    observe: Callable | None = None              # (record: dict) -> None (yapısal tur gözlemi; yoksa INFO log)
    recall: Callable | None = None      # (text: str) -> tuple[str,...] (mem0 geri çağırma; serbest cevaba enjekte edilir)
    recall_hits: Callable | None = None  # (text: str) -> tuple[MemoryHit,...] (provenance taşıyan recall)
    mem_enqueue: Callable | None = None  # (user, assistant, *, scope, action_result) -> None (kalıcı tur → mem0 kuyruğu)
    mem_flush: Callable | None = None    # () -> None (oturum sonu: kuyruktaki kalıcı turları toplu yaz)
    # Düşük-güvenli eylemde teyit sorma: tutucu enjekte edilirse action sessizce inbox'a
    # düşmez → "şunu mu demek istedin?" sorulur, açık 'evet' ile çalışır. None → özellik kapalı
    # (eski inbox davranışı). Bekleyen onay confirm_timeout_sec sonra düşer (eylem yapılmaz).
    pending: PendingStore | None = None
    confirm_timeout_sec: float = 60.0


def _safe_say(say: Callable, msg: str) -> None:
    """TTS yalnızca kozmetik geri bildirimdir; başarısızlığı asla pipeline'ı bozmamalı.

    say() bir kez kayıt yapıldıktan SONRA çağrılır; burada hata fırlatması,
    başarılı bir notu yanlışlıkla 'inbox'/'failed' durumuna düşürürdü."""
    try:
        say(msg)
    except Exception as exc:  # noqa: BLE001 — TTS hatası kasıtlı olarak yutulur
        log.warning("Sesli geri bildirim başarısız (yok sayıldı): %s", exc)


def _recall(deps: "Deps", text: str) -> tuple[str, ...]:
    """mem0 paylaşımlı bellekten ilgili anıları geri çağır (varsa). recall dep yoksa
    ya da hata verirse boş tuple döner — recall kozmetik, turu ASLA bozmaz."""
    if deps.recall_hits is not None:
        trace("memory.recall", "started")
        started_at = time.perf_counter()
        try:
            result = tuple(hit.prompt_text() for hit in deps.recall_hits(text))
            trace("memory.recall", "completed", duration_ms=duration_ms(started_at), hits=len(result))
            return result
        except Exception as exc:  # noqa: BLE001 — recall kozmetik; turu bozmasın
            trace("memory.recall", "failed", duration_ms=duration_ms(started_at), error=type(exc).__name__)
            log.warning("provenance recall başarısız (yok sayıldı): %s", exc)
            return ()
    if deps.recall is None:
        return ()
    try:
        trace("memory.recall", "started")
        started_at = time.perf_counter()
        result = tuple(deps.recall(text))
        trace("memory.recall", "completed", duration_ms=duration_ms(started_at), hits=len(result))
        return result
    except Exception as exc:  # noqa: BLE001 — recall kozmetik; turu bozmasın
        trace("memory.recall", "failed", duration_ms=duration_ms(started_at), error=type(exc).__name__)
        log.warning("recall başarısız (yok sayıldı): %s", exc)
        return ()


def _answer_text(deps: "Deps", user_text: str, *, fallback: str, memories: tuple[str, ...] = ()) -> str:
    """Serbest sohbet cevabını al (router'dan AYRI motor). answer dep yoksa ya da
    hata verirse router'ın schema'lı clean_text'ine düş → asistan asla sessiz kalmaz.

    `memories` çağıran tarafından (bir kez) hesaplanmış mem0 recall sonucudur; burada
    tekrar aranmaz — sadece deps.answer'a iletilir."""
    if deps.answer is None:
        return fallback
    trace("llm.response", "started")
    started_at = time.perf_counter()
    try:
        text = deps.answer(user_text, memories=memories)
    except Exception as exc:  # noqa: BLE001 — cevap motoru/ağ hatası → router metnine düş
        trace("llm.response", "failed", duration_ms=duration_ms(started_at), error=type(exc).__name__)
        log.warning("Serbest cevap başarısız, router metnine düşülüyor: %s", exc)
        return fallback or "Şu an cevap veremedim, tekrar dener misin?"
    trace("llm.response", "completed", duration_ms=duration_ms(started_at), chars=len(text or fallback or ""))
    return text or fallback or "Şu an cevap veremedim, tekrar dener misin?"


def _halted(deps: "Deps") -> bool:
    """Akan cevap kesilmeli mi? İKİ kapı tek yerde:

    - deps.should_stop → UI 'Durdur' (controls.is_cancelled)
    - speak.was_interrupted() → barge-in (kullanıcı araya girdi; listen.record_barge_in
      speak.interrupt() çağırır). Bayrak TUR boyunca yaşar, say() onu silmez → asistan
      bir cümle susup kaldığı yerden devam etmez."""
    if deps.should_stop is not None and deps.should_stop():
        return True
    return was_interrupted()


def _answer_streamed(deps: "Deps", user_text: str, *, fallback: str) -> str:
    """Akan cevabı bellekte TAMAMLA, sonra tek seferde seslendir ve tam metni döndür.

    Ollama parçaları akmaya devam eder; ancak cümle aralarında bloklayan TTS sentezi
    ses boşluğu oluşturduğu için normal tamamlanan yanıtta yalnızca bir ``say`` çağrısı
    yapılır. Akış boş/hatalıysa mevcut fallback yoluna düşer ve fallback metni varsa
    onu bir kez seslendirir.

    recall BİR KEZ hesaplanır (fonksiyon başında) ve hem answer_stream'e hem fallback
    cevabına geçirilir → çift arama yapılmaz."""
    memories = _recall(deps, user_text)
    parts: list[str] = []
    stopped = False
    # Ollama üretimi konuşma aktivitesi değildir. `speak.is_active()` yalnızca
    # speak.say() içindeki gerçek playback (`_speaking`) tarafından açılmalıdır;
    # native barge-in bu kapıyı kullanır.
    try:
        trace("llm.response", "started")
        started_at = time.perf_counter()
        for chunk in deps.answer_stream(user_text, memories=memories):
            if _halted(deps):
                stopped = True
                break
            if chunk:
                parts.append(chunk)
    except Exception as exc:  # noqa: BLE001 — fallback yolu da kozmetik
        trace("llm.response", "failed", duration_ms=duration_ms(started_at), error=type(exc).__name__)
        log.warning("Akan cevap başarısız, tam cevaba düşülüyor: %s", exc)
        parts = []
    else:
        trace("llm.response", "completed", duration_ms=duration_ms(started_at), chars=sum(len(part) for part in parts))
    full = "".join(parts).strip()
    # Durdurulduysa veya akış tamamlandıktan hemen önce kesme geldiyse TTS başlatma.
    if stopped or _halted(deps):
        return full
    if full:
        _safe_say(deps.say, full)
        return full
    if deps.answer is None:
        msg = fallback
    else:
        try:
            msg = deps.answer(user_text, memories=memories) or fallback
        except Exception as exc:  # noqa: BLE001 — fallback yolu da kozmetik
            log.warning("Serbest cevap başarısız, router metnine düşülüyor: %s", exc)
            msg = fallback
    if not msg:
        return ""
    _safe_say(deps.say, msg)
    return msg


def _remember(
    deps: "Deps",
    user: str,
    assistant: str,
    *,
    action_result: dict | None = None,
    intent: Intent | None = None,
) -> None:
    """Başarılı turu sohbet belleğine ekle (varsa). Bellek hatası turu bozmamalı.

    Yalnızca başarılı dallarda çağrılır → inbox/hata mesajları bağlamı kirletmez.
    `action_result` yoksa eski 2 argümanlı remember çağrısı kullanılır (geriye uyumlu);
    varsa yapılandırılmış sonuç 3. argüman olarak geçer.

    `intent` verilip `intent.mem_durable` doğruysa tur ayrıca mem0 paylaşımlı bellek
    kuyruğuna alınır (deps.mem_enqueue) — bu, kısa ömürlü sohbet belleğinden (remember)
    AYRI ve kalıcı bir katmandır. mem_enqueue hatası da turu ASLA bozmaz."""
    if deps.remember is not None:
        try:
            if action_result is not None:
                deps.remember(user, assistant, action_result)
            else:
                deps.remember(user, assistant)
        except Exception as exc:  # noqa: BLE001 — bellek kozmetik; pipeline'ı bozmasın
            log.warning("Sohbet belleğine yazılamadı (yok sayıldı): %s", exc)

    if deps.mem_enqueue is not None and intent is not None and getattr(intent, "mem_durable", False):
        try:
            deps.mem_enqueue(
                user, assistant,
                scope=getattr(intent, "mem_scope", "personal"),
                action_result=action_result,
            )
        except Exception as exc:  # noqa: BLE001 — kalıcı hafıza kozmetik; turu bozmasın
            log.warning("mem0 kuyruğa alma başarısız (yok sayıldı): %s", exc)


def _observe(
    deps: "Deps",
    result: ProcessResult,
    raw_text: str,
    intent: Intent | None,
    timings: dict,
    t0: float,
    *,
    error: BaseException | None = None,
) -> None:
    """Tek turluk yapısal gözlem kaydı (status, intent, aşama latency'leri, hata sinyali).

    'asla raise etmez → inbox' özelliği sessiz başarısızlığa dönmesin diye HER tur loglanır:
    yanlış sınıflandırma / STT hatası / sürekli inbox-düşme bu kayıtla görünür olur. observe
    dep'i yoksa INFO log'a yazılır; sink hatası turu ASLA bozmaz (TTS/bellek ile aynı kalıp)."""
    record = {
        "trace_id": current_trace_id(),
        "status": result.status,
        "intent": intent.intent if intent is not None else None,
        "chars": len(raw_text or ""),
        "error": f"{type(error).__name__}: {error}" if error is not None else None,
        **timings,
        "total_ms": round((time.perf_counter() - t0) * 1000, 1),
    }
    try:
        if deps.observe is not None:
            deps.observe(record)
        else:
            log.info("tur: %s", record)
    except Exception as exc:  # noqa: BLE001 — gözlem kozmetik; turu bozmasın
        log.warning("Gözlem kaydı yazılamadı (yok sayıldı): %s", exc)


def _research_turn(
    deps: "Deps", query: str, raw_text: str, today: str, *, intent: Intent | None = None
) -> ProcessResult:
    """research intent: ara → sentezle → kaynaklı not + KISA sesli özet.

    research/synthesize dep yoksa → query inbox'a alınır (özellik kapalı). Ağ/servis/sentez
    hatası, boş sonuç ve not-yazma hatası SESLİ fallback'e düşer; process_once asla raise etmez."""
    if deps.research is None or deps.synthesize is None:
        path = deps.save_to_inbox(raw_text, today)
        msg = "Araştırma şu an kapalı, kaydı gelen kutusuna aldım."
        _safe_say(deps.say, msg)
        return ProcessResult(status="inbox", message=msg, path=path)
    try:
        sources = deps.research(query)
        if not sources:
            msg = "Bununla ilgili bir şey bulamadım."
            _safe_say(deps.say, msg)
            return ProcessResult(status="researched", message=msg)
        summary = deps.synthesize(query, sources)
    except Exception as exc:  # noqa: BLE001 — ağ/servis/sentez hatası → sesli fallback
        log.warning("Araştırma başarısız: %s", exc)
        msg = "Şu an araştıramadım, internet ya da servis sorunu olabilir."
        _safe_say(deps.say, msg)
        return ProcessResult(status="researched", message=msg)

    spoken = spoken_summary(summary)        # [n] atıfları sök + kısalt (sesli kısa)
    path = None
    if deps.write_research_note is not None:
        try:
            path = deps.write_research_note(query, summary, sources)
        except Exception as exc:  # noqa: BLE001 — not yazılamasa da sesli cevap döner
            log.warning("Araştırma notu yazılamadı (yok sayıldı): %s", exc)
    _safe_say(deps.say, spoken)
    _remember(deps, raw_text, spoken, intent=intent)
    return ProcessResult(status="researched", message=spoken, path=path)


def _execute_action(
    deps: "Deps", action: str, params: dict, raw_text: str, *, intent: Intent | None = None
) -> ProcessResult:
    """Küratörlü Mac eylemini çalıştır → sesli onay + bellek + ProcessResult(status='action').

    Hem normal yüksek-güvenli action dalı hem de teyit-sonrası 'evet' yolu bunu kullanır
    (DRY). do_action ActionResult (sesli metin + yapılandırılmış bellek) ya da düz str döner."""
    outcome = deps.do_action(action, dict(params))
    if isinstance(outcome, ActionResult):
        msg, action_result = outcome.spoken, outcome.memory
    else:
        msg, action_result = outcome, None
    _safe_say(deps.say, msg)
    _remember(deps, raw_text, msg, action_result=action_result, intent=intent)
    return ProcessResult(status="action", message=msg, intent=intent)


def _handle_pending(
    deps: "Deps", pc: PendingConfirmation, raw_text: str
) -> ProcessResult | None:
    """Bekleyen onay varken bu turu yanıt olarak yorumla.

    'evet' → tutulan eylemi çalıştır (status='action'). 'hayır' → "yapmadım" (status='cancelled').
    'unclear' → None döndür: çağıran normal classify'a düşer (bu tur yeni komut sayılır)."""
    verdict = interpret_reply(raw_text)
    if verdict == "yes":
        return _execute_action(deps, pc.action, pc.params, raw_text)
    if verdict == "no":
        msg = "Tamam, yapmadım."
        _safe_say(deps.say, msg)
        return ProcessResult(status="cancelled", message=msg)
    return None


def _route(
    deps: "Deps",
    intent: Intent,
    raw_text: str,
    today: str,
    on_event: Callable[[AgentEvent], None],
) -> ProcessResult:
    """Sınıflandırılmış intent'i ilgili dala yönlendir → ProcessResult.

    Dal mantığı process_once'tan birebir taşındı; tek-çıkışlı gözlem (process_once'ta)
    için ayrıştırıldı. Bir dal raise ederse process_once'ın dış try'ı inbox'a düşürür."""
    if intent.intent == "note":
        path = deps.write_note(intent, raw_text, today)
        msg = f"{intent.title} kaydedildi."
        _safe_say(deps.say, msg)
        _remember(deps, raw_text, msg, intent=intent)
        return ProcessResult(status="saved", message=msg, path=path, intent=intent)

    if intent.intent == "task":
        path = deps.append_task(intent)
        msg = f"Görev eklendi: {intent.title}"
        _safe_say(deps.say, msg)
        _remember(deps, raw_text, msg, intent=intent)
        return ProcessResult(status="task", message=msg, path=path, intent=intent)

    if intent.intent == "answered":
        # Cevap router'dan DEĞİL, ayrı serbest motordan gelir (doğal, schema'sız);
        # motor yoksa/hata verirse router'ın clean_text'ine düşülür. answer_stream
        # varsa cevap CÜMLE CÜMLE seslendirilir (seslendirme _answer_streamed içinde);
        # yoksa tam metin alınıp burada bir kez seslendirilir (geriye uyumlu).
        if deps.answer_stream is not None:
            msg = _answer_streamed(deps, raw_text, fallback=intent.clean_text)
        else:
            # recall BİR KEZ hesaplanır (döngü/çift arama yok) ve serbest cevaba geçilir.
            memories = _recall(deps, raw_text)
            msg = _answer_text(deps, raw_text, fallback=intent.clean_text, memories=memories)
            _safe_say(deps.say, msg)
        _remember(deps, raw_text, msg, intent=intent)
        return ProcessResult(status="answered", message=msg, intent=intent)

    is_action = intent.intent == "action" or (intent.intent == "agent" and intent.action in ACTION_NAMES)
    if is_action and deps.do_action is not None:
        if intent.confidence == "low":
            # Düşük güven: yan-etkili/geri-alınamaz eylemi SESSİZCE yapma.
            if deps.pending is not None:
                # Teyit iste: ne anladığını geri söyle + bekleyen onay kur. Açık 'evet'
                # ile sonraki tur çalışır (process_once); sessizlik/'hayır'/bayatlama → yapılmaz.
                deps.pending.set(PendingConfirmation(
                    action=intent.action, params=dict(intent.params),
                    request_text=raw_text, clean_text=intent.clean_text,
                    created_at=time.monotonic(),
                ))
                msg = f"Şunu mu demek istedin: \"{intent.clean_text}\"? Evet dersem yaparım."
                _safe_say(deps.say, msg)
                return ProcessResult(status="clarify", message=msg, intent=intent)
            # Özellik kapalı (pending yok) → güvenli erteleme: sesli haber + ham metni inbox'a al.
            log.warning("Düşük güvenli eylem '%s' ertelendi (inbox).", intent.action)
            path = deps.save_to_inbox(raw_text, today)
            msg = "Bunu tam anlayamadım, emin olmadığım için yapmadım; gelen kutusuna aldım."
            _safe_say(deps.say, msg)
            return ProcessResult(status="unclear", message=msg, path=path, intent=intent)
        # Kürütörlü Mac eylemi (beyaz liste) → ortak yürütücü (teyit-sonrası 'evet' ile aynı yol).
        return _execute_action(deps, intent.action, intent.params, raw_text, intent=intent)

    if intent.intent == "research":
        # Web araştırma: sorgu = clean_text. Tüm hata yolları sesli fallback'e düşer.
        return _research_turn(deps, intent.clean_text, raw_text, today, intent=intent)

    if intent.intent == "agent" and deps.run_agent is not None:
        # Çok-adımlı bilgisayar/proje komutu: planla → izin → çalıştır → doğrula → özetle.
        # run_agent ASLA raise etmez; AgentRunResult.spoken kısa sesli özettir. run_agent
        # enjekte edilmemişse (eski wiring) agent intent güvenle inbox'a düşer. on_event
        # geçilir → executor her adımda THINKING+mesaj yayınlar (menübar canlı gösterir).
        # Explicit skill selectors are control syntax, not prose. Preserve the raw
        # request so the router's clean_text normalization cannot silently remove it.
        agent_request = raw_text if raw_text.lstrip().startswith("skill:") else (intent.clean_text or raw_text)
        trace("agent.execution", "started")
        outcome = deps.run_agent(agent_request, on_event)
        trace("agent.execution", "completed", result_status=getattr(outcome, "status", "unknown"))
        msg = outcome.spoken
        _safe_say(deps.say, msg)
        _remember(deps, raw_text, msg, intent=intent)
        return ProcessResult(status="agent", message=msg, intent=intent)

    # Bilinmeyen intent → inbox (hata DEĞİL; gözlemde error=None ile ayrışır)
    log.warning("Bilinmeyen intent '%s', inbox'a düşüyor.", intent.intent)
    path = deps.save_to_inbox(raw_text, today)
    msg = "Anlaşılamadı, inbox'a kaydedildi."
    _safe_say(deps.say, msg)
    return ProcessResult(status="inbox", message=msg, path=path)


def process_once(
    wav: Path | None,
    *,
    deps: Deps,
    on_event: Callable[[AgentEvent], None] = _noop_event,
) -> ProcessResult:
    today = date.today().isoformat()
    raw_text = ""
    t0 = time.perf_counter()
    timings: dict[str, float] = {}
    intent: Intent | None = None

    try:
        on_event(AgentEvent(state=AgentState.TRANSCRIBING, source="pipeline"))
        trace("stt.transcription", "started")
        stt_started = time.perf_counter()
        try:
            raw_text = deps.transcribe(wav)
        except Exception as exc:
            trace("stt.transcription", "failed", duration_ms=duration_ms(stt_started), error=type(exc).__name__)
            raise
        trace("stt.transcription", "completed", duration_ms=duration_ms(stt_started), chars=len(raw_text or ""))
        timings["stt_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        # Boş transkript (sessizlik / 'Durdur' iptali / whisper uydurması elendi) → SESSİZ
        # no-op: classify/say/save YOK, bildirim çıkmaz. Kullanıcı bir şey demedi → standby.
        if not raw_text.strip():
            result = ProcessResult(status="empty", message="")
            _observe(deps, result, raw_text, intent, timings, t0)
            return result

        # Sürekli sohbet: bitirme ifadesi ("kapat kendini") → classify/act ÇALIŞMADAN çık.
        # İçerik olarak işlenmez (not/cevap olmaz), belleğe yazılmaz; agent döngüsü standby'a alır.
        if deps.is_stop_phrase is not None and deps.is_stop_phrase(raw_text):
            if deps.pending is not None:
                deps.pending.clear()           # oturum bitiyor → bekleyen onay düşer
            if deps.mem_flush is not None:
                try:
                    deps.mem_flush()           # oturum sonu: kuyruktaki kalıcı turları toplu yaz
                except Exception as exc:  # noqa: BLE001 — flush kozmetik; oturum bitişini bozmasın
                    log.warning("mem0 flush başarısız (yok sayıldı): %s", exc)
            result = ProcessResult(status="ended", message="")
            _observe(deps, result, raw_text, intent, timings, t0)
            return result

        # Bekleyen teyit (önceki tur düşük-güvenli action sordu) → bu turu yanıt olarak yorumla.
        # 'evet' eylemi çalıştırır, 'hayır' iptal eder; 'unclear' (ne evet ne hayır) → tutucu
        # zaten boşaltıldı, aşağı düşüp bu turu YENİ komut olarak classify ederiz.
        if deps.pending is not None:
            pc = deps.pending.take_if_fresh(now=time.monotonic(), timeout=deps.confirm_timeout_sec)
            if pc is not None:
                result = _handle_pending(deps, pc, raw_text)
                if result is not None:
                    _observe(deps, result, raw_text, intent, timings, t0)
                    return result

        on_event(AgentEvent(state=AgentState.THINKING, source="pipeline"))
        t_classify = time.perf_counter()
        trace("llm.intent", "started")
        classify_started = time.perf_counter()
        try:
            intent = deps.classify(raw_text)
        except Exception as exc:
            trace("llm.intent", "failed", duration_ms=duration_ms(classify_started), error=type(exc).__name__)
            raise
        trace("llm.intent", "completed", duration_ms=duration_ms(classify_started), intent=intent.intent)
        timings["classify_ms"] = round((time.perf_counter() - t_classify) * 1000, 1)

        # Niyet anlaşıldı → komutlarda kısa, değişen sesli onay ("duyuldu, işliyorum").
        # answered turunda (serbest sohbet) onay söylenmez → doğrudan doğal cevap başlar (gecikmesiz + barge-in dostu).
        # Düşük güvenli eylemde onay söylenmez → teyit sorulur.
        is_act = intent.intent == "action" or (intent.intent == "agent" and intent.action in ACTION_NAMES)
        if intent.intent != "answered" and not (is_act and intent.confidence == "low"):
            _safe_say(deps.say, _pick_ack())

        result = _route(deps, intent, raw_text, today, on_event)
        _observe(deps, result, raw_text, intent, timings, t0)
        return result

    except Exception as exc:
        # Beyin/STT hatası (Ollama down, bozuk JSON, ağ…) burada yakalanır. Asistan
        # ASLA sessiz kalmaz: sesli özür + veri güvenliği için ham metni inbox'a al.
        # exc_info=True → tam yığın izi (traceback) log dosyasına yazılır; yoksa
        # yalnız hata mesajı görünür ve "dinledikten sonra hata" ayıklanamaz.
        trace("pipeline", "failed", error=type(exc).__name__)
        log.warning("İşleme hatası, inbox'a düşüyor: %s", exc, exc_info=True)
        try:
            path = deps.save_to_inbox(raw_text or "(boş transkripsiyon)", today)
            msg = "Şu an işleyemedim, kaydı gelen kutusuna aldım."
            _safe_say(deps.say, msg)
            result = ProcessResult(status="inbox", message=msg, path=path)
        except Exception as exc2:
            log.error("Inbox kaydı da başarısız: %s", exc2)
            critical = "Bir sorun var, kayıt yapamadım. Tekrar dener misin?"
            _safe_say(deps.say, critical)  # disk de dolsa en azından sesli haber ver
            result = ProcessResult(status="failed", message=critical)
        _observe(deps, result, raw_text, intent, timings, t0, error=exc)
        return result
