"""timeparse.resolve_when testleri — Türkçe göreli zaman → mutlak ISO (deterministik).

Sabit `now` ile (2026-06-20 14:30, Cumartesi); gerçek saat/LLM yok."""
from datetime import datetime

from arda.timeparse import resolve_when

NOW = datetime(2026, 6, 20, 14, 30)


def _r(text, fallback_iso=None):
    return resolve_when(text, now=NOW, fallback_iso=fallback_iso)


class TestOffsets:
    """'X dakika/saat sonra' — belirsizlik YOK, tam override (LLM aritmetiği güvenilmez)."""

    def test_minutes(self):
        assert _r("10 dakika sonra hatırlat") == "2026-06-20T14:40"

    def test_dk_abbreviation(self):
        assert _r("5 dk sonra") == "2026-06-20T14:35"

    def test_hours(self):
        assert _r("2 saat sonra toplantı") == "2026-06-20T16:30"

    def test_half_hour(self):
        assert _r("yarım saat sonra") == "2026-06-20T15:00"

    def test_word_number_hours(self):
        assert _r("bir saat sonra") == "2026-06-20T15:30"

    def test_offset_rolls_over_to_next_day(self):
        assert _r("12 saat sonra") == "2026-06-21T02:30"


class TestDateMarkerWithClock:
    """Gün işareti + saat → tam tarih. Daypart varsa am/pm netleşir."""

    def test_tomorrow_morning(self):
        assert _r("yarın sabah 9'da toplantı") == "2026-06-21T09:00"

    def test_tomorrow_evening_shifts_to_pm(self):
        assert _r("yarın akşam 8'de") == "2026-06-21T20:00"

    def test_today_with_explicit_clock(self):
        assert _r("bugün 17:00") == "2026-06-20T17:00"

    def test_day_after_tomorrow(self):
        assert _r("öbür gün 10'da") == "2026-06-22T10:00"

    def test_x_days_later(self):
        assert _r("3 gün sonra 9'da") == "2026-06-23T09:00"

    def test_night_shifts_to_pm(self):
        assert _r("yarın gece 11'de") == "2026-06-21T23:00"

    def test_noon_oglen_shifts_to_pm(self):
        # 'öğlen 1' = 13:00, 01:00 DEĞİL (regresyon: _apply_daypart öğlen'i atlıyordu).
        assert _r("bugün öğlen 1'de") == "2026-06-20T13:00"

    def test_afternoon_oglenden_sonra_shifts_to_pm(self):
        # 'öğleden sonra 3' = 15:00.
        assert _r("yarın öğleden sonra 3'te hatırlat") == "2026-06-21T15:00"

    def test_noon_twelve_stays_noon(self):
        # 'öğlen 12' = 12:00 (öğle vakti); 24'e taşınmaz.
        assert _r("yarın öğlen 12'de") == "2026-06-21T12:00"


class TestDateMarkerKeepsLlmTime:
    """Gün işareti var ama saat belirsiz (daypart yok): LLM saatini KORU, TARİHİ düzelt
    (am/pm'i LLM bağlamla daha iyi bilir; biz yalnız yanlış GÜNÜ deterministik düzeltiriz)."""

    def test_corrects_wrong_date_keeps_time(self):
        # 'yarın 8' ama LLM yanlış güne (25) koymuş → gün düzeltilir, saat (08:00) korunur.
        assert _r("yarın 8'de", fallback_iso="2026-06-25T08:00") == "2026-06-21T08:00"

    def test_bare_clock_without_fallback_uses_literal_hour(self):
        # fallback yoksa çıplak saat literal alınır (belgeli davranış: 8 → 08:00).
        assert _r("yarın 8'de") == "2026-06-21T08:00"


class TestNoSignal:
    """Tanınan zaman ifadesi yoksa None → çağıran LLM değerini korur."""

    def test_no_time_expression_returns_none(self):
        assert _r("süt almayı unutma") is None

    def test_pure_chat_returns_none(self):
        assert _r("bugün çok yorgunum") is None


class TestTurkishCaseInsensitive:
    def test_uppercase_yarin_normalized(self):
        # 'YARIN' Türkçe-bilinçli küçültülmeli ('I'→'ı'), yoksa eşleşmez.
        assert _r("YARIN sabah 9'da") == "2026-06-21T09:00"
