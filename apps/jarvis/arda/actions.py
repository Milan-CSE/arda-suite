"""Mac eylemleri — KÜRATÖRLÜ beyaz liste (asla keyfi shell yok).

Güvenlik modeli (kullanıcı kararı): LLM yalnızca buradaki sabit eylem adlarından
birini seçebilir. Her eylem `subprocess` çağrısını **argüman listesiyle** yapar
(`shell=False`) → komut enjeksiyonu yok. Girdiler ayrıca doğrulanır/sınırlanır.

Yeni yetenek eklemek = buraya yeni bir handler + ACTION_SPECS girdisi yazmak.
`run_action` asla raise etmez: bilinmeyen/başarısız eylemde sesli, güvenli bir
mesaj döndürür (pipeline'ı bozmaz).
"""
from __future__ import annotations

import logging
import re
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from arda.models import ActionResult
from arda.platform_runtime import open_app_command, open_url_command

log = logging.getLogger(__name__)

_RUN_TIMEOUT_SEC = 10.0
_MAX_TIMER_SEC = 24 * 60 * 60  # 24 saat — kazara dev sayaç kurulmasın


def _default_run(args: list[str]) -> subprocess.CompletedProcess:
    """Argüman listesiyle komut çalıştır (shell yok). Çıktıyı yakalar, raise etmez."""
    return subprocess.run(
        args, capture_output=True, text=True, timeout=_RUN_TIMEOUT_SEC, check=False
    )


def _default_schedule(seconds: float, callback: Callable[[], None]) -> None:
    """Geri çağrımı `seconds` sonra arka planda çalıştır (sayaç için).

    DİKKAT: daemon thread → SÜREÇ-İÇİ ve KALICI DEĞİL. Uygulama kapanırsa/çökerse
    sayaç sessizce kaybolur. Yeniden başlatmaya dayanması gereken hatırlatmalar için
    `set_reminder` (Reminders.app) kullanılır. Bu fark bilinçli: sayaç = kısa/anlık,
    reminder = kalıcı. (Mimari: docs/ARCHITECTURE.md "Bilinen kısıtlar".)"""
    timer = threading.Timer(seconds, callback)
    timer.daemon = True
    timer.start()


@dataclass
class ActionDeps:
    """Eylemlerin yan etkileri — test için enjekte edilebilir (gerçek IO mock'lanır)."""

    run: Callable = _default_run                 # (args) -> CompletedProcess
    schedule: Callable = _default_schedule       # (seconds, callback) -> None
    say: Callable[[str], None] = lambda _t: None  # sayaç dolunca sesli haber
    now: Callable[[], datetime] = datetime.now    # saat (test'te sabitlenebilir)


# --- eylem handler'ları: (params, deps) -> sesli onay metni ---

# Handler'lar TİPLİ + DOĞRULANMIŞ param alır (run_action önce missing_params +
# coerce_params'tan geçirir): name dolu str, url http/https str, level/seconds int.
# Burada yalnızca anlamsal/aralık denetimi + IO kalır (tip parse'ı YOK → DRY).

_APP_ALIASES: dict[str, str] = {
    "chrome": "Google Chrome",
    "google chrome": "Google Chrome",
    "vscode": "Visual Studio Code",
    "vs code": "Visual Studio Code",
    "code": "Visual Studio Code",
    "word": "Microsoft Word",
    "excel": "Microsoft Excel",
    "powerpoint": "Microsoft PowerPoint",
    "terminal": "Terminal",
    "iterm": "iTerm",
    "iterm2": "iTerm",
    "finder": "Finder",
    "safari": "Safari",
    "spotify": "Spotify",
    "hesap makinesi": "Calculator",
    "calculator": "Calculator",
    "notlar": "Notes",
    "notes": "Notes",
    "ayarlar": "System Settings",
    "sistem ayarları": "System Settings",
    "settings": "System Settings",
    "system settings": "System Settings",
    "takvim": "Calendar",
    "calendar": "Calendar",
    "müzik": "Music",
    "music": "Music",
    "posta": "Mail",
    "mail": "Mail",
    "mesajlar": "Messages",
    "messages": "Messages",
    "harita": "Maps",
    "haritalar": "Maps",
    "maps": "Maps",
    "fotoğraflar": "Photos",
    "photos": "Photos",
    "anımsatıcılar": "Reminders",
    "reminders": "Reminders",
    "obsidian": "Obsidian",
    "slack": "Slack",
    "discord": "Discord",
    "telegram": "Telegram",
    "whatsapp": "WhatsApp",
    "zoom": "zoom.us",
}


