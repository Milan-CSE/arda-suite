"""Agent executor — planı izin/onay kapısından geçirip adım adım yürütür.

Akış: plan → her adım için { izin (auto/confirm/deny) → gerekirse onay → çalıştır →
doğrula → logla } → sesli özet. run_agent ASLA raise etmez: plan üretilemezse güvenli
sesli fallback; tek adım hatası tüm koşuyu öldürmez (izole + loglanır).

Güvenlik: confirm fail-closed (callback patlarsa adım çalışmaz); deny adımları hiç
çalıştırılmaz; tüm araç IO sandbox'lı ToolContext üzerinden.
"""
from __future__ import annotations

import dataclasses
import inspect
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from arda.agent_models import AgentRunResult, Plan, PlanStep, RiskLevel, StepLog
from arda.agent_skills import SkillContext
from arda.agent_permission import decide as _decide
from arda.agent_tools import ToolContext, cleanup_browser_tabs, run_tool as _run_tool
from arda.agent_verifier import verify_step as _verify_step
from arda.models import AgentEvent, AgentRuntimeEvent, AgentState
from arda.process_runner import ManagedProcessRunner

log = logging.getLogger(__name__)

_DEFAULT_MAX_STEPS = 12
_DEFAULT_RUN_TIMEOUT_SEC = 120.0


def _noop_event(_event: AgentEvent) -> None:
    """Varsayılan on_event — canlı ilerleme istemeyen çağrılar için (test/headless)."""


def _emit(on_event, message: str, *, runtime: AgentRuntimeEvent | None = None) -> None:
    """Adım ilerlemesini THINKING olayı olarak yayınla. Olay kozmetik → hata yutulur
    (UI kuyruğu sorunu agent koşusunu bozmamalı; pipeline/TTS ile aynı kalıp)."""
    try:
        on_event(AgentEvent(state=AgentState.THINKING, message=message, source="agent", runtime=runtime))
    except Exception as exc:  # noqa: BLE001 — olay yayını kozmetik
        log.warning("Agent ilerleme olayı yayınlanamadı (yok sayıldı): %s", exc)


def _fail_closed_confirm(_msg: str) -> bool:
    """Varsayılan confirm: onay yoksa REDDET (güvenli taraf)."""
    return False


@dataclass
class AgentRunDeps:
    """run_agent bağımlılıkları — hepsi enjekte edilebilir (test'te mock, app'te gerçek)."""

    plan: Callable[[str], Plan]                                  # planlayıcı (LLM)
    ctx: ToolContext                                             # sandbox'lı araç ortamı
    confirm: Callable[[str], bool] = _fail_closed_confirm        # onay callback (native dialog)
    decide: Callable[[str, dict], RiskLevel] = _decide           # izin politikası
    run_tool: Callable = _run_tool                               # araç dispatch
    verify: Callable = _verify_step                              # adım doğrulama
    max_steps: int = _DEFAULT_MAX_STEPS
    # Opsiyonel LLM özetleyici (plan, logs) -> str; yoksa deterministik özet kullanılır.
    summarize: Callable[[Plan, tuple], str] | None = None
    # Opsiyonel denetim günlüğü (request, result) -> None; koşu izini kalıcılaştırır (agent_audit).
    audit: Callable[[str, "AgentRunResult"], None] | None = None
    # Opsiyonel, iş-parçacığı güvenli iptal kontrolü; HER adımdan önce çağrılır.
    # True dönerse koşu o noktada nazikçe durur (status="cancelled"). Hata → fail-open
    # (koşu sürer): bozuk bir kontrol iyi koşuyu öldürmesin (olay/denetim ile aynı kalıp).
    should_stop: Callable[[], bool] | None = None
    skill: SkillContext | None = None
    run_timeout_sec: float = _DEFAULT_RUN_TIMEOUT_SEC


def _confirm_prompt(step: PlanStep) -> str:
    """Onay dialog'una yazılacak kısa, anlaşılır metin."""
    why = step.rationale or step.tool
    detail = ", ".join(f"{k}={v}" for k, v in step.params.items() if k != "content")
    suffix = f" ({detail})" if detail else ""
    return f"{why} — '{step.tool}' çalıştırılsın mı?{suffix}"


