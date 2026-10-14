"""Çok-turlu sohbet belleği — Arda'nın takip sorularını/atıfları çözebilmesi için.

Sesli asistan bağlamı KISA ÖMÜRLÜDÜR: TTL geçince geçmiş kendini temizler, böylece
saatler sonra yazdırdığın not eski bir sohbetten etkilenmez. Son N tur (kullanıcı
sözü + Arda'nın sesli yanıtı) tutulur ve beyne bağlam olarak verilir.

Tek worker thread'inden yazılır ama hotkey/UI yan thread'leri de okuyabildiği için
kilitle korunur. `clock` enjekte edilebilir → TTL davranışı deterministik test edilir.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

_DEFAULT_MAX_TURNS = 8      # son 8 gidiş-geliş — sesli sohbet için yeterli, prompt'u şişirmez
_DEFAULT_TTL_SEC = 300.0    # 5 dk sessizlikten (idle) sonra bağlam unutulur


@dataclass(frozen=True)
class Turn:
    """Tek bir gidiş-geliş: kullanıcının ham sözü + Arda'nın sesli yanıtı.

    `action_result` yalnızca eylem turlarında dolar (set_reminder vb. yapılandırılmış
    sonucu); None ise eski sade tur davranışı birebir korunur. Beyne bağlam verilirken
    (brain._build_messages) modele okunabilir biçimde iliştirilir → sonraki turdaki
    atıflar ("az önce kurduğum") çözülebilir."""

    user: str
    assistant: str
    action_result: dict | None = None


class Conversation:
    def __init__(
        self,
        *,
        max_turns: int = _DEFAULT_MAX_TURNS,
        ttl_sec: float = _DEFAULT_TTL_SEC,
        clock: Callable[[], float] = time.monotonic,
        on_expire: Callable[[], None] | None = None,
    ) -> None:
        self._ttl = ttl_sec
        self._clock = clock
        self._turns: "deque[Turn]" = deque(maxlen=max(1, max_turns))
        self._last = 0.0
        self._lock = threading.Lock()
        self._on_expire = on_expire

    def _expire_if_stale(self) -> None:
        """TTL geçtiyse geçmişi temizle (lock TUTULUYORKEN çağrılır).

        Temizlemeden ÖNCE on_expire (varsa) bir kez tetiklenir — idle flush tetiği
        (ör. mem0 kuyruğunu boşalt). on_expire hatası temizliği ASLA bozmasın."""
        if self._turns and (self._clock() - self._last) > self._ttl:
            if self._on_expire is not None:
                try:
                    self._on_expire()
                except Exception:  # noqa: BLE001 — flush kozmetik; temizliği bozmasın
                    pass
            self._turns.clear()

    def recent(self) -> tuple[Turn, ...]:
        """Geçerli (TTL içindeki) turları döndür. Bayatsa önce temizler."""
        with self._lock:
            self._expire_if_stale()
            return tuple(self._turns)

    def remember(self, user: str, assistant: str, action_result: dict | None = None) -> None:
        """Bir turu kaydet. maxlen sayesinde en eski tur otomatik düşer.

        `action_result` opsiyoneldir → eski 2 argümanlı çağrılar (not/görev/answered)
        birebir çalışır; eylem turları yapılandırılmış sonucu ekler."""
        with self._lock:
            self._expire_if_stale()
            self._turns.append(Turn(user=user, assistant=assistant, action_result=action_result))
            self._last = self._clock()

    def clear(self) -> None:
        """Bağlamı elle sıfırla (ör. 'yeni konu')."""
        with self._lock:
            self._turns.clear()