_WEB_FALLBACKS: dict[str, str] = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "github": "https://github.com",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "chatgpt": "https://chatgpt.com",
    "netflix": "https://www.netflix.com",
    "gmail": "https://mail.google.com",
    "ekşi": "https://eksisozluk.com",
    "ekşisözlük": "https://eksisozluk.com",
}


def resolve_app_name(name: str) -> str:
    """Uygulama adını macOS bundle adına veya kurulu uygulamaya eşle.

    Örn. 'chrome' → 'Google Chrome', 'vscode' → 'Visual Studio Code'.
    Bulunamazsa girilen adı döndürür (open -a doğrudan dener).
    """
    cleaned = name.strip()
    low = cleaned.lower()
    if low in _APP_ALIASES:
        return _APP_ALIASES[low]
    dirs = [
        Path("/Applications"),
        Path("/System/Applications"),
        Path("/System/Applications/Utilities"),
        Path.home() / "Applications",
    ]
    for d in dirs:
        if not d.exists():
            continue
        for p in d.glob("*.app"):
            if p.stem.lower() == low:
                return p.stem
    for d in dirs:
        if not d.exists():
            continue
        for p in d.glob("*.app"):
            if low in p.stem.lower():
                return p.stem
    return cleaned


def _open_app(params: dict, deps: ActionDeps) -> str:
    name = params["name"]
    resolved = resolve_app_name(name)
    command = open_app_command(resolved)
    if command is None:
        return "Bu platformda uygulama açma desteklenmiyor."
    res = deps.run(command)
    if res.returncode != 0 and resolved != name:
        fallback = open_app_command(name)
        if fallback is not None:
            res = deps.run(fallback)
    if res.returncode != 0:
        low = name.strip().lower()
        if low in _WEB_FALLBACKS:
            url_command = open_url_command(_WEB_FALLBACKS[low])
            if url_command is None:
                return "Bu platformda web tarayıcısı açılamıyor."
            deps.run(url_command)
            return f"{name.capitalize()} web sitesi açılıyor."
        return f"{name} uygulamasını bulamadım."
    return f"{resolved} açılıyor."


def _open_url(params: dict, deps: ActionDeps) -> str:
    command = open_url_command(params["url"])
    if command is None:
        return "Bu platformda web tarayıcısı açılamıyor."
    deps.run(command)  # şema 'url' tipi http/https'i coerce'de doğruladı
    return "Açıyorum."


def _set_volume(params: dict, deps: ActionDeps) -> str:
    level = max(0, min(100, params["level"]))  # int (coerce edildi); aralığa sıkıştır
    deps.run(["osascript", "-e", f"set volume output volume {level}"])
    return f"Ses yüzde {level}."


def _lock_screen(_params: dict, deps: ActionDeps) -> str:
    deps.run(["pmset", "displaysleepnow"])
    return "Ekranı kapatıyorum."


def _tell_time(_params: dict, deps: ActionDeps) -> str:
    return f"Saat {deps.now().strftime('%H:%M')}."


def _search_files(params: dict, deps: ActionDeps) -> str:
    query = params["query"]
    home = str(Path.home())
    res = deps.run(["mdfind", "-onlyin", home, "-name", query])
    hits = [line for line in res.stdout.splitlines() if line.strip()]
    if not hits:
        return f"'{query}' için dosya bulamadım."
    first = Path(hits[0]).name
    if len(hits) == 1:
        return f"Bir dosya buldum: {first}."
    return f"{len(hits)} dosya buldum, ilki {first}."


def _set_timer(params: dict, deps: ActionDeps) -> str:
    """Süreç-içi geri sayım sayacı kur (params.seconds, opsiyonel params.label).

    KALICI DEĞİL: uygulama kapanırsa sayaç kaybolur (bkz. _default_schedule). Kalıcı,
    yeniden-başlatmaya dayanan hatırlatmalar için set_reminder (Reminders.app)."""
    seconds = params["seconds"]  # int (coerce edildi)
    if seconds <= 0 or seconds > _MAX_TIMER_SEC:
        return "Sayaç süresi geçersiz."
    label = params.get("label", "")  # opsiyonel

    def _fire() -> None:
        deps.say(f"{label} sayacı doldu." if label else "Sayaç doldu.")

    deps.schedule(seconds, _fire)
    minutes = seconds // 60
    if minutes:
        return f"{minutes} dakikalık sayaç kurdum."
    return f"{seconds} saniyelik sayaç kurdum."


