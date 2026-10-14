"""Web araştırma — ports/adapters. `SearchProvider` arayüzü + Tavily/DDG adaptörleri.

Tavily birincil (AI-optimize, API key), DuckDuckGo anahtarsız fallback. Provider seçimi
config (`research.provider`: auto|tavily|duckduckgo) + key varlığına göre. Tüm ağ erişimi
enjekte edilebilir (transport/client_factory) → testte gerçek istek yok. Hatalar
`ResearchError`'a sarılır → pipeline sesli fallback verir, asla çökmez.

Şimdilik yalnızca snippet özeti; ileride provider'a `fetch(url)` eklenip tam içerik
çıkarımı (extract) kolayca desteklenebilir (Source.snippet alanı yerinde duruyor)."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

_DEFAULT_MAX_RESULTS = 5
_SPOKEN_MAX = 600          # sesli cevap için karakter sınırı (kelime sınırında kırpılır)
_HTTP_TIMEOUT = 10.0
_TAVILY_URL = "https://api.tavily.com/search"

# Esnek anahtar eşleme: farklı provider'lar farklı alan adları kullanır.
_URL_KEYS = ("url", "href", "link")
_TITLE_KEYS = ("title", "name")
_SNIPPET_KEYS = ("snippet", "content", "body", "description")
_DATE_KEYS = ("published_date", "date", "published")


class ResearchError(RuntimeError):
    """Arama başarısız/boş ya da yapılandırma eksik. Pipeline yakalar → sesli fallback."""


@dataclass(frozen=True)
class Source:
    """Normalleştirilmiş arama sonucu. published_date opsiyonel."""

    title: str
    url: str
    snippet: str
    provider: str
    published_date: str | None = None


@runtime_checkable
class SearchProvider(Protocol):
    name: str

    def search(self, query: str, *, max_results: int) -> list[Source]:
        ...


def _first(item: dict, keys: tuple[str, ...]) -> str:
    """item içinde keys sırasıyla ilk dolu değeri döndür (yoksa '')."""
    for k in keys:
        val = item.get(k)
        if val:
            return str(val).strip()
    return ""


def _norm_url(url: str) -> str:
    """Dedupe anahtarı: şema+host küçük harf, fragment at, sondaki '/' sadeleştir."""
    url = url.split("#", 1)[0].rstrip("/")
    return url.lower()


def normalize_sources(raw_items: list, provider: str) -> list[Source]:
    """Ham sonuç dict'lerini Source'a çevir: bozuk (url'siz/dict olmayan) atlanır, URL'e göre dedupe."""
    out: list[Source] = []
    seen: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        url = _first(item, _URL_KEYS)
        if not url:
            continue
        key = _norm_url(url)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            Source(
                title=_first(item, _TITLE_KEYS) or url,
                url=url,
                snippet=_first(item, _SNIPPET_KEYS),
                provider=provider,
                published_date=_first(item, _DATE_KEYS) or None,
            )
        )
    return out


def spoken_summary(text: str, max_chars: int = _SPOKEN_MAX) -> str:
    """Sesli okunacak biçim: '[n]' atıflarını sök, boşlukları sadeleştir, kelime sınırında kırp."""
    text = re.sub(r"\[\d+\]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    clipped = text[: max_chars - 1].rsplit(" ", 1)[0].rstrip()
    return f"{clipped}…"


def _tavily_http(query: str, api_key: str, max_results: int) -> dict:
    """Tavily /search POST (stdlib urllib → yeni bağımlılık yok). Hata → ResearchError."""
    payload = json.dumps(
        {"api_key": api_key, "query": query, "max_results": max_results}
    ).encode("utf-8")
    req = urllib.request.Request(
        _TAVILY_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise ResearchError("Tavily API anahtarı geçersiz (401).") from exc
        if exc.code == 429:
            raise ResearchError("Tavily oran sınırı aşıldı (429).") from exc
        raise ResearchError(f"Tavily HTTP hatası: {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise ResearchError(f"Tavily isteği başarısız: {exc}") from exc


class TavilyProvider:
    """Tavily adaptörü. `transport(query, api_key, max_results) -> dict` enjekte edilebilir (test)."""

    name = "tavily"

    def __init__(self, api_key: str, *, transport: Callable | None = None) -> None:
        self._api_key = api_key or ""
        self._transport = transport or _tavily_http

    def search(self, query: str, *, max_results: int) -> list[Source]:
        if not self._api_key:
            raise ResearchError("Tavily API anahtarı yok.")
        try:
            data = self._transport(query, self._api_key, max_results)
        except ResearchError:
            raise
        except Exception as exc:  # noqa: BLE001 — her taşıma hatası ResearchError'a sarılır
            raise ResearchError(f"Tavily isteği başarısız: {exc}") from exc
        results = data.get("results", []) if isinstance(data, dict) else []
        return normalize_sources(results, self.name)


def _ddgs_client(query: str, max_results: int) -> list[dict]:
    """Anahtarsız DuckDuckGo araması (ddgs lazy import → modül testte dep gerektirmez)."""
    from ddgs import DDGS

    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results))


class DDGProvider:
    """DuckDuckGo adaptörü (anahtarsız). `client_factory(query, max_results) -> list[dict]` enjekte edilebilir."""

    name = "duckduckgo"

    def __init__(self, *, client_factory: Callable | None = None) -> None:
        self._client = client_factory or _ddgs_client

    def search(self, query: str, *, max_results: int) -> list[Source]:
        try:
            raw = self._client(query, max_results)
        except Exception as exc:  # noqa: BLE001 — ağ/parse hatası ResearchError'a sarılır
            raise ResearchError(f"DuckDuckGo araması başarısız: {exc}") from exc
        return normalize_sources(list(raw), self.name)


def select_providers(
    cfg: dict,
    *,
    tavily_factory: Callable | None = None,
    ddg_factory: Callable | None = None,
) -> list[SearchProvider]:
    """config + key varlığına göre denenecek provider'ları SIRALI döndür.

    auto: key varsa [Tavily, DDG], yoksa [DDG]. tavily: [Tavily] (key yoksa ResearchError).
    duckduckgo: [DDG]. Fabrikalar enjekte edilebilir (test)."""
    tavily_factory = tavily_factory or (lambda key: TavilyProvider(key))
    ddg_factory = ddg_factory or (lambda: DDGProvider())
    rc = cfg.get("research", {})
    mode = rc.get("provider", "auto")
    key = (rc.get("tavily_api_key") or "").strip()

    if mode == "tavily":
        if not key:
            raise ResearchError("provider=tavily seçili ama Tavily API anahtarı yok.")
        return [tavily_factory(key)]
    if mode == "duckduckgo":
        return [ddg_factory()]
    # auto
    return [tavily_factory(key), ddg_factory()] if key else [ddg_factory()]


def research(
    query: str, cfg: dict, *, providers: list[SearchProvider] | None = None
) -> tuple[list[Source], str]:
    """Provider'ları sırayla dene; ilk boş-olmayan sonucu (+ provider adı) döndür.

    Bir provider ResearchError verir ya da boş dönerse sonrakine düşülür (auto'da Tavily→DDG).
    Hepsi başarısız/boşsa ResearchError. `providers` enjekte edilebilir (test)."""
    rc = cfg.get("research", {})
    max_results = int(rc.get("max_results", _DEFAULT_MAX_RESULTS))
    provs = providers if providers is not None else select_providers(cfg)

    last_err: ResearchError | None = None
    for provider in provs:
        try:
            sources = provider.search(query, max_results=max_results)
        except ResearchError as exc:
            last_err = exc
            continue
        if sources:
            return sources, provider.name
        last_err = ResearchError("Arama boş sonuç döndü.")
    raise last_err or ResearchError("Hiçbir arama sağlayıcısı sonuç vermedi.")
