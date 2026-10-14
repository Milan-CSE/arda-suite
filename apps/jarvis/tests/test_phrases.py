"""phrases testleri — sesli oturumu bitiren 'kapat kendini' türü ifadelerin eşleşmesi.

Metin düzeyinde deterministik (STT/LLM yok): normalize (Türkçe-duyarlı küçük harf,
noktalama/boşluk sadeleştirme) + substring eşleşme.
"""
from arda.phrases import STOP_PHRASES, is_stop_phrase


class TestIsStopPhrase:
    def test_exact_match(self):
        assert is_stop_phrase("kapat kendini")

    def test_case_insensitive_turkish(self):
        assert is_stop_phrase("Kapat Kendini")
        assert is_stop_phrase("KAPAT KENDİNİ")     # Türkçe İ → i

    def test_trailing_punctuation_and_space(self):
        assert is_stop_phrase("  kapat kendini! ")

    def test_synonyms(self):
        assert is_stop_phrase("görüşürüz")
        assert is_stop_phrase("kendini kapat")
        assert is_stop_phrase("konuşmayı bitir")

    def test_phrase_within_sentence(self):
        assert is_stop_phrase("tamam kapat kendini artık")

    def test_unrelated_is_false(self):
        assert not is_stop_phrase("bugün çok yorgunum")
        assert not is_stop_phrase("")

    def test_bare_kapat_does_not_trigger(self):
        # 'kapat' tek başına bir eylem olabilir (ör. uygulama kapat) → oturumu bitirmemeli.
        assert not is_stop_phrase("chrome'u kapat")

    def test_custom_phrase_list(self):
        assert is_stop_phrase("yeter artık", phrases=("yeter",))
        assert not is_stop_phrase("görüşürüz", phrases=("yeter",))

    def test_default_list_has_kapat_kendini(self):
        assert "kapat kendini" in STOP_PHRASES
