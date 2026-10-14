"""UI ↔ worker thread köprüsü (`Controls`).

`AgentState`/`AgentEvent` veri modelleri `arda.models`'te tanımlıdır; buradan
yalnızca yeniden ihraç edilir (geriye uyumluluk: `from arda.state import AgentState`).
`Controls` ise thread-safe köprüdür:
- UI → worker: manuel kayıt tetiği (debounce'lu) + wake kelimesi aç/kapa
- worker → UI (dolaylı): meşgul bayrağı, böylece kayıt sürerken yeni tetik reddedilir

UI güncellemesi BURADA yapılmaz — worker yalnızca olay yayınlar (on_event → AgentEvent),
UI ana thread'de kuyruğu boşaltıp kendini günceller (bkz. menubar.py).
"""
from __future__ import annotations

import threading

from arda.models import AgentState

__all__ = ["AgentState", "Controls"]

# Bu durumlardayken ajan meşguldür → yeni manuel tetik reddedilir (debounce).
_BUSY_STATES = frozenset(
    {AgentState.WOKE, AgentState.RECORDING, AgentState.TRANSCRIBING, AgentState.THINKING}
)


class Controls:
    """UI ↔ worker thread arası thread-safe köprü (Event tabanlı, kilitsiz)."""

    def __init__(self, *, wake_enabled: bool = True) -> None:
        self._manual = threading.Event()
        self._wake = threading.Event()
        self._busy = threading.Event()
        self._cancel = threading.Event()
        self._shutdown = threading.Event()
        self._dictation = threading.Event()
        self._dictating = threading.Event()   # bir dikte turu aktif mi (kayıt sürüyor)
        self._dict_stop = threading.Event()   # aktif dikteyi durdur (amblem 'Durdur' tıklaması)
        self._open_panel = threading.Event()
        if wake_enabled:
            self._wake.set()

    # --- UI tarafı (ana thread veya kısayol thread'i) ---
    def request_manual(self) -> bool:
        """Manuel kayıt iste. Meşgulken reddedilir (debounce). Kabulde True."""
        if self._busy.is_set():
            return False
        self._manual.set()
        return True

    def wait_for_manual(self, timeout: float | None = None) -> bool:
        """Manuel tetik gelene kadar blokla — **mikrofon AÇMADAN**. Gelirse tüket + True.

        Wake kelimesi kapalıyken (push-to-talk modu) kullanılır: WakeListener hiç
        kurulmaz, mikrofon yalnızca asıl kayıt sırasında açılır → 'her zaman dinleme' yok.
        """
        if self._manual.wait(timeout):
            self._manual.clear()
            return True
        return False

    # --- dikte (amblem / ⌘⇧D) tetiği — manuel ajan tetiğinden ayrı ---
    def request_dictation(self) -> bool:
        """Dikte tetiği (amblem tıklama / ⌘⇧D) — TOGGLE. Her zaman True.

        Bir dikte turu AKTİFKEN (kayıt sürüyor) → mevcut kaydı DURDURMA sinyali verir
        (amblem 'Durdur' butonu gibi: o ana kadarki ses yazılır). Aksi halde yeni tur başlatır."""
        if self._dictating.is_set():
            self._dict_stop.set()
        else:
            self._dictation.set()
        return True

    def begin_dictation(self) -> None:
        """Worker tur başında çağırır: eski durdur bayrağını temizle, turu aktif işaretle."""
        self._dict_stop.clear()
        self._dictating.set()

    def end_dictation(self) -> None:
        """Worker tur sonunda çağırır (finally): aktif + durdur bayraklarını temizle."""
        self._dictating.clear()
        self._dict_stop.clear()

    def dictation_should_stop(self) -> bool:
        """Kayıt döngüsü (record_until_silence.should_stop) çağırır → True ise erken kes."""
        return self._dict_stop.is_set()

    def is_dictating(self) -> bool:
        return self._dictating.is_set()

    def wait_for_dictation(self, timeout: float | None = None) -> bool:
        """Dikte tetiği gelene kadar blokla (worker döngüsü). Gelirse tüket + True."""
        if self._dictation.wait(timeout):
            self._dictation.clear()
            return True
        return False

    def consume_dictation(self) -> bool:
        """Bekleyen dikte tetiği varsa tüket (clear) ve True dön."""
        if self._dictation.is_set():
            self._dictation.clear()
            return True
        return False

    def request_open_panel(self) -> None:
        """Kısayol thread'inden panel açma iste (AppKit'e DOKUNMA). Ana thread tüketir."""
        self._open_panel.set()

    def consume_open_panel(self) -> bool:
        if self._open_panel.is_set():
            self._open_panel.clear()
            return True
        return False

    def wake_enabled(self) -> bool:
        return self._wake.is_set()

    def set_wake(self, on: bool) -> None:
        self._wake.set() if on else self._wake.clear()

    def is_busy(self) -> bool:
        return self._busy.is_set()

    # --- iptal: çok-adımlı agent koşusunu durdur (executor.should_stop'a bağlanır) ---
    def request_cancel(self) -> None:
        """Süren agent koşusunu durdurmayı iste (UI 'Durdur' / kısayol). Bir sonraki
        adım sınırında executor nazikçe durur (status='cancelled')."""
        self._cancel.set()

    def is_cancelled(self) -> bool:
        """executor her adımdan önce çağırır (should_stop). True → koşu durur."""
        return self._cancel.is_set()

    def request_shutdown(self) -> None:
        """Signal the daemon loop to stop after the current safe boundary."""
        self._shutdown.set()

    def is_shutdown(self) -> bool:
        return self._shutdown.is_set()

    def clear_cancel(self) -> None:
        """İptal bayrağını sıfırla — her yeni koşudan önce (eski iptal yeni koşuyu öldürmesin)."""
        self._cancel.clear()

    # --- worker tarafı ---
    def consume_manual(self) -> bool:
        """Bekleyen manuel tetik varsa tüket (clear) ve True dön."""
        if self._manual.is_set():
            self._manual.clear()
            return True
        return False

    def set_busy_for(self, state: AgentState) -> None:
        """on_event'ten çağrılır: duruma göre meşgul bayrağını ayarla (debounce için)."""
        if state in _BUSY_STATES:
            self._busy.set()
        else:
            self._busy.clear()
