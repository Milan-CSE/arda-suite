from arda.models import Intent
from arda.notes import (
    append_task,
    build_frontmatter,
    render_note,
    save_to_inbox,
    slugify_tr,
    write_note,
)

INTENT = Intent(
    intent="note",
    category="internship",
    title="ORS Staj Notu",
    clean_text="Üretim hattında rulmanların taşlama aşaması incelendi.",
    tags=["staj", "ORS", "rulman"],
    follow_up=["Yüzey pürüzlülüğü ölçümü (Ra/Rz)", "Rulman ISO standartları"],
)


class TestSlugifyTr:
    def test_turkish_chars_mapped(self):
        assert slugify_tr("Çağrı Şükrü Öğüt İçi") == "cagri-sukru-ogut-ici"

    def test_spaces_and_emdash_collapsed(self):
        assert slugify_tr("ORS Staj Notu — 16 Haziran") == "ors-staj-notu-16-haziran"

    def test_strips_punctuation(self):
        assert slugify_tr("Rulman: taşlama, kalite!") == "rulman-taslama-kalite"

    def test_strips_leading_trailing_dashes(self):
        assert slugify_tr("  --Merhaba--  ") == "merhaba"


def test_build_frontmatter_has_type_date_tags():
    fm = build_frontmatter(INTENT, date="2026-06-16")
    assert fm.startswith("---")
    assert "type: internship" in fm
    assert "date: 2026-06-16" in fm
    assert 'tags: ["staj", "ORS", "rulman"]' in fm
    assert fm.strip().endswith("---")


def test_build_frontmatter_is_valid_yaml_with_special_chars():
    """Özel karakter içeren etiket frontmatter'ı bozmamalı (Obsidian YAML parse)."""
    import yaml as _yaml

    intent = Intent(
        intent="note", category="daily", title="t", clean_text="c",
        tags=["saat: 14:00", "[acil]", 'tırnak"lı'], follow_up=[],
    )
    fm = build_frontmatter(intent, date="2026-06-16")
    parsed = _yaml.safe_load(fm.strip("-\n"))
    assert parsed["tags"] == ["saat: 14:00", "[acil]", 'tırnak"lı']


def test_render_note_contains_sections_and_content():
    md = render_note(INTENT, raw_text="bugün ORS'de rulman gördük", date="2026-06-16")
    assert "# ORS Staj Notu" in md
    assert "## Ham Not" in md
    assert "bugün ORS'de rulman gördük" in md
    assert "## Düzenlenmiş Not" in md
    assert "Üretim hattında rulmanların" in md
    assert "## Anahtar Kelimeler" in md
    assert "#staj #ORS #rulman" in md
    assert "## Sonradan Araştırılacaklar" in md
    assert "- Yüzey pürüzlülüğü ölçümü (Ra/Rz)" in md


CFG = {
    "vault": {
        "path": None,  # tmp_path ile her testte doldurulur
        "categories": {
            "internship": "Internship",
            "research": "Research",
            "idea": "Ideas",
            "daily": "Daily",
            "task": "Tasks.md",
            "inbox": "Inbox",
        },
    }
}


def _cfg(tmp_path):
    import copy
    c = copy.deepcopy(CFG)
    c["vault"]["path"] = str(tmp_path)
    return c


class TestWriteNote:
    def test_creates_file_in_category_folder(self, tmp_path):
        cfg = _cfg(tmp_path)
        path = write_note(INTENT, "ham metin", cfg=cfg, date="2026-06-18")
        assert path.exists()
        assert path.parent.name == "Internship"
        assert path.suffix == ".md"

    def test_filename_is_slug_of_title(self, tmp_path):
        path = write_note(INTENT, "ham metin", cfg=_cfg(tmp_path), date="2026-06-18")
        assert path.stem == "ors-staj-notu"

    def test_content_includes_frontmatter_and_title(self, tmp_path):
        path = write_note(INTENT, "ham metin", cfg=_cfg(tmp_path), date="2026-06-18")
        content = path.read_text(encoding="utf-8")
        assert "type: internship" in content
        assert "# ORS Staj Notu" in content

    def test_unknown_category_falls_to_inbox(self, tmp_path):
        import copy
        intent = Intent(
            intent="note", category="unknown_cat", title="Bilinmeyen",
            clean_text="x", tags=[], follow_up=[],
        )
        path = write_note(intent, "ham", cfg=_cfg(tmp_path), date="2026-06-18")
        assert path.parent.name == "Inbox"