def _as_applescript_str(text: str) -> str:
    """Metni güvenli bir AppleScript string literal'ına çevir.

    osascript arg-listesiyle çalıştırılır (shell yok); tek risk AppleScript string
    içinden kaçıştır → ters-bölü ve çift tırnak escape edilir (komut enjeksiyonu yok)."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _set_reminder(params: dict, deps: ActionDeps) -> ActionResult:
    """Belirli bir zamanda kalıcı macOS hatırlatıcısı kur (Reminders.app).

    Mutlak tarih AppleScript'e string olarak değil, `(current date) + delta_saniye`
    olarak verilir → locale/tarih-biçimi sorunları yok. Sayaçtan farkı: süreç kapansa
    bile sistemde durur. İlk kullanımda macOS otomasyon (Reminders) izni isteyebilir.

    Oluşturulan reminder'ın id'sini yakalayıp ActionResult.memory'ye koyar → sonradan
    'az önce kurduğumu sil' atıfı belleğden çözülebilir (bkz. delete_reminder)."""
    at = params["at"]      # datetime (coerce edildi)
    text = params["text"]
    delta = int((at - deps.now()).total_seconds())
    if delta <= 0:
        return ActionResult("O zaman çoktan geçti, ileri bir zaman söyle.")
    script = (
        'tell application "Reminders"\n'
        f"  set r to make new reminder with properties {{name:{_as_applescript_str(text)}, "
        f"remind me date:(current date) + {delta}}}\n"
        "  return id of r\n"
        "end tell"
    )
    res = deps.run(["osascript", "-e", script])
    if res.returncode != 0:
        return ActionResult(
            "Hatırlatıcıyı kuramadım (Reminders izni gerekebilir).",
            {"action": "set_reminder", "success": False, "text": text, "at": at.isoformat()},
        )
    return ActionResult(
        f"Tamam, {at.strftime('%H:%M')} için hatırlatıcı kurdum: {text}",
        {"action": "set_reminder", "success": True, "text": text,
         "at": at.isoformat(), "id": (res.stdout or "").strip()},
    )


def _delete_reminder(params: dict, deps: ActionDeps) -> ActionResult:
    """Bir hatırlatıcıyı sil (Reminders.app). id verilirse kesin eşleşme, yoksa başlık.

    'Az önce kurduğumu sil' gibi atıflar: router geçmişteki set_reminder action_result'ından
    (bkz. brain._build_messages) başlığı/id'yi okuyup params'a yazar; bu handler yalnızca siler.
    set_reminder ile aynı güvenlik kalıbı: osascript arg-listesiyle (shell yok), string kaçışlı."""
    title = params["title"]
    rid = params.get("id", "")
    selector = f"id is {_as_applescript_str(rid)}" if rid else f"name is {_as_applescript_str(title)}"
    script = (
        'tell application "Reminders"\n'
        f"  set matches to (reminders whose {selector})\n"
        "  set n to (count of matches)\n"
        "  repeat with r in matches\n"
        "    delete r\n"
        "  end repeat\n"
        "  return n\n"
        "end tell"
    )
    res = deps.run(["osascript", "-e", script])
    if res.returncode != 0:
        return ActionResult("Hatırlatıcıyı silemedim (izin gerekebilir).",
                            {"action": "delete_reminder", "success": False, "title": title})
    if (res.stdout or "").strip() in ("", "0"):
        return ActionResult(f"'{title}' diye bir hatırlatıcı bulamadım.",
                            {"action": "delete_reminder", "success": False, "title": title})
    return ActionResult(f"'{title}' hatırlatıcısını sildim.",
                        {"action": "delete_reminder", "success": True, "title": title})


def _list_reminders(_params: dict, deps: ActionDeps) -> str:
    """Bekleyen (tamamlanmamış) hatırlatıcıları oku ve answered-tarzı sesle özetle.

    Salt-okunur → yapılandırılmış bellek gerekmez (düz str döner). osascript arg-listesi."""
    script = (
        'tell application "Reminders"\n'
        "  set out to {}\n"
        "  repeat with r in (reminders whose completed is false)\n"
        "    set end of out to name of r\n"
        "  end repeat\n"
        "  return out\n"
        "end tell"
    )
    res = deps.run(["osascript", "-e", script])
    if res.returncode != 0:
        return "Hatırlatıcıları okuyamadım (izin gerekebilir)."
    items = [s.strip() for s in (res.stdout or "").split(",") if s.strip()]
    if not items:
        return "Bekleyen hatırlatıcın yok."
    if len(items) == 1:
        return f"Bir hatırlatıcın var: {items[0]}."
    return f"{len(items)} hatırlatıcın var: " + ", ".join(items) + "."


_HANDLERS: dict[str, Callable[[dict, ActionDeps], "str | ActionResult"]] = {
    "open_app": _open_app,
    "open_url": _open_url,
    "set_volume": _set_volume,
    "lock_screen": _lock_screen,
    "tell_time": _tell_time,
    "search_files": _search_files,
    "set_timer": _set_timer,
    "set_reminder": _set_reminder,
    "delete_reminder": _delete_reminder,
    "list_reminders": _list_reminders,
}

# Beyin (brain.py) bu açıklamaları sistem prompt'una koyar → LLM yalnızca bu
# adlardan birini seçebilir. Tek kaynak: yeni eylem eklenince prompt da güncel kalır.
ACTION_SPECS: dict[str, str] = {
    "open_app": "bir uygulamayı aç (params.name = uygulama adı)",
    "open_url": "bir web adresini aç (params.url = http/https adres)",
    "set_volume": "ses seviyesini ayarla (params.level = 0-100)",
    "lock_screen": "ekranı kilitle/kapat (params yok)",
    "tell_time": "saati söyle (params yok)",
    "search_files": "dosya ara (params.query = aranacak ad)",
    "set_timer": "kısa sayaç kur (params.seconds = saniye, params.label = isim opsiyonel)",
    "set_reminder": "belirli bir zamanda hatırlat — kalıcı (params.at = ISO 8601 tarih-saat, params.text = hatırlatma metni)",
    "delete_reminder": "bir hatırlatıcıyı sil (params.title = hatırlatıcı başlığı; params.id opsiyonel kesin eşleşme)",
    "list_reminders": "bekleyen hatırlatıcıları say/oku (params yok)",
}

ACTION_NAMES: tuple[str, ...] = tuple(_HANDLERS)


@dataclass(frozen=True)
class ParamSpec:
    """Bir eylem parametresinin sözleşmesi. `type` doğrulama/coercion ipucudur
    (str | int | url); değerler LLM sınırından hep string gelir, asıl tip dönüşümü
    handler içinde yapılır (bkz. _set_volume, _set_timer)."""

    name: str
    type: str = "str"
    required: bool = True


# Her eylemin parametre sözleşmesi — `missing_params` doğrulaması ve (gelecekte)
# per-action schema için tek kaynak. Params'ı genel string-map tutmak (additionalProperties
# string) schema'yı kararlı kılar; tip/zorunluluk denetimi burada merkezîleşir →
# "open_app ama isim yok → runtime'da patla" yerine kibar sesli soru döner.
ACTION_PARAMS: dict[str, tuple[ParamSpec, ...]] = {
    "open_app": (ParamSpec("name"),),
    "open_url": (ParamSpec("url", "url"),),
    "set_volume": (ParamSpec("level", "int"),),
    "lock_screen": (),
    "tell_time": (),
    "search_files": (ParamSpec("query"),),
    "set_timer": (ParamSpec("seconds", "int"), ParamSpec("label", required=False)),
    "set_reminder": (ParamSpec("at", "datetime"), ParamSpec("text")),
    "delete_reminder": (ParamSpec("title"), ParamSpec("id", required=False)),
    "list_reminders": (),
}

# Zorunlu parametre eksikse sorulacak kibar, eyleme özel metin.
_MISSING_PROMPTS: dict[str, str] = {
    "open_app": "Hangi uygulamayı açayım?",
    "open_url": "Hangi adresi açayım?",
    "set_volume": "Sesi kaça ayarlayayım?",
    "search_files": "Ne arayayım?",
    "set_timer": "Kaç saniyelik sayaç kurayım?",
    "set_reminder": "Neyi ne zaman hatırlatayım?",
    "delete_reminder": "Hangi hatırlatıcıyı sileyim?",
}


def missing_params(name: str, params: dict) -> list[str]:
    """Eylem için verilmesi zorunlu ama eksik/boş olan parametre adlarını döndür."""
    specs = ACTION_PARAMS.get(name, ())
    return [s.name for s in specs if s.required and not str(params.get(s.name, "")).strip()]


def _coerce_value(spec: ParamSpec, raw: object) -> tuple[object | None, str | None]:
    """Ham string param'ı spec.type'a çevir → (değer, None) ya da (None, hata_metni).

    Tip dönüşümü TEK yerde (DRY): handler'lar artık parse etmez. Yeni tip eklemek
    (ör. 'date', 'bool') yalnızca buraya bir dal eklemek demek."""
    text = str(raw).strip()
    if spec.type == "int":
        low = text.lower()
        if low in ("kapat", "sessiz", "sıfır", "mute"):
            return 0, None
        try:
            return int(float(text)), None
        except (TypeError, ValueError):
            digits = re.findall(r"\d+", text)
            if digits:
                return int(digits[0]), None
            return None, f"'{text}' bir sayı değil, anlayamadım."
    if spec.type == "url":
        if not text.startswith(("http://", "https://")):
            return None, "Sadece web adreslerini açabilirim."
        return text, None
    if spec.type == "datetime":
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None, "Zamanı anlayamadım, ne zaman olduğunu netleştirir misin?"
        return dt.replace(tzinfo=None), None  # yerel kabul et → naive now ile çıkarılır
    return text, None  # "str" — olduğu gibi (kırpılmış)


def coerce_params(name: str, params: dict) -> tuple[dict, str | None]:
    """params'ı ACTION_PARAMS'a göre TİPLİ sözlüğe çevir → (typed, None) | ({}, hata).

    Güvenlik: şemada TANIMLI OLMAYAN anahtarlar düşürülür → model beklenmeyen bir
    parametre sızdıramaz. Eksik (ama opsiyonel) param atlanır; zorunlu eksikler
    `missing_params` ile run_action'da daha önce yakalanır."""
    typed: dict = {}
    for spec in ACTION_PARAMS.get(name, ()):
        if not str(params.get(spec.name, "")).strip():
            continue  # yok/boş → opsiyonelse atla (zorunlu zaten missing_params'ta)
        value, err = _coerce_value(spec, params[spec.name])
        if err:
            return {}, err
        typed[spec.name] = value
    return typed, None


