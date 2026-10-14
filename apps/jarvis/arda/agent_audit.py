"""Agent koşu denetim günlüğü — otonom, dosyaya-dokunan agent'ın izini kalıcılaştırır.

"Jarvis dosyalarıma ne yaptı?" sorusunu yanıtlar: her koşu (istek + durum + her adımın
ne/karar/ok/doğrulama/çıktı) günlük bir markdown dosyasına EKLENİR (üzerine yazmaz).
Vault-dostu (Obsidian'da okunur). write_run_log disk hatasında raise edebilir → çağıran
(executor) güvenle yutar (denetim kozmetik; koşuyu bozmamalı).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable

from arda.agent_models import AgentRunResult


def _cell(text: str) -> str:
    """Markdown tablo hücresi için güvenli metin: pipe kaçışla, tek satır, kırpılmış."""
    return (text or "").replace("\n", " ").replace("|", "\\|")[:120]


def _params_summary(params: dict) -> str:
    """Adımın anlamlı parametrelerini tek satıra özetle → audit "NE çalıştı"yı gösterir.

    En riskli araçlarda kritik: run_applescript'in script'i, ui_type'ın metni,
    safe_shell'in komutu, write_file'ın path'i artık günlükte görünür ("Jarvis ne yaptı?").
    'content' (büyük dosya gövdesi) yalnızca uzunlukla gösterilir → günlük şişmez."""
    if not params:
        return ""
    parts = [
        f"content[{len(str(v))} kr]" if k == "content" else f"{k}={v}"
        for k, v in params.items()
    ]
    return _cell(", ".join(parts))


def format_run(request: str, result: AgentRunResult, *, now: Callable[[], datetime] = datetime.now) -> str:
    """Bir koşuyu insan-okur markdown bloğuna çevir (başlık + istek + özet + adım tablosu)."""
    ts = now().isoformat(timespec="seconds")
    lines = [
        f"## {ts} — {result.status}",
        "",
        f"**İstek:** {request}",
        "",
        f"**Özet:** {result.spoken}",
        "",
    ]
    if result.run_id:
        lines.extend([
            f"**Run ID:** {result.run_id}",
            f"**Başlangıç:** {result.started_at or ''}",
            f"**Süre (ms):** {result.duration_ms if result.duration_ms is not None else ''}",
            f"**Tamamlanan adım:** {result.completed_steps}",
            f"**Kesilen adım:** {result.interrupted_step or ''}",
            f"**Sonlanma nedeni:** {result.termination_reason or ''}",
            "",
        ])
    if result.skill_id:
        lines.extend([
            "**Skill:** " + result.skill_id,
            "**Skill kaynağı:** " + (result.skill_source or ""),
            "**Skill içerik hash'i:** " + (result.skill_hash or ""),
            "",
        ])
    if result.logs:
        lines.append("| # | Araç | Param | Karar | OK | Doğrulandı | Çıktı/Hata |")
        lines.append("|---|------|-------|-------|----|-----------|------------|")
        for i, l in enumerate(result.logs, 1):
            detail = _cell(l.output or l.error)
            lines.append(
                f"| {i} | {l.tool} | {_params_summary(l.params)} | {l.decision} | "
                f"{'✓' if l.ok else '✗'} | {'✓' if l.verified else '✗'} | {detail} |"
            )
    else:
        lines.append("_(adım yok)_")
    lines.append("")
    return "\n".join(lines)


def write_run_log(
    request: str, result: AgentRunResult, *, log_dir, now: Callable[[], datetime] = datetime.now
) -> Path:
    """Koşuyu günlük markdown dosyasına EKLE (agent-YYYY-MM-DD.md). Klasör yoksa oluşturur.

    Aynı gün birden çok koşu üzerine yazılmaz: var olan dosyaya `---` ayracıyla eklenir."""
    folder = Path(log_dir)
    folder.mkdir(parents=True, exist_ok=True)
    day = now().date().isoformat()
    path = folder / f"agent-{day}.md"
    is_new = not path.exists()
    with path.open("a", encoding="utf-8") as f:
        if is_new:
            f.write(f"# Agent Koşu Günlüğü — {day}\n\n")
        else:
            f.write("\n---\n\n")
        f.write(format_run(request, result, now=now))
    return path
