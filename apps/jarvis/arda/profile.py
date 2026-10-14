"""Kullanıcı profili — statik config 'profile:' bloğunu üç tüketiciye saf metin sunar.

STT (vocabulary → whisper initial_prompt), router (about + komut few-shot), answer (about).
Blok yoksa/bozuksa hepsi "" → özellik kapalı, mevcut davranış birebir. Hiç raise etmez.
"""
from __future__ import annotations


def _profile(cfg: dict) -> dict:
    p = (cfg or {}).get("profile", {})
    return p if isinstance(p, dict) else {}


def vocabulary(cfg: dict) -> str:
    """STT ipucu: isimler/proje adları/terimler (whisper initial_prompt). Yoksa ""."""
    v = _profile(cfg).get("vocabulary", "")
    return v.strip() if isinstance(v, str) else ""


def about_block(cfg: dict) -> str:
    """Beyne 'Kullanıcı hakkında' bağlamı (system prompt eki). Yoksa ""."""
    a = _profile(cfg).get("about", "")
    a = a.strip() if isinstance(a, str) else ""
    return f"\n\nKullanıcı hakkında (cevabında dikkate al):\n{a}" if a else ""


def command_examples(cfg: dict) -> str:
    """Router few-shot: kullanıcının gerçek komut kalıpları. Yoksa "".

    commands: [{say: "...", intent: "..."}] → '- "say" -> intent' satırları."""
    cmds = _profile(cfg).get("commands", [])
    if not isinstance(cmds, list):
        return ""
    lines = []
    for c in cmds:
        if isinstance(c, dict):
            say = str(c.get("say", "")).strip()
            intent = str(c.get("intent", "")).strip()
            if say and intent:
                lines.append(f'- "{say}" -> {intent}')
    if not lines:
        return ""
    return "\n\nKullanıcının sık komutları (niyet ipucu):\n" + "\n".join(lines)
