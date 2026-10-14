"""Sesli oturum kontrol ifadeleri — sohbeti bitiren 'kapat kendini' türü komutlar.

Pipeline, transkripsiyon metnini buraya verir; eşleşirse oturum biter (standby).
Metin düzeyinde deterministik: normalize + substring eşleşme. İfadeler tek/çok kelimelik
olduğu için substring kullanılır ('kapat' tek başına eşleşmez → yanlış-tetik düşük)."""
from __future__ import annotations

import re

# Varsayılan bitirme ifadeleri tek kaynaktan gelir (arda.constants); buradan yeniden
# ihraç edilir ki `phrases.STOP_PHRASES` çağrıları aynen çalışsın (geriye uyumlu).
# Çalışma zamanında config.app.stop_phrases ile genişletilebilir/değiştirilebilir.
from arda.constants import STOP_PHRASES

# Türkçe-duyarlı küçük harf: 'İ'→'i', 'I'→'ı' (str.lower() bunları bozar).
_TR_LOWER = str.maketrans({"İ": "i", "I": "ı"})


def _normalize(text: str) -> str:
    """Küçük harf (Türkçe-duyarlı) + noktalama→boşluk + boşlukları sadeleştir."""
    text = text.translate(_TR_LOWER).lower()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)  # noktalama/apostrof → boşluk
    return re.sub(r"\s+", " ", text).strip()


def is_stop_phrase(text: str, phrases: tuple[str, ...] = STOP_PHRASES) -> bool:
    """`text` bir bitirme ifadesi içeriyor mu? (normalize edilip substring aranır)."""
    norm = _normalize(text)
    if not norm:
        return False
    return any(_normalize(p) in norm for p in phrases)
