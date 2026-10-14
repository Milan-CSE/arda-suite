"""conversation.Conversation testleri — kısa süreli sohbet belleği.

Saat (`clock`) enjekte edilir → idle timeout davranışı deterministik test edilir
(gerçek zaman beklemeden). Gereksinimler: turları sakla, son N ile sınırla, idle
sonrası temizle, süresi geçmiş bağlamı KULLANMA.
"""
from arda.conversation import Conversation, Turn


class _Clock:
    """Elle ilerletilen sahte monotonik saat."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class TestStoresTurns:
    def test_remember_then_recent_returns_user_assistant_turn(self):
        c = Conversation()
        c.remember("yarın 8'de dersim var", "Tamam, not aldım.")
        turns = c.recent()
        assert turns == (Turn(user="yarın 8'de dersim var", assistant="Tamam, not aldım."),)

    def test_empty_recent_is_empty_tuple(self):
        assert Conversation().recent() == ()

    def test_order_is_oldest_to_newest(self):
        c = Conversation()
        c.remember("bir", "1")
        c.remember("iki", "2")
        assert [t.user for t in c.recent()] == ["bir", "iki"]


class TestMaxTurns:
    def test_default_keeps_last_8(self):
        c = Conversation()  # default max_turns=8
        for i in range(12):
            c.remember(f"u{i}", f"a{i}")
        turns = c.recent()
        assert len(turns) == 8
        assert turns[0].user == "u4"     # en eski 4 düştü
        assert turns[-1].user == "u11"

    def test_custom_max_turns(self):
        c = Conversation(max_turns=2)
        c.remember("a", "1")
        c.remember("b", "2")
        c.remember("c", "3")
        assert [t.user for t in c.recent()] == ["b", "c"]


class TestIdleExpiry:
    def test_default_idle_is_5_minutes(self):
        # Varsayılan idle timeout = 300 sn (5 dk) — gereksinim.
        clock = _Clock()
        c = Conversation(clock=clock)
        c.remember("merhaba", "selam")
        clock.advance(299)
        assert len(c.recent()) == 1            # 5 dk dolmadan bağlam durur
        clock.advance(2)                       # toplam 301 sn → idle aşıldı
        assert c.recent() == ()                # süresi geçmiş bağlam KULLANILMAZ

    def test_activity_resets_idle_timer(self):
        clock = _Clock()
        c = Conversation(ttl_sec=100.0, clock=clock)
        c.remember("a", "1")
        clock.advance(80)
        c.remember("b", "2")                   # aktivite → sayaç sıfırlanır
        clock.advance(80)                      # ilk turdan 160 sn ama son turdan 80 sn
        assert len(c.recent()) == 2            # hâlâ canlı

    def test_remember_after_expiry_starts_fresh(self):
        clock = _Clock()
        c = Conversation(ttl_sec=100.0, clock=clock)
        c.remember("eski", "x")
        clock.advance(200)                     # süresi geçti
        c.remember("yeni", "y")                # yeni konuşma temiz başlamalı
        assert [t.user for t in c.recent()] == ["yeni"]


class TestClear:
    def test_clear_empties_context(self):
        c = Conversation()
        c.remember("a", "1")
        c.clear()
        assert c.recent() == ()


class TestActionResultMemory:
    """ADIM 2: turlar opsiyonel yapılandırılmış action sonucu taşıyabilir (geriye uyumlu)."""

    def test_turn_defaults_action_result_to_none(self):
        assert Turn("u", "a").action_result is None

    def test_remember_stores_action_result(self):
        c = Conversation()
        ar = {"action": "set_reminder", "success": True, "text": "toplantı"}
        c.remember("8'de hatırlat", "kurdum", ar)
        assert c.recent()[0].action_result == ar

    def test_remember_without_action_result_stays_none(self):
        c = Conversation()
        c.remember("merhaba", "selam")          # 2 argümanlı eski çağrı şekli
        assert c.recent()[0].action_result is None


class TestOnExpire:
    """ADIM 7: on_expire callback — TTL ile turlar temizlenmeden ÖNCE bir kez tetiklenir
    (idle flush tetiği, ör. mem0 kuyruğunu boşalt)."""

    def test_on_expire_called_before_clearing_stale_turns(self):
        t = {"now": 0.0}
        fired = []
        conv = Conversation(ttl_sec=5.0, clock=lambda: t["now"],
                            on_expire=lambda: fired.append(True))
        conv.remember("u", "a")
        t["now"] = 100.0                    # TTL aşıldı
        assert conv.recent() == ()          # bayat → temizlendi
        assert fired == [True]              # temizlemeden önce flush tetiklendi

    def test_on_expire_not_called_when_fresh(self):
        conv = Conversation(ttl_sec=5.0, clock=lambda: 0.0,
                            on_expire=lambda: (_ for _ in ()).throw(AssertionError()))
        conv.remember("u", "a")
        assert len(conv.recent()) == 1      # taze → callback yok
