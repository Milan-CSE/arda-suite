"""İzin katmanı — bir (araç, params) çağrısını auto | confirm | deny olarak sınıflar.

Politika tek yerde (executor bu kararla kapı tutar):
- auto: salt-okuma/listeleme/arama/test → onaysız.
- confirm: yazma/kurulum/commit/dış-veri → kullanıcı onayı (native dialog).
- deny: sır/credential okuma, destructive delete, sudo, izinsiz mail → asla çalışmaz.

Temel risk araç kayıt defterinden (agent_tools.REGISTRY) gelir; sır-yolu ve shell komutu
gibi DİNAMİK durumlar burada yükseltilir/düşürülür. Yıkıcı shell kalıpları için
agent_tools.is_hard_denied tek kaynak olarak kullanılır (savunma derinliği orada da var).
"""
from __future__ import annotations

import re
import shlex

from arda.agent_models import RiskLevel
# sensitive_path tek kaynak agent_tools'ta (alt katman) → araçlar + izin katmanı paylaşır,
# döngüsel import olmaz. Buradan yeniden ihraç: `from arda.agent_permission import sensitive_path` korunur.
from arda.agent_tools import REGISTRY, is_hard_denied, sensitive_path

# İlk token'a göre shell komutu sınıfı. Liste dışı her ikili → muhafazakâr CONFIRM.
_SHELL_ALLOW = {
    "ls", "cat", "pwd", "echo", "grep", "egrep", "fgrep", "rg", "find", "head", "tail",
    "wc", "which", "type", "date", "du", "df", "tree", "stat", "diff", "file", "sort",
    "uniq", "cut", "awk", "pytest", "true", "basename", "dirname", "realpath",
}
_SHELL_MUTATE = {
    "mkdir", "touch", "cp", "mv", "rm", "ln", "tee", "sed", "chmod", "chown",
    "npm", "pnpm", "yarn", "pip", "pip3", "brew", "make", "python", "python3",
    "node", "deno", "bun", "cargo", "go", "install",
}
# git alt-komutuna göre okuma vs yazma. "config" YOK: git config yazabilir
# (git config user.email …) → okuma sanılıp onaysız çalışmasın, CONFIRM'a düşsün.
_GIT_READ = {"status", "log", "diff", "show", "branch", "remote", "ls-files", "blame", "describe"}

# AUTO listesindeki güvenli komutlar bile bu tehlikeli alt-kalıpları içeriyorsa CONFIRM'a
# YÜKSELT (DENY değil — kullanıcı native dialog'la onaylayabilir): find -exec/-delete
# (her eşleşmede komut çalıştırır/siler, ilk-token sınıflamasını atlar), awk/perl system()
# (kabuk sandbox'ına kaçış). Gerçekten yıkıcı biçimler (rm -rf, sudo…) zaten hard-deny.
_AUTO_ESCALATE_RE = re.compile(r"-exec|-delete|\bsystem\s*\(", re.IGNORECASE)

# AppleScript `do shell script "<cmd>"` — gömülü kabuk komutunu yakala (kaçışlı tırnak dahil).
# Bu payload `classify_shell`'e verilir → sır-yolu/yıkıcı kalıplar AppleScript üzerinden de yakalanır.
_DO_SHELL_RE = re.compile(r'do\s+shell\s+script\s+"((?:[^"\\]|\\.)*)"', re.IGNORECASE)


def classify_shell(command: str) -> RiskLevel:
    """Bir shell komutunu auto | confirm | deny olarak sınıfla (ilk token + içerik)."""
    command = command or ""
    if is_hard_denied(command):
        return RiskLevel.DENY
    try:
        tokens = shlex.split(command)
    except ValueError:
        return RiskLevel.CONFIRM           # tırnak/sözdizimi bozuk → güvenli tarafta kal
    if not tokens:
        return RiskLevel.CONFIRM
    # Komut bir sır-yoluna dokunuyorsa (cat ~/.ssh/id_rsa) → reddet.
    if any(sensitive_path(t) for t in tokens[1:]):
        return RiskLevel.DENY
    base = tokens[0].rsplit("/", 1)[-1]    # /usr/bin/ls → ls
    if base == "git":
        sub = tokens[1] if len(tokens) > 1 else ""
        return RiskLevel.AUTO if sub in _GIT_READ else RiskLevel.CONFIRM
    if base in _SHELL_ALLOW:
        # Güvenli komut bile tehlikeli alt-kalıp içeriyorsa (find -exec/-delete, system()) →
        # otomatik çalıştırma, onay iste.
        if _AUTO_ESCALATE_RE.search(command):
            return RiskLevel.CONFIRM
        return RiskLevel.AUTO
    if base in _SHELL_MUTATE:
        return RiskLevel.CONFIRM
    return RiskLevel.CONFIRM                # bilinmeyen ikili → onay iste


def decide(tool: str, params: dict | None) -> RiskLevel:
    """(araç, params) çağrısının risk seviyesi. executor bu kararla kapı tutar."""
    params = params or {}
    if tool == "safe_shell":
        return classify_shell(str(params.get("command", "")))
    if tool == "run_applescript":
        # AppleScript `do shell script` kabuk sandbox'ını ATLAR → gömülü komutu shell
        # sınıflandırıcıdan geçir: sır-yolu (cat ~/.ssh/id_rsa) ya da yıkıcı kalıp (rm -rf,
        # sudo) DENY döner → AppleScript bunları CONFIRM'a İNDİREMEZ. Komut literal değilse
        # (değişkenden kuruluysa) çıkaramayız; saf AppleScript gibi muhafazakâr CONFIRM kalır.
        script = str(params.get("script", ""))
        has_do_shell = "do shell script" in script.lower()
        # is_hard_denied SHELL kalıpları içindir (rm -rf, sudo, mail…) — saf AppleScript'e
        # uygulanırsa 'tell application "Mail"' gibi ifadeler yanlışlıkla eşleşir. Bu yüzden
        # yalnız gerçek `do shell script` varken (değişkenden kurulmuş komutları da kapsar).
        if has_do_shell and is_hard_denied(script):
            return RiskLevel.DENY
        # Literal gömülü komutları sır-yolu/sınıf için shell sınıflandırıcıdan geçir.
        for inner in _DO_SHELL_RE.findall(script):
            if classify_shell(inner) is RiskLevel.DENY:
                return RiskLevel.DENY
        return RiskLevel.CONFIRM
    # Sır-yolu yükseltmesi: path/file alanı bir credential'a işaret ediyorsa reddet.
    for key in ("path", "file"):
        if key in params and sensitive_path(params[key]):
            return RiskLevel.DENY
    spec = REGISTRY.get(tool)
    if spec is None:
        return RiskLevel.CONFIRM            # bilinmeyen araç → muhafazakâr
    return spec.risk
