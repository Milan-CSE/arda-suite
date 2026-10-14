"""confirm.py testleri — bekleyen-onay tutucusu + TR evet/hayır yorumu (saf).

Güvenlik asimetrisi: yanlış 'no' zararsız (eylem yapılmaz), yanlış 'yes' eylem ÇALIŞTIRIR
→ yes-eşleşmesi muhafazakâr (net kelime + kısa cümle), no önce kontrol edilir."""
from arda.confirm import PendingConfirmation, PendingStore, interpret_reply


class TestInterpretReply:
    def test_plain_yes(self):
        assert interpret_reply("evet") == "yes"

    def test_yes_with_verb(self):
        assert interpret_reply("tamam yap") == "yes"

    def test_uppercase_turkish_yes(self):
        # 'EVET' Türkçe-bilinçli küçültülmeli (I→ı), yoksa eşleşmez.
        assert interpret_reply("EVET") == "yes"

    def test_yes_with_trailing_punctuation(self):
        assert interpret_reply("Evet.") == "yes"

    def test_plain_no(self):
        assert interpret_reply("hayır") == "no"

    def test_no_phrase(self):
        assert interpret_reply("yok iptal") == "no"

    def test_no_wins_over_yes_when_both_present(self):
        # 'yapma' bir no-kelimesidir ('yap' içerse de) — no önce kontrol edilir.
        assert interpret_reply("hayır yapma") == "no"

    def test_correction_with_no_word_is_no(self):
        # 'yok Veli'ye' no-kelimesi içerir → güvenli iptal (kullanıcı yeniden söyler).
        assert interpret_reply("yok Veli'ye") == "no"

    def test_correction_without_yes_no_is_unclear(self):
        # evet/hayır kelimesi yok → yeni komut olarak ele alınmalı.
        assert interpret_reply("aslında Veli'ye olsun") == "unclear"

    def test_unknown_is_unclear(self):
        assert interpret_reply("bilmem ki") == "unclear"

    def test_empty_is_unclear(self):
        assert interpret_reply("") == "unclear"

    def test_long_sentence_with_yes_word_is_unclear(self):
        # Uzun cümlede yes-kelimesi geçse de 'yes' sayılmaz (muhafazakâr) → yanlış eylem riski yok.
        assert interpret_reply("evet ama önce şunu bir düşünmem lazım galiba") == "unclear"

    def test_single_letter_e_is_not_yes(self):
        # Tek harf 'e' yanlış 'yes' (eylem çalıştırır) riski → kabul edilmez.
        assert interpret_reply("e") == "unclear"


class TestPendingStore:
    def _pc(self, created_at=100.0):
        return PendingConfirmation(
            action="send_message", params={"to": "Ali"},
            request_text="ali'ye mesaj at", clean_text="Ali'ye mesaj at", created_at=created_at,
        )

    def test_empty_take_returns_none(self):
        assert PendingStore().take_if_fresh(now=100.0, timeout=60.0) is None

    def test_fresh_take_returns_and_clears(self):
        s = PendingStore()
        s.set(self._pc(created_at=100.0))
        got = s.take_if_fresh(now=130.0, timeout=60.0)   # 30 sn < 60 → taze
        assert got is not None and got.action == "send_message"
        assert s.peek() is None                          # alındı → temizlendi

    def test_stale_take_returns_none_and_clears(self):
        s = PendingStore()
        s.set(self._pc(created_at=100.0))
        assert s.take_if_fresh(now=200.0, timeout=60.0) is None   # 100 sn > 60 → düştü
        assert s.peek() is None                                   # düşse de temizlenir

    def test_set_overwrites_previous(self):
        s = PendingStore()
        s.set(self._pc(created_at=1.0))
        s.set(PendingConfirmation("open_app", {}, "safari aç", "Safari aç", 2.0))
        assert s.peek().action == "open_app"

    def test_clear(self):
        s = PendingStore()
        s.set(self._pc())
        s.clear()
        assert s.peek() is None
