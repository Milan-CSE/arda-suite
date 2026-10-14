"""Dikte (sesli yazma) — saf mantık: kayıt → STT → hafif temizlik → imlece yapıştır.

Mevcut ajan akışından AYRI: konuşma ham metne çevrilir, kural-tabanlı temizlenir ve
aktif uygulamadaki imlece yapıştırılır. UI bilmez; tüm yan etkiler enjekte edilir → test mock'lar.
"""
from __future__ import annotations

import re
import subprocess
import threading
import time

from arda.constants import STT_MODEL
from arda.listen import record_until_silence
from arda.transcribe import make_transcriber

# Cümle başında atılabilen güvenli dolgu kelimeleri (anlam taşımaz).
_FILLER = frozenset({"ee", "eee", "ıı", "ııı", "şey", "yani", "hani", "işte", "um", "uh", "uhm"})
_DEFAULT_TRANSCRIBE_TIMEOUT_SEC = 90.0
_DEFAULT_PASTE_TIMEOUT_SEC = 5.0
_CLIPBOARD_TIMEOUT_SEC = 2.0


def clean_text(raw: str, *, mode: str = "light") -> str:
    """STT ham çıktısını hafifçe temizle.

    mode="light" (varsayılan): boşluk normalize, baştaki dolgu kelimeleri at,
      noktalama öncesi boşluğu düzelt, ilk harfi büyüt.
    mode="none": yalnız kırp (ham).
    Boş/yalnız-boşluk → "".
    """
    text = (raw or "").strip()
    if not text or mode == "none":
        return text
    text = re.sub(r"\s+", " ", text)                       # çoklu boşluk → tek
    words = text.split(" ")
    while words and words[0].lower().strip(".,!?") in _FILLER:
        words.pop(0)                                       # baştaki dolgu sesleri
    text = " ".join(words)
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)           # noktalama öncesi boşluk
    if text:
        text = text[0].upper() + text[1:]                  # ilk harf büyük
    return text


# ⌘V'yi öndeki uygulamaya gönder (Accessibility izni gerekir).
_PASTE_SCRIPT = 'tell application "System Events" to keystroke "v" using command down'


def _call_with_timeout(fn, *, timeout: float, label: str):
    """Blocking bir çağrıyı daemon thread'de bekle; süre aşarsa caller'ı serbest bırak.

    Python thread'i zorla öldürülemez; ama UI/worker sonsuza kadar asılı kalmaz. Alttaki
    çağrı sonradan dönerse daemon thread doğal olarak biter.
    """
    box = {}

    def _run():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 — caller'a aynen ilet
            box["error"] = exc

    t = threading.Thread(target=_run, daemon=True, name=f"arda-{label}")
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"{label} zaman aşımı ({timeout:g} sn)")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _clip_get() -> str:
    # BAYT oku + errors='replace' ile çöz: pano geçersiz UTF-8 (MacRoman 0xd1 gibi)
    # içerebilir → text=True strict decode dikte turunu düşürürdü. Dayanıklı olmalı.
    res = subprocess.run(["pbpaste"], capture_output=True, check=False, timeout=_CLIPBOARD_TIMEOUT_SEC)
    return (res.stdout or b"").decode("utf-8", errors="replace")


def _clip_set(text: str) -> None:
    # Açıkça UTF-8'e kodla (str her zaman geçerli → çökme yok; locale'den bağımsız).
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False, timeout=_CLIPBOARD_TIMEOUT_SEC)


def insert_text(
    text: str,
    *,
    clip_get=_clip_get,
    clip_set=_clip_set,
    run=subprocess.run,
    sleep=time.sleep,
    restore_delay: float = 0.15,
    paste_timeout: float = _DEFAULT_PASTE_TIMEOUT_SEC,
) -> bool:
    """Metni aktif uygulamaya yapıştır: pano-koru → set → ⌘V → eski panoyu geri-yükle.

    Boş metin → no-op (False). Yapıştırma başarısızsa eski pano GERİ YÜKLENMEZ
    (metin panoda kalır → kullanıcı elle ⌘V yapar). Odak çalınmaz: ⌘V öndeki app'e gider.
    """
    if not text:
        return False
    previous = clip_get()
    clip_set(text)
    try:
        res = run(
            ["osascript", "-e", _PASTE_SCRIPT],
            capture_output=True,
            text=True,
            check=False,
            timeout=paste_timeout,
        )
    except subprocess.TimeoutExpired:
        return False
    ok = getattr(res, "returncode", 1) == 0
    if ok:
        sleep(restore_delay)        # app pano'yu okusun, sonra eski içeriği geri koy
        clip_set(previous)
    return ok


def dictate_once(
    cfg: dict, transcriber, *, record=record_until_silence, on_state=lambda s: None,
    should_stop=lambda: False,
) -> str:
    """Tek dikte turu: kaydet → yazıya çevir → hafifçe temizle. Metni döner (boş olabilir).

    on_state("listening") kayıttan önce, on_state("transcribing") çeviriden önce çağrılır →
    amblem canlı durum gösterir. should_stop() True olursa (amblem 'Durdur' tıklaması / ⌘⇧D
    tekrar) kayıt hemen kesilir → o ana kadarki ses yazıya çevrilir (boşsa "" döner)."""
    on_state("listening")
    wav = record(cfg, should_stop=should_stop)
    on_state("transcribing")
    timeout = float(cfg.get("dictation", {}).get("transcribe_timeout_sec", _DEFAULT_TRANSCRIBE_TIMEOUT_SEC))
    raw = _call_with_timeout(lambda: transcriber.transcribe(wav), timeout=timeout, label="dictation-transcribe")
    mode = cfg.get("dictation", {}).get("cleanup", "light")
    return clean_text(raw, mode=mode)


def run_dictation_cycle(
    cfg, transcriber, set_state, *, should_stop=lambda: False,
    dictate=dictate_once, insert=insert_text,
) -> str:
    """Amblem için tam dikte turu. set_state(state_str) ana-thread'e durum yayar.

    dictate listening/transcribing'i yayar; burada done/idle/error eklenir.
    should_stop amblem 'Durdur' tıklamasını kayda iletir (erken kesme).
    İstisna olursa state=error yapılıp yeniden fırlatılır (sessiz yutma yok)."""
    try:
        text = dictate(cfg, transcriber, on_state=set_state, should_stop=should_stop)
    except Exception:
        set_state("error")
        raise
    if not text:
        set_state("idle")
        return ""
    set_state("pasting")
    try:
        ok = insert(text)
    except Exception:
        set_state("error")
        raise
    set_state("done" if ok else "error")
    return text if ok else ""


def make_dictation_transcriber(cfg: dict):
    """Dikte için her zaman çok-dilli whisper transcriber (auto dil algılama).

    parakeet İngilizce-odaklı; dikte Türkçe+İngilizce karışık olabildiği için whisper'a
    zorlanır. language "auto"/None → mlx-whisper otomatik dil algılar."""
    d = cfg.get("dictation", {})
    lang = d.get("language", "auto")
    stt = {
        "backend": "whisper",
        "model": cfg.get("stt", {}).get("model", STT_MODEL),
        "language": None if lang in ("auto", None) else lang,
    }
    return make_transcriber({**cfg, "stt": stt})
