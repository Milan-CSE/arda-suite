"""trigger.wait_for_trigger testleri — wake KAPALIYKEN mic/model kurulmadığını kanıtlar.

Asıl gizlilik garantisi: wake kapalıyken `listener_provider` HİÇ çağrılmaz →
openWakeWord yüklenmez, mikrofon açılmaz; yalnızca manuel tetik (⌘⇧A) beklenir.
"""
from arda.state import Controls
from arda.trigger import wait_for_trigger


class _Listener:
    def __init__(self, ret=True):
        self.calls = 0
        self._ret = ret

    def wait_for_wake(self, **_kw):
        self.calls += 1
        return self._ret


def test_wake_off_waits_for_manual_without_building_listener():
    c = Controls(wake_enabled=False)
    c.request_manual()  # UI "Şimdi Kaydet" / ⌘⇧A

    def _provider():
        raise AssertionError("wake kapalıyken listener kurulmamalı (mic/model yok)")

    assert wait_for_trigger(c, _provider, poll=0.01) is True


def test_wake_off_stop_returns_false_without_listener():
    c = Controls(wake_enabled=False)
    calls = {"n": 0}

    def _provider():
        calls["n"] += 1
        return _Listener()

    assert wait_for_trigger(c, _provider, stop=lambda: True, poll=0.01) is False
    assert calls["n"] == 0  # kapatma sinyali → mic/model hiç kurulmadı


def test_wake_on_delegates_to_listener():
    c = Controls(wake_enabled=True)
    listener = _Listener(ret=True)
    assert wait_for_trigger(c, lambda: listener) is True
    assert listener.calls == 1


def test_wake_provider_failure_falls_back_to_push_to_talk():
    """Eksik paket/model → listener KURULAMAZSA sonsuz hata-backoff yerine push-to-talk'a düş."""
    c = Controls(wake_enabled=True)
    c.request_manual()  # kullanıcı ⌘⇧A'ya basmış gibi

    def _provider():
        raise RuntimeError("vosk paketi kurulu değil")  # kalıcı wake hatası

    assert wait_for_trigger(c, _provider, poll=0.01) is True
    assert c.wake_enabled() is False  # wake kapatıldı → bir daha denenmez


def test_wake_listener_failure_falls_back_and_disables_wake():
    """Listener kurulur ama wait_for_wake çökerse de wake kapatılıp manuel'e düşülür."""
    c = Controls(wake_enabled=True)
    c.request_manual()

    class _Boom:
        def wait_for_wake(self, **_kw):
            raise ImportError("openwakeword yok")

    assert wait_for_trigger(c, lambda: _Boom(), poll=0.01) is True
    assert c.wake_enabled() is False
