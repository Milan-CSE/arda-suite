"""Düşük-güvenli eylemde teyit sorma — bekleyen-onay tutucusu + TR evet/hayır yorumu.

Saf mantık (IO/saat yok): `interpret_reply` saf fonksiyon, `PendingStore` tek-yuvalı
thread-safe tutucu. Pipeline bunları enjekte eder → test mock'lamadan çalışır.

Güvenlik asimetrisi (kasıtlı): yanlış 'no' zararsızdır (eylem yapılmaz), yanlış 'yes'
eylemi ÇALIŞTIRIR. Bu yüzden no önce kontrol edilir ve yes yalnız KISA + NET ifadede kabul edilir.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

# Net onay kelimeleri. Tek harf ('e'/'he') YOK: yanlış 'yes' eylem çalıştırır (tehlikeli yön).
# 'olsun' YOK: düzeltmede ("Veli'ye olsun") geçer → yanlış onay riski; muhafazakâr kal.
_YES = frozenset({"evet", "tamam", "olur", "yap", "onayla", "doğru", "dogru", "aynen", "tabii"})
# Ret kelimeleri. 'yapma' burada ('yap' içerse de no önce kontrol edilir). 'yok' "gerek yok"u da kapsar.
_NO = frozenset({"hayır", "hayir", "yok", "iptal", "vazgeç", "vazgec", "boşver", "bosver", "yapma", "dur"})

# yes yalnız bu kadar veya daha kısa cümlede kabul (uzun cümlede yes-kelimesi geçse de 'unclear').
_MAX_YES_WORDS = 4
_STRIP = ".,!?;:'\"’`"


def _tr_lower(s: str) -> str:
    """Türkçe-bilinçli küçültme: 'I'→'ı', 'İ'→'i' (Python lower 'I'→'i' yanlış olurdu)."""
    return s.replace("İ", "i").replace("I", "ı").lower()


def _words(text: str) -> list[str]:
    """Metni küçült + kelimelere böl, baş/son noktalamayı temizle, boşları at."""
    return [w for w in (t.strip(_STRIP) for t in _tr_lower(text or "").split()) if w]


def interpret_reply(text: str) -> str:
    """Bir yanıtı 'yes' | 'no' | 'unclear' olarak yorumla (TR, muhafazakâr).

    no önce: bir ret kelimesi varsa → 'no'. Sonra yes: KISA cümlede net onay kelimesi → 'yes'.
    Aksi → 'unclear' (yeni komut sayılır). Boş/anlamsız → 'unclear'."""
    words = _words(text)
    if any(w in _NO for w in words):
        return "no"
    if len(words) <= _MAX_YES_WORDS and any(w in _YES for w in words):
        return "yes"
    return "unclear"


@dataclass(frozen=True)
class PendingConfirmation:
    """Teyit bekleyen tek bir eylem. `created_at` = time.monotonic() (tazelik için)."""
    action: str
    params: dict
    request_text: str   # orijinal ham metin (gözlem/loglama)
    clean_text: str     # geri söylenen yorum ("şunu mu demek istedin")
    created_at: float


class PendingStore:
    """Tek-yuvalı, thread-safe bekleyen-onay tutucusu. En fazla bir onay tutar (yenisi ezer)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pc: PendingConfirmation | None = None

    def set(self, pc: PendingConfirmation) -> None:
        """Bekleyen onayı kur (öncekini ezer)."""
        with self._lock:
            self._pc = pc

    def take_if_fresh(self, now: float, timeout: float) -> PendingConfirmation | None:
        """Bekleyen onayı al (HER durumda temizle). Taze ise döndür; süresi geçtiyse None.

        Tek-atımlık: bir kez okunur ve yuvayı boşaltır → bayat onay sonradan tetiklenemez."""
        with self._lock:
            pc, self._pc = self._pc, None
        if pc is None or now - pc.created_at > timeout:
            return None
        return pc

    def clear(self) -> None:
        """Bekleyen onayı düşür (stop-phrase / iptal)."""
        with self._lock:
            self._pc = None

    def peek(self) -> PendingConfirmation | None:
        """Tutulan onayı tüketmeden gör (test/gözlem)."""
        with self._lock:
            return self._pc