def _dispatch(name: str, params: dict, deps: ActionDeps) -> ActionResult:
    """Eylemi çalıştır → ActionResult. ASLA raise etmez (tek dispatch noktası).

    Sıra: bilinmeyen ad → güvenli 'yapamam'; zorunlu param eksik → kibar soru;
    sonra handler (tip/biçim doğrulaması + IO). Handler str ya da ActionResult
    döndürebilir; str ise belleksiz (memory=None) ActionResult'a sarılır."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return ActionResult(f"Bunu henüz yapamıyorum ({name}).")
    missing = missing_params(name, params)
    if missing:
        # Eksik zorunlu param → IO'ya hiç gitme, kullanıcıya kibarca sor (issue #3).
        return ActionResult(_MISSING_PROMPTS.get(name, f"{name} için eksik bilgi var: {', '.join(missing)}."))
    typed, err = coerce_params(name, params)
    if err:
        # Param yanlış tipte/biçimde (ör. ses "yüksek", url "file://") → handler'a gitme.
        return ActionResult(err)
    try:
        outcome = handler(typed, deps)
    except Exception as exc:  # noqa: BLE001 — eylem hatası sesli mesaja düşer, pipeline ölmez
        log.warning("Eylem '%s' başarısız: %s", name, exc)
        return ActionResult("Bu işi yaparken bir sorun çıktı.", {"action": name, "success": False})
    return outcome if isinstance(outcome, ActionResult) else ActionResult(outcome)


def run_action(name: str, params: dict | None = None, *, deps: ActionDeps | None = None) -> str:
    """Beyaz listedeki bir eylemi çalıştır → sesli onay metni (str). ASLA raise etmez.

    Geriye uyumlu sade arabirim: yalnızca sesli metni döndürür. Yapılandırılmış
    bellek (atıf çözümü için) gereken çağıran `run_action_result` kullanır."""
    return _dispatch(name, params or {}, deps or ActionDeps()).spoken


def run_action_result(name: str, params: dict | None = None, *, deps: ActionDeps | None = None) -> ActionResult:
    """`run_action`'ın yapılandırılmış sürümü: sesli metin + (varsa) bellek payload'ı.

    Pipeline bunu kullanır → eylem sonucu sohbet belleğine yazılır ve sonraki turlarda
    hem router hem answer motoruna bağlam olur ('az önce kurduğum hatırlatıcı…')."""
    return _dispatch(name, params or {}, deps or ActionDeps())