def _safe_confirm(
    confirm: Callable[[str], bool], prompt: str,
    interrupted: Callable[[], bool] | None = None,
) -> bool:
    """Onayı güvenle bekle; iptal/timeout'ta stale cevap yeni koşuya geçemez."""
    if interrupted is None:
        try:
            return bool(confirm(prompt))
        except Exception as exc:  # noqa: BLE001 — dialog hatası → adım çalışmasın
            log.warning("Onay callback'i başarısız, fail-closed: %s", exc)
            return False

    cancel_event = threading.Event()
    result: list[bool] = []
    try:
        accepts_cancel_event = "cancel_event" in inspect.signature(confirm).parameters
    except (TypeError, ValueError):
        accepts_cancel_event = False

    def invoke() -> None:
        try:
            value = (
                confirm(prompt, cancel_event=cancel_event)
                if accepts_cancel_event else confirm(prompt)
            )
            result.append(bool(value))
        except Exception as exc:  # noqa: BLE001 — dialog hatası → adım çalışmasın
            log.warning("Onay callback'i başarısız, fail-closed: %s", exc)
            result.append(False)

    worker = threading.Thread(target=invoke, daemon=True, name="arda-agent-approval")
    worker.start()
    while worker.is_alive():
        if interrupted():
            cancel_event.set()
            return False
        worker.join(0.05)
    # The callback may have finished during the final join after the deadline
    # or cancellation flag became true; never accept that stale approval.
    if interrupted():
        return False
    return result[0] if result else False


def _should_stop(deps: AgentRunDeps) -> bool:
    """İptal istendi mi? Hata → fail-open (False): bozuk kontrol iyi koşuyu durdurmasın."""
    if deps.should_stop is None:
        return False
    try:
        return bool(deps.should_stop())
    except Exception as exc:  # noqa: BLE001 — iptal kontrolü kozmetik hata → koşu sürer
        log.warning("İptal kontrolü başarısız (yok sayıldı): %s", exc)
        return False


def _emit_runtime(on_event, *, event_type: str, run_id: str, status: str,
                  step_id: str | None = None, metadata: dict | None = None,
                  message: str = "") -> None:
    """Publish a safe structured event through the existing UI event queue."""
    _emit(
        on_event,
        message,
        runtime=AgentRuntimeEvent(
            event_type=event_type, run_id=run_id, step_id=step_id,
            status=status, metadata=dict(metadata or {}),
        ),
    )


