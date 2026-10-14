"""Not biçimlendirme: Türkçe slug, frontmatter, Markdown render ve vault yazma."""
from __future__ import annotations

import json
import re
from pathlib import Path

from arda.models import Intent

# Türkçe karakterleri ASCII'ye indir (lower'dan önce; "İ".lower() Unicode'da bozuk olur).
_TR_MAP = str.maketrans(
    {
        "ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g", "ı": "i", "İ": "i",
        "ö": "o", "Ö": "o", "ş": "s", "Ş": "s", "ü": "u", "Ü": "u",
        "â": "a", "î": "i", "û": "u",
    }
)


def slugify_tr(text: str) -> str:
    """Türkçe metni dosya-adı dostu slug'a çevir: 'ORS Staj Notu' -> 'ors-staj-notu'."""
    text = text.translate(_TR_MAP).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


def build_frontmatter(intent: Intent, date: str) -> str:
    """Obsidian uyumlu YAML frontmatter bloğu döndür.

    Etiketler çift tırnakla (JSON-uyumlu kaçışla) yazılır; ':' '[' '"' gibi
    karakterler içeren etiketler YAML'ı bozmaz. Türkçe karakterler korunur."""
    tags = ", ".join(json.dumps(tag, ensure_ascii=False) for tag in intent.tags)
    return f"---\ntype: {intent.category}\ndate: {date}\ntags: [{tags}]\n---"


def render_note(intent: Intent, raw_text: str, date: str) -> str:
    """Tam not gövdesi: frontmatter + başlık + ham/düzenlenmiş/etiket/araştırma bölümleri."""
    keywords = " ".join(f"#{tag}" for tag in intent.tags)
    follow_up = "\n".join(f"- {item}" for item in intent.follow_up) or "- (yok)"
    return (
        f"{build_frontmatter(intent, date)}\n\n"
        f"# {intent.title}\n\n"
        f"## Ham Not\n{raw_text}\n\n"
        f"## Düzenlenmiş Not\n{intent.clean_text}\n\n"
        f"## Anahtar Kelimeler\n{keywords}\n\n"
        f"## Sonradan Araştırılacaklar\n{follow_up}\n"
    )


def _unique_path(folder: Path, slug: str) -> Path:
    """Çakışma olmayan dosya yolu döndür: slug.md, yoksa slug-2.md, slug-3.md ..."""
    candidate = folder / f"{slug}.md"
    if not candidate.exists():
        return candidate
    index = 2
    while True:
        candidate = folder / f"{slug}-{index}.md"
        if not candidate.exists():
            return candidate
        index += 1


def write_note(intent: Intent, raw_text: str, *, cfg: dict, date: str) -> Path:
    """Notu vault'taki kategori klasörüne yazar; klasör yoksa oluşturur.

    Aynı başlıkta var olan bir notu **silmez**: çakışmada slug-2.md, slug-3.md
    olarak yeni dosya açar (sessiz veri kaybını önler).
    """
    vault = Path(cfg["vault"]["path"])
    categories = cfg["vault"]["categories"]
    folder_name = categories.get(intent.category, categories.get("inbox", "Inbox"))
    folder = vault / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    path = _unique_path(folder, slugify_tr(intent.title))
    path.write_text(render_note(intent, raw_text, date), encoding="utf-8")
    return path


def render_research_note(query: str, summary: str, sources, *, date: str, provider: str) -> str:
    """Kaynaklı araştırma notu: frontmatter (type/query/provider/date/sources_count) +
    özet + numaralı kaynak listesi (tıklanabilir linkler)."""
    frontmatter = (
        "---\n"
        "type: research\n"
        f"query: {json.dumps(query, ensure_ascii=False)}\n"
        f"provider: {provider}\n"
        f"date: {date}\n"
        f"sources_count: {len(sources)}\n"
        "---"
    )
    src_lines = "\n".join(f"{i}. [{s.title}]({s.url})" for i, s in enumerate(sources, 1)) or "- (yok)"
    return (
        f"{frontmatter}\n\n"
        f"# {query}\n\n"
        f"## Özet\n{summary}\n\n"
        f"## Kaynaklar\n{src_lines}\n"
    )


def write_research_note(query: str, summary: str, sources, *, cfg: dict, date: str) -> Path:
    """Kaynaklı araştırma notunu vault/Research/'e yazar (çakışmada slug-2.md…).

    Provider, ilk kaynağın `.provider`'ından alınır (hepsi aynı sağlayıcıdan)."""
    vault = Path(cfg["vault"]["path"])
    categories = cfg["vault"]["categories"]
    folder = vault / categories.get("research", "Research")
    folder.mkdir(parents=True, exist_ok=True)
    provider = sources[0].provider if sources else "unknown"
    path = _unique_path(folder, slugify_tr(query) or "arastirma")
    path.write_text(
        render_research_note(query, summary, sources, date=date, provider=provider),
        encoding="utf-8",
    )
    return path


def append_task(intent: Intent, *, cfg: dict) -> Path:
    """Görevi Tasks.md dosyasına checkbox satırı olarak ekler."""
    vault = Path(cfg["vault"]["path"])
    tasks_file = cfg["vault"]["categories"].get("task", "Tasks.md")
    path = vault / tasks_file
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(f"- [ ] {intent.title} — {intent.clean_text}\n")
    return path


def save_to_inbox(raw_text: str, *, cfg: dict, date: str) -> Path:
    """Ham metni Inbox klasörüne kaydeder (asla-veri-kaybetme fallback).

    Aynı gün birden fazla çağrı **üzerine yazmaz**: var olan günlük dosyaya
    ekler; her giriş `---` ile ayrılır. İlk çağrı başlığı oluşturur.
    """
    vault = Path(cfg["vault"]["path"])
    inbox_name = cfg["vault"]["categories"].get("inbox", "Inbox")
    folder = vault / inbox_name
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"inbox-{date}.md"
    is_new = not path.exists()
    with path.open("a", encoding="utf-8") as f:
        if is_new:
            f.write(f"# Inbox — {date}\n\n")
        else:
            f.write("\n---\n\n")
        f.write(f"{raw_text}\n")
    return path
