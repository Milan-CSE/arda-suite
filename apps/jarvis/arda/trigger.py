"""Tetik bekleme: wake kelimesi (her-zaman-dinle) VEYA push-to-talk (mic kapalı).

`wait_for_trigger` ajan döngüsünün "ne zaman kayda geç" kararını verir:

- **Wake KAPALI (varsayılan):** mikrofon açılmaz, openWakeWord yüklenmez. Yalnızca
  manuel tetik (⌘⇧A / 'Şimdi Kaydet') beklenir → gerçek push-to-talk, gizlilik korunur.
- **Wake AÇIK (opt-in):** `listener_provider()` ile WakeListener **tembel** kurulur
  (model + sürekli mikrofon ancak burada yüklenir) ve wake kelimesi dinlenir.

`listener_provider` tembeldir: yalnızca wake açıkken çağrılır → kapalıyken hiçbir
donanım/model maliyeti doğmaz.
"""
from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger(__name__)

# Push-to-talk modunda tetik beklerken yeniden-yoklama aralığı (sn). Kısa tutulur ki
# wake aç/kapa ve kapatma (stop) sinyaline hızlı tepki verilsin.
_POLL_SEC = 0.3


def wait_for_trigger(
    controls,
    listener_provider: Callable,
    *,
    stop: Callable[[], bool] = lambda: False,
    poll: float = _POLL_SEC,
) -> bool:
    """Tetik gelene kadar blokla. True = kayda geç, False = kapatma sinyali.

    Args:
        controls: thread-safe Controls (wake aç/kapa + manuel tetik).
        listener_provider: çağrıldığında WakeListener döndüren tembel fabrika
            (yalnızca wake açıkken çağrılır → kapalıyken mic/model kurulmaz).
        stop: True dönerse beklemeyi bırak ve False dön (kapatma).
        poll: push-to-talk modunda yeniden-yoklama aralığı (sn).
    """
    while not stop():
        if controls.wake_enabled():
            # Wake açık → listener'ı tembel kur, wake kelimesini (veya manuel) dinle.
            try:
                return listener_provider().wait_for_wake(
                    triggered=controls.consume_manual,
                    wake_on=controls.wake_enabled,
                    stop=stop,
                )
            except Exception as exc:  # noqa: BLE001 — wake backend kurulamadı/çöktü
                # Kalıcı hata (eksik paket/model) sonsuz 2 sn'lik hata-backoff'una yol açardı.
                # Bunun yerine wake'i KAPAT → push-to-talk'a düş: kullanıcı ⌘⇧A ile çalışmaya
                # devam eder, sorunu düzeltince menüden 'Uyandırma kelimesi'ni tekrar açar.
                log.warning(
                    "Wake backend başlatılamadı (%s) → push-to-talk'a düşülüyor. Eksik "
                    "paket/model olabilir; düzeltip menüden uyandırma kelimesini tekrar aç.",
                    exc,
                )
                controls.set_wake(False)
                continue  # döngü başına dön → artık manuel (push-to-talk) dalı çalışır
        # Wake kapalı → mic AÇMADAN sadece manuel tetik bekle.
        if controls.wait_for_manual(timeout=poll):
            return True
    return False