def _deadline_exceeded(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


def _run_step(step: PlanStep, deps: AgentRunDeps, *, run_id: str, step_id: str,
              on_event, deadline: float | None) -> StepLog:
    """Tek adımı izin/onay kapısından geçir, çalıştır, doğrula → StepLog. Raise etmez."""
    risk = deps.decide(step.tool, step.params)
    if risk == RiskLevel.DENY:
        return StepLog(step.tool, step.params, step.rationale, risk, "denied",
                       ok=False, error="güvenlik politikası reddetti", verified=False)
    if risk == RiskLevel.CONFIRM:
        _emit_runtime(on_event, event_type="agent.approval.requested", run_id=run_id,
                      step_id=step_id, status="waiting", metadata={"tool": step.tool})
        approved = _safe_confirm(
            deps.confirm,
            _confirm_prompt(step),
            interrupted=lambda: _should_stop(deps) or _deadline_exceeded(deadline),
        )
        if not approved:
            _emit_runtime(on_event, event_type="agent.approval.resolved", run_id=run_id,
                          step_id=step_id, status="denied", metadata={"tool": step.tool})
            return StepLog(step.tool, step.params, step.rationale, risk, "confirmed-no",
                           ok=False, error="kullanıcı onaylamadı", verified=False)
        _emit_runtime(on_event, event_type="agent.approval.resolved", run_id=run_id,
                      step_id=step_id, status="approved", metadata={"tool": step.tool})

    result = deps.run_tool(step.tool, step.params, deps.ctx)
    verified, _vmsg = deps.verify(step, result, deps.ctx)
    return StepLog(
        step.tool, step.params, step.rationale, risk,
        decision="ran" if result.ok else "error",
        ok=result.ok, output=result.output, error=result.error, verified=verified,
    )


def _deterministic_summary(plan: Plan, logs: tuple[StepLog, ...]) -> str:
    """Loglardan kısa, sesli-dostu Türkçe özet (LLM özetleyici yoksa)."""
    ran = [l for l in logs if l.decision == "ran"]
    blocked = [l for l in logs if l.decision in ("denied", "confirmed-no")]
    errored = [l for l in logs if l.decision == "error"]
    parts: list[str] = []
    if plan.summary:
        parts.append(plan.summary.rstrip(".") + ".")
    parts.append(f"{len(ran)} adım çalıştı.")
    for l in ran:                         # öne çıkan çıktılar
        if l.tool == "run_tests" and l.output:
            parts.append(f"Testler: {l.output}.")
        elif l.tool == "list_dir" and l.output:
            parts.append(f"{l.output} listelendi.")
    if blocked:
        parts.append(f"{len(blocked)} adım onay/güvenlik nedeniyle atlandı.")
    if errored:
        parts.append(f"{len(errored)} adım hata verdi.")
    return " ".join(parts)


def _summary(plan: Plan, logs: tuple[StepLog, ...], deps: AgentRunDeps) -> str:
    """Özet: varsa LLM özetleyici, hata/yoksa deterministik özet (asla boş/raise değil)."""
    if deps.summarize is not None:
        try:
            text = deps.summarize(plan, logs)
            if text and text.strip():
                return text.strip()
        except Exception as exc:  # noqa: BLE001 — LLM özet hatası → deterministik özete düş
            log.warning("LLM özetleyici başarısız, deterministik özete düşülüyor: %s", exc)
    return _deterministic_summary(plan, logs)


def _status(logs: tuple[StepLog, ...]) -> str:
    """Koşu durumu: done (hepsi çalıştı) | denied (hepsi engellendi) | partial (karışık)."""
    if not logs:
        return "empty"
    ran_ok = [l for l in logs if l.decision == "ran" and l.ok]
    if len(ran_ok) == len(logs):
        return "done"
    if all(l.decision == "denied" for l in logs):
        return "denied"
    return "partial"


def _safe_audit(deps: AgentRunDeps, request: str, result: AgentRunResult) -> None:
    """Koşuyu kalıcı denetim günlüğüne yaz (varsa). Denetim kozmetik → hata koşuyu bozmasın."""
    if deps.audit is None:
        return
    try:
        deps.audit(request, result)
    except Exception as exc:  # noqa: BLE001 — günlük yazımı koşuyu bozmamalı
        log.warning("Denetim günlüğü yazılamadı (yok sayıldı): %s", exc)


def run_agent(request: str, *, deps: AgentRunDeps, on_event=_noop_event) -> AgentRunResult:
    """İsteği planla, adım adım güvenle yürüt, sesli özetle, koşuyu denetim günlüğüne yaz.

    ASLA raise etmez. on_event her adımda THINKING+ilerleme yayınlar (menübar canlı gösterir);
    her çıkış yolu (failed/empty/done/partial) audit ile kalıcı günlüğe geçer."""
    run_id = f"run_{uuid.uuid4().hex}"
    started_mono = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    timeout = max(0.0, float(deps.run_timeout_sec))
    deadline = started_mono + timeout if timeout else None
    process_runner = ManagedProcessRunner(
        should_cancel=lambda: _should_stop(deps),
        deadline_exceeded=lambda: _deadline_exceeded(deadline),
    )
    run_deps = dataclasses.replace(
        deps,
        ctx=dataclasses.replace(deps.ctx, process_runner=process_runner.run),
    )
    _emit_runtime(on_event, event_type="agent.run.started", run_id=run_id,
                  status="running", metadata={"timeout_sec": timeout})
    try:
        result = _execute(request, run_deps, on_event, run_id=run_id, deadline=deadline)
    except Exception as exc:  # noqa: BLE001 — lifecycle boundary must not raise
        log.warning("Agent koşusu beklenmedik şekilde başarısız: %s", exc)
        result = AgentRunResult(status="failed", spoken="Agent koşusu başarısız oldu.",
                                termination_reason="internal_error")
    finally:
        process_runner.close()
        cleanup_browser_tabs(run_deps.ctx)
    duration_ms = round((time.monotonic() - started_mono) * 1000, 1)
    result = dataclasses.replace(
        result, run_id=run_id, started_at=started_at, duration_ms=duration_ms,
        completed_steps=sum(1 for log_item in result.logs if log_item.decision == "ran"),
    )
    if run_deps.skill is not None:
        result = dataclasses.replace(
            result,
            skill_id=run_deps.skill.skill_id,
            skill_source=run_deps.skill.source,
            skill_hash=run_deps.skill.content_hash,
        )
    terminal_event = {
        "done": ("agent.run.completed", "completed"),
        "cancelled": ("agent.run.cancelled", "cancelled"),
        "timed_out": ("agent.run.timed_out", "timed_out"),
    }.get(result.status, ("agent.run.failed", "failed"))
    _emit_runtime(on_event, event_type=terminal_event[0], run_id=run_id,
                  status=terminal_event[1], metadata={"duration_ms": duration_ms})
    _safe_audit(run_deps, request, result)
    return result


def _execute(request: str, deps: AgentRunDeps, on_event, *, run_id: str,
             deadline: float | None) -> AgentRunResult:
    """Asıl yürütme: plan → adımlar → özet. run_agent bunu sarmalayıp denetim günlüğü yazar."""
    if _should_stop(deps):
        return AgentRunResult(status="cancelled", spoken="Agent koşusu iptal edildi.", termination_reason="cancelled_before_planning")
    if _deadline_exceeded(deadline):
        return AgentRunResult(status="timed_out", spoken="Agent koşusunun süresi doldu.", termination_reason="deadline_before_planning")
    _emit_runtime(on_event, event_type="agent.planning.started", run_id=run_id,
                  status="running", message="Plan hazırlanıyor…")
    planning_started = time.monotonic()
    try:
        plan = deps.plan(request)
    except Exception as exc:  # noqa: BLE001 — plan üretilemedi → güvenli sesli fallback
        log.warning("Plan üretilemedi: %s", exc)
        _emit_runtime(on_event, event_type="agent.planning.failed", run_id=run_id,
                      status="failed", metadata={
                          "error_type": type(exc).__name__,
                          "duration_ms": round((time.monotonic() - planning_started) * 1000, 1),
                      })
        return AgentRunResult(status="failed", spoken="Planı oluşturamadım, tekrar dener misin?", termination_reason="planning_failed")

    _emit_runtime(on_event, event_type="agent.planning.completed", run_id=run_id,
                  status="completed", metadata={
                      "step_count": len(plan.steps),
                      "duration_ms": round((time.monotonic() - planning_started) * 1000, 1),
                  })
    if _should_stop(deps):
        return AgentRunResult(status="cancelled", spoken="Agent koşusu iptal edildi.", termination_reason="cancelled_after_planning")
    if _deadline_exceeded(deadline):
        return AgentRunResult(status="timed_out", spoken="Agent koşusunun süresi doldu.", termination_reason="deadline_after_planning")

    if not plan.steps:
        return AgentRunResult(status="empty", spoken=plan.summary or "Yapacak bir adım bulamadım.", termination_reason="empty_plan")

    steps = plan.steps[: deps.max_steps]
    total = len(steps)
    logs: list[StepLog] = []
    cancelled = False
    for i, step in enumerate(steps, 1):
        if _should_stop(deps):            # iptal: kalan adımları çalıştırma, nazikçe dur
            cancelled = True
            break
        if _deadline_exceeded(deadline):
            return AgentRunResult(status="timed_out", spoken="Agent koşusunun süresi doldu.", logs=tuple(logs),
                                  termination_reason="deadline_before_tool", interrupted_step=f"step_{i}")
        step_id = f"step_{i}"
        tool_started = time.monotonic()
        _emit_runtime(on_event, event_type="agent.tool.started", run_id=run_id,
                      step_id=step_id, status="running", metadata={"tool": step.tool},
                      message=f"{i}/{total} {step.tool}: {step.rationale}"[:90])
        try:
            step_log = _run_step(
                step, deps, run_id=run_id, step_id=step_id, on_event=on_event,
                deadline=deadline,
            )
            logs.append(step_log)
            _emit_runtime(on_event,
                          event_type="agent.tool.completed" if step_log.ok else "agent.tool.failed",
                          run_id=run_id, step_id=step_id,
                          status="completed" if step_log.ok else "failed",
                          metadata={
                              "tool": step.tool,
                              "duration_ms": round((time.monotonic() - tool_started) * 1000, 1),
                          })
        except Exception as exc:  # noqa: BLE001 — tek adım hatası koşuyu öldürmesin
            log.warning("Adım '%s' beklenmedik hata: %s", step.tool, exc)
            logs.append(StepLog(step.tool, step.params, step.rationale, RiskLevel.CONFIRM,
                                "error", ok=False, error=str(exc), verified=False))
            _emit_runtime(on_event, event_type="agent.tool.failed", run_id=run_id,
                          step_id=step_id, status="failed", metadata={
                              "tool": step.tool,
                              "error_type": type(exc).__name__,
                              "duration_ms": round((time.monotonic() - tool_started) * 1000, 1),
                          })

        if _deadline_exceeded(deadline):
            return AgentRunResult(status="timed_out", spoken="Agent koşusunun süresi doldu.", logs=tuple(logs),
                                  termination_reason="deadline_after_tool", interrupted_step=step_id)
        if _should_stop(deps):
            return AgentRunResult(status="cancelled", spoken="Agent koşusu iptal edildi.", logs=tuple(logs),
                                  termination_reason="cancelled_after_tool", interrupted_step=step_id)

    logs_t = tuple(logs)
    status = "cancelled" if cancelled else _status(logs_t)
    return AgentRunResult(
        status=status,
        spoken=_summary(plan, logs_t, deps),
        logs=logs_t,
        termination_reason="cancelled" if cancelled else None,
        interrupted_step=f"step_{len(logs) + 1}" if cancelled else None,
    )