class TestAppendTask:
    def test_appends_checkbox_line(self, tmp_path):
        cfg = _cfg(tmp_path)
        path = append_task(INTENT, cfg=cfg)
        assert path.name == "Tasks.md"
        lines = path.read_text(encoding="utf-8").splitlines()
        assert any("ORS Staj Notu" in line for line in lines)
        assert any("[ ]" in line for line in lines)

    def test_multiple_calls_append_not_overwrite(self, tmp_path):
        cfg = _cfg(tmp_path)
        append_task(INTENT, cfg=cfg)
        append_task(INTENT, cfg=cfg)
        lines = (tmp_path / "Tasks.md").read_text().splitlines()
        assert sum("ORS Staj Notu" in l for l in lines) == 2


class TestWriteNoteCollision:
    def test_same_title_does_not_overwrite(self, tmp_path):
        """İki not aynı başlıkta → ikincisi birinciyi silmemeli (veri kaybı)."""
        cfg = _cfg(tmp_path)
        first = Intent(
            intent="note", category="internship", title="Günlük",
            clean_text="ilk içerik", tags=[], follow_up=[],
        )
        second = Intent(
            intent="note", category="internship", title="Günlük",
            clean_text="ikinci içerik", tags=[], follow_up=[],
        )
        p1 = write_note(first, "ilk ham", cfg=cfg, date="2026-06-18")
        p2 = write_note(second, "ikinci ham", cfg=cfg, date="2026-06-18")
        assert p1 != p2
        assert p1.exists() and p2.exists()
        assert "ilk içerik" in p1.read_text(encoding="utf-8")
        assert "ikinci içerik" in p2.read_text(encoding="utf-8")


class TestSaveToInbox:
    def test_creates_inbox_file(self, tmp_path):
        path = save_to_inbox("ham metin", cfg=_cfg(tmp_path), date="2026-06-18")
        assert path.exists()
        assert path.parent.name == "Inbox"

    def test_content_includes_raw_text(self, tmp_path):
        path = save_to_inbox("ham metin burada", cfg=_cfg(tmp_path), date="2026-06-18")
        assert "ham metin burada" in path.read_text(encoding="utf-8")

    def test_multiple_same_day_calls_preserve_all(self, tmp_path):
        """Aynı gün iki fallback → ilk kayıt silinmemeli (asla-veri-kaybetme)."""
        cfg = _cfg(tmp_path)
        save_to_inbox("birinci hata metni", cfg=cfg, date="2026-06-18")
        path = save_to_inbox("ikinci hata metni", cfg=cfg, date="2026-06-18")
        content = path.read_text(encoding="utf-8")
        assert "birinci hata metni" in content
        assert "ikinci hata metni" in content


class TestWriteResearchNote:
    """Kaynaklı araştırma notu → vault/Research/. Frontmatter + kaynak listesi."""

    def _sources(self):
        from arda.research import Source

        return [
            Source("Python", "https://python.org", "Python bir dildir.", "tavily"),
            Source("Docs", "https://docs.python.org", "Resmi dokümanlar.", "tavily"),
        ]

    def test_writes_to_research_folder(self, tmp_path):
        from arda.notes import write_research_note

        path = write_research_note(
            "python nedir", "Python bir dildir [1].", self._sources(),
            cfg=_cfg(tmp_path), date="2026-06-19",
        )
        assert path.parent.name == "Research" and path.exists()

    def test_filename_is_slug_of_query(self, tmp_path):
        from arda.notes import write_research_note

        path = write_research_note("Python Nedir", "x", self._sources(),
                                   cfg=_cfg(tmp_path), date="2026-06-19")
        assert path.name == "python-nedir.md"

    def test_frontmatter_has_required_fields(self, tmp_path):
        from arda.notes import write_research_note

        text = write_research_note("python nedir", "özet [1]", self._sources(),
                                   cfg=_cfg(tmp_path), date="2026-06-19").read_text(encoding="utf-8")
        assert "type: research" in text
        assert "provider: tavily" in text          # sources[0].provider
        assert "sources_count: 2" in text
        assert "date: 2026-06-19" in text
        assert "python nedir" in text              # query frontmatter + başlık

    def test_sources_listed_with_links(self, tmp_path):
        from arda.notes import write_research_note

        text = write_research_note("q", "özet", self._sources(),
                                   cfg=_cfg(tmp_path), date="2026-06-19").read_text(encoding="utf-8")
        assert "https://python.org" in text and "https://docs.python.org" in text
        assert "## Kaynaklar" in text
