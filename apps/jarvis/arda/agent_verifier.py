"""Adım doğrulayıcı — bir aracın iddia ettiği etkinin gerçekten olduğunu kontrol eder.

Yürütme + sonuç ≠ doğrulama: write_file "ok" dönebilir ama disk'te yanlış içerik olabilir.
verify_step yan-etki bırakmadan (salt-okuma) sonucu teyit eder. ASLA raise etmez →
executor için güvenli bir bool döndürür.
"""
from __future__ import annotations

from arda.agent_models import PlanStep, ToolResult
from arda.agent_tools import ToolContext, resolve_in_workspace


def verify_step(step: PlanStep, result: ToolResult, ctx: ToolContext) -> tuple[bool, str]:
    """(verified, açıklama). Başarısız sonuç asla doğrulanmaz; write_file re-read ile teyit edilir."""
    if not result.ok:
        return False, "araç başarısız döndü"

    if step.tool in ("write_file", "edit_file"):
        try:
            target = resolve_in_workspace(ctx.workspace, step.params.get("path", ""))
            actual = target.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 — doğrulama hatası = doğrulanamadı (raise yok)
            return False, f"doğrulama için okunamadı: {exc}"
        # write_file: beklenen = params.content. edit_file: beklenen = sonuç içeriği (data.content).
        if step.tool == "edit_file":
            expected = result.data.get("content") if isinstance(result.data, dict) else None
            if expected is None:
                return False, "doğrulama içeriği yok"
        else:
            expected = step.params.get("content", "")
        if actual == expected:
            return True, "diskteki içerik doğrulandı"
        return False, "diskteki içerik beklenenle uyuşmuyor"

    if step.tool == "read_file":
        has_content = isinstance(result.data, dict) and "content" in result.data
        return (has_content, "okuma doğrulandı" if has_content else "içerik yok")

    if step.tool == "browser_screenshot":
        try:
            target = resolve_in_workspace(ctx.workspace, step.params.get("path", "browser-screenshot.png"))
            return (target.is_file() and target.stat().st_size > 0, "screenshot dosyası doğrulandı")
        except Exception as exc:  # noqa: BLE001
            return False, f"screenshot doğrulanamadı: {exc}"

    # Salt-okuma/çalıştırma araçları: ok=True yeterli teyit.
    return True, "ok"
