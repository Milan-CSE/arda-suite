"""research testleri — ports/adapters. Ağ MOCK'lu (transport/client enjekte edilir);
ayrıca ağ varsa çalışan bir canlı DDG testi (yoksa skip)."""
from __future__ import annotations

import socket

import pytest

from arda.research import (
    DDGProvider,
    ResearchError,
    Source,
    TavilyProvider,
    normalize_sources,
    research,
    select_providers,
    spoken_summary,
)


class _FakeProvider:
    """Test için sahte SearchProvider (sonuç ya da hata döndürür)."""

    def __init__(self, name, *, sources=None, error=None):
        self.name = name
        self._sources = sources or []
        self._error = error

    def search(self, query, *, max_results):
        if self._error:
            raise self._error
        return self._sources


def _src(url, *, title="T", provider="tavily"):
    return Source(title=title, url=url, snippet="s", provider=provider)


class TestNormalizeSources:
    def test_builds_source_fields(self):
        out = normalize_sources(
            [{"title": "Py", "url": "https://x.com/a", "content": "dil", "published_date": "2026-01-01"}],
            "tavily",
        )
        assert out == [Source("Py", "https://x.com/a", "dil", "tavily", "2026-01-01")]

    def test_maps_alternate_keys(self):
        # DDG biçimi: href + body
        out = normalize_sources([{"title": "D", "href": "https://d.com", "body": "özet"}], "duckduckgo")
        assert out[0].url == "https://d.com" and out[0].snippet == "özet"

    def test_dedupes_by_url(self):
        raw = [{"url": "https://x.com/a/"}, {"url": "https://x.com/a"}, {"url": "https://y.com"}]
        out = normalize_sources(raw, "tavily")
        assert [s.url for s in out] == ["https://x.com/a/", "https://y.com"]   # ilk kazanır, /b dedupe

    def test_skips_malformed(self):
        out = normalize_sources([{"title": "url yok"}, "string değil", {"url": "https://ok.com"}], "tavily")
        assert [s.url for s in out] == ["https://ok.com"]

    def test_empty_title_falls_back_to_url(self):
        out = normalize_sources([{"url": "https://z.com"}], "tavily")
        assert out[0].title == "https://z.com"

    def test_published_date_optional(self):
        assert normalize_sources([{"url": "https://a.com"}], "tavily")[0].published_date is None


class TestSpokenSummary:
    def test_strips_citation_markers(self):
        assert "[" not in spoken_summary("Cevap böyle [1] ve şöyle [2].")

    def test_caps_length_at_word_boundary(self):
        text = "kelime " * 50
        out = spoken_summary(text, max_chars=40)
        assert len(out) <= 40 and out.endswith("…")

    def test_short_text_unchanged(self):
        assert spoken_summary("kısa cevap", max_chars=100) == "kısa cevap"


class TestSelectProviders:
    def _factories(self):
        return (lambda key: _FakeProvider("tavily"), lambda: _FakeProvider("duckduckgo"))

    def test_auto_with_key_is_tavily_then_ddg(self):
        tf, df = self._factories()
        provs = select_providers({"research": {"provider": "auto", "tavily_api_key": "k"}},
                                 tavily_factory=tf, ddg_factory=df)
        assert [p.name for p in provs] == ["tavily", "duckduckgo"]

    def test_auto_without_key_is_ddg_only(self):
        tf, df = self._factories()
        provs = select_providers({"research": {"provider": "auto", "tavily_api_key": ""}},
                                 tavily_factory=tf, ddg_factory=df)
        assert [p.name for p in provs] == ["duckduckgo"]

    def test_forced_tavily_without_key_raises(self):
        tf, df = self._factories()
        with pytest.raises(ResearchError):
            select_providers({"research": {"provider": "tavily", "tavily_api_key": ""}},
                             tavily_factory=tf, ddg_factory=df)

    def test_forced_duckduckgo(self):
        tf, df = self._factories()
        provs = select_providers({"research": {"provider": "duckduckgo", "tavily_api_key": "k"}},
                                 tavily_factory=tf, ddg_factory=df)
        assert [p.name for p in provs] == ["duckduckgo"]


class TestResearchOrchestration:
    CFG = {"research": {"max_results": 5}}

    def test_returns_first_nonempty_with_provider_name(self):
        provs = [_FakeProvider("tavily", sources=[_src("https://a.com")])]
        sources, name = research("q", self.CFG, providers=provs)
        assert name == "tavily" and len(sources) == 1

    def test_tavily_error_falls_back_to_ddg(self):
        provs = [
            _FakeProvider("tavily", error=ResearchError("boom")),
            _FakeProvider("duckduckgo", sources=[_src("https://d.com", provider="duckduckgo")]),
        ]
        sources, name = research("q", self.CFG, providers=provs)
        assert name == "duckduckgo" and sources[0].url == "https://d.com"

    def test_all_empty_raises(self):
        provs = [_FakeProvider("tavily"), _FakeProvider("duckduckgo")]
        with pytest.raises(ResearchError):
            research("q", self.CFG, providers=provs)

    def test_forced_single_provider_no_fallback(self):
        provs = [_FakeProvider("duckduckgo", error=ResearchError("ddg down"))]
        with pytest.raises(ResearchError):
            research("q", self.CFG, providers=provs)


class TestTavilyProvider:
    def test_no_key_raises(self):
        with pytest.raises(ResearchError):
            TavilyProvider("").search("q", max_results=5)

    def test_transport_result_normalized(self):
        transport = lambda q, key, n: {"results": [{"title": "T", "url": "https://t.com", "content": "c"}]}
        out = TavilyProvider("k", transport=transport).search("q", max_results=5)
        assert out[0].url == "https://t.com" and out[0].provider == "tavily"

    def test_transport_error_becomes_research_error(self):
        def transport(q, key, n):
            raise TimeoutError("zaman aşımı")
        with pytest.raises(ResearchError):
            TavilyProvider("k", transport=transport).search("q", max_results=5)


class TestDDGProvider:
    def test_client_results_normalized(self):
        client = lambda q, n: [{"title": "D", "href": "https://d.com", "body": "b"}]
        out = DDGProvider(client_factory=client).search("q", max_results=5)
        assert out[0].url == "https://d.com" and out[0].provider == "duckduckgo"

    def test_client_error_becomes_research_error(self):
        def client(q, n):
            raise RuntimeError("ddg patladı")
        with pytest.raises(ResearchError):
            DDGProvider(client_factory=client).search("q", max_results=5)


def _net_up() -> bool:
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=1.0):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _net_up(), reason="canlı test: internet erişimi gerekli")
class TestLiveDDG:
    def test_real_duckduckgo_search_returns_sources(self):
        out = DDGProvider().search("python programming language", max_results=3)
        assert len(out) >= 1
        assert all(s.url.startswith("http") for s in out)
