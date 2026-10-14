"""Türkçe göreli zaman ifadelerini deterministik olarak mutlak ISO 8601'e çözer.

Amaç: 'yarın 8'de', '2 saat sonra', '10 dakika sonra' gibi ifadelerin tarih-saatini
LLM'in aritmetiğine/biçimine bırakmamak. LLM göreli zamanı sık yanlış hesaplar; burada
DİL-BİLGİSEL (gramer/regex) bir parser ile belirsiz-olmayan kısımları zorlarız:

- Ofsetler ('X dakika/saat sonra', 'yarım saat sonra') → tam override (belirsizlik yok).
- Gün işareti + daypart + saat ('yarın akşam 8') → tam override (am/pm net).
- Gün işareti + çıplak saat → fallback (LLM) saatini KORU, yalnız yanlış GÜNÜ düzelt
  (am/pm'i LLM bağlamla daha iyi bilir; biz deterministik olduğumuz yeri zorlarız).
- Tanınmayan ifade → None (çağıran LLM değerini korur).

Saf modül (saat/IO yok): `now` enjekte edilir → test edilebilir, app'te datetime.now().
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

# Türkçe sayı sözcükleri (sık kullanılanlar) → çarpan. 'yarım' = 0.5 (yarım saat).
_NUM_WORDS = {
    "yarım": 0.5, "bir": 1, "iki": 2, "üç": 3, "dört": 4, "beş": 5,
    "altı": 6, "yedi": 7, "sekiz": 8, "dokuz": 9, "on": 10,
}

# Günün bölümü → çıplak saate uygulanacak kayma kuralı.
_DAYPARTS = ("sabah", "öğlen", "öğle", "akşam", "gece")

_OFFSET_RE = re.compile(
    r"(?P<num>\d+(?:[.,]\d+)?|" + "|".join(_NUM_WORDS) + r")\s*"
    r"(?P<unit>saat|dakika|dk)\s+sonra"
)
_XDAYS_RE = re.compile(r"(\d+)\s*gün\s+sonra")
# Çıplak saat: 'saat 9', '9:30', '17:00', "9'da" / '8 de' (locative). 'X gün' eşleşmez
# (rakamdan sonra 'gün' gelir, locative değil).
_CLOCK_RES = (
    re.compile(r"saat\s*(\d{1,2})(?::(\d{2}))?"),
    re.compile(r"(\d{1,2}):(\d{2})"),
    re.compile(r"(\d{1,2})(?::(\d{2}))?\s*['’]?(?:de|da|te|ta)\b"),
)


def _tr_lower(s: str) -> str:
    """Türkçe-bilinçli küçültme: 'I'→'ı', 'İ'→'i' (Python lower 'I'→'i' yanlış olurdu)."""
    return s.replace("İ", "i").replace("I", "ı").lower()


def _num(token: str) -> float:
    """Sayı sözcüğü ya da rakamı float'a çevir ('yarım'→0.5, '2'→2, '2,5'→2.5)."""
    if token in _NUM_WORDS:
        return _NUM_WORDS[token]
    return float(token.replace(",", "."))


def _offset(text: str, now: datetime) -> datetime | None:
    """'X saat/dakika sonra' → now + delta. Tanınmazsa None (belirsizlik yok → tam override)."""
    m = _OFFSET_RE.search(text)
    if not m:
        return None
    amount = _num(m.group("num"))
    unit = m.group("unit")
    delta = timedelta(hours=amount) if unit == "saat" else timedelta(minutes=amount)
    return now + delta


def _date_marker(text: str, now: datetime) -> date | None:
    """'bugün' | 'yarın' | 'öbür gün' | 'X gün sonra' → mutlak tarih. Yoksa None."""
    today = now.date()
    if "öbür gün" in text or "öbürgün" in text:
        return today + timedelta(days=2)
    m = _XDAYS_RE.search(text)
    if m:
        return today + timedelta(days=int(m.group(1)))
    if "yarın" in text:
        return today + timedelta(days=1)
    if "bugün" in text:
        return today
    return None


def _clock(text: str) -> tuple[int, int] | None:
    """Çıplak saat-dakika çek (saat N | N:MM | N'de). Geçersiz aralık → atla."""
    for rx in _CLOCK_RES:
        m = rx.search(text)
        if not m:
            continue
        hour = int(m.group(1))
        minute = int(m.group(2)) if m.group(2) else 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    return None


def _apply_daypart(text: str, hour: int) -> int:
    """Daypart sözcüğüne göre çıplak saati 24-saate taşı (akşam/gece 8 → 20).

    'öğlen'/'öğleden sonra' da 1–11 arası saatleri öğleden-sonraya taşır ('öğlen 1' → 13:00);
    'öğlen 12' öğle vaktidir, 12'de kalır. ('öğle', 'öğlen' için ortak ön-ek olarak yeterli.)"""
    if hour < 12 and ("akşam" in text or "gece" in text or "öğle" in text):
        return hour + 12
    if hour == 12 and ("gece" in text):
        return 0
    return hour


def resolve_when(
    text: str, *, now: datetime, fallback_iso: str | None = None
) -> str | None:
    """Türkçe göreli zamanı mutlak ISO 8601 (dakika çözünürlük) string'ine çöz.

    Dönüş: deterministik çözülebiliyorsa ISO string; gün biliniyor ama saat belirsizse
    fallback (LLM) saatini koruyup tarihi düzeltir; hiç sinyal yoksa None (LLM korunur).
    """
    t = _tr_lower(text)

    off = _offset(t, now)
    if off is not None:
        return off.isoformat(timespec="minutes")

    d = _date_marker(t, now)
    if d is None:
        return None

    clock = _clock(t)
    has_daypart = any(p in t for p in _DAYPARTS)
    if clock is not None and (has_daypart or not fallback_iso):
        hour, minute = clock
        hour = _apply_daypart(t, hour)
        return datetime.combine(d, time(hour, minute)).isoformat(timespec="minutes")

    # Gün biliniyor, saat belirsiz → LLM saatini koru, yalnız TARİHİ düzelt.
    if fallback_iso:
        try:
            base = datetime.fromisoformat(fallback_iso)
        except ValueError:
            return None
        return datetime.combine(d, base.time()).isoformat(timespec="minutes")

    return None
