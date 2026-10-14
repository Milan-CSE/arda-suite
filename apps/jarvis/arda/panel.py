"""Masaüstü kontrol paneli — native NSWindow. Kontrol + 'Beni Tanı' + Hafıza.

İki katman:
  (1) Saf mantık (kontrol dispatch, profil kaydetme, hafıza sorgu) — AppKit'siz test edilir.
  (2) _build/_render — native görünüm; pyobjc lazy import (overlay.py deseni). pyobjc yoksa
      panel sessizce devre dışı, app ÇÖKMEZ. Tüm AppKit çağrıları ANA THREAD'de (çağıran sorumlu).
"""
from __future__ import annotations

import logging
from typing import Callable

from arda import overlay  # is_available() DRY
from arda.runtime_hud import RuntimeProjection

log = logging.getLogger(__name__)


def is_available() -> bool:
    """pyobjc/AppKit kurulu mu (pencere çizilebilir mi)?"""
    return overlay.is_available()


def parse_commands(text: str) -> list[dict]:
    """'say => intent' satırlarını [{say, intent}] listesine çevir. Eksik/boş satır atlanır."""
    out: list[dict] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or "=>" not in line:
            continue
        say, _, intent = line.partition("=>")
        say, intent = say.strip(), intent.strip()
        if say and intent:
            out.append({"say": say, "intent": intent})
    return out


def format_commands(commands: list) -> str:
    """[{say, intent}] → 'say => intent' satırları (panel metin kutusu için)."""
    lines = []
    for c in commands or []:
        if isinstance(c, dict):
            say = str(c.get("say", "")).strip()
            intent = str(c.get("intent", "")).strip()
            if say and intent:
                lines.append(f"{say} => {intent}")
    return "\n".join(lines)


class ControlPanel:
    """Kontrol penceresi. Saf mantık + native görünüm (lazy AppKit)."""

    def __init__(
        self,
        *,
        controls,
        cfg: dict,
        mem_store,
        on_record: Callable[[], bool],
        on_cancel: Callable[[], None],
        on_toggle_wake: Callable[[], bool],
        on_toggle_dictation: Callable[[], bool],
        on_open_vault: Callable[[], None],
        save_local: Callable[[dict], None] | None = None,
    ) -> None:
        self._controls = controls
        self._cfg = cfg
        self._mem = mem_store
        self._on_record = on_record
        self._on_cancel = on_cancel
        self._on_toggle_wake = on_toggle_wake
        self._on_toggle_dictation = on_toggle_dictation
        self._on_open_vault = on_open_vault
        if save_local is None:
            from arda.config import save_local as _sl
            save_local = _sl
        self._save_local = save_local
        self._window = None
        self._status_field = None
        self._last_state = None
        self._runtime = RuntimeProjection()
        self._runtime_status_field = None
        self._runtime_timeline_tv = None

    # --- 'Beni Tanı' (profil) ---
    def save_profile(self, *, about: str, vocabulary: str, commands_text: str) -> None:
        prof = {
            "about": (about or "").strip(),
            "vocabulary": (vocabulary or "").strip(),
            "commands": parse_commands(commands_text),
        }
        self._cfg.setdefault("profile", {}).update(prof)  # canlı etki (bir sonraki tur)
        try:
            self._save_local({"profile": prof})
        except Exception as exc:  # noqa: BLE001 — kayıt hatası UI'ı çökertmesin
            log.warning("Profil kaydedilemedi (yok sayıldı): %s", exc)

    def profile_fields(self) -> tuple[str, str, str]:
        p = self._cfg.get("profile", {})
        p = p if isinstance(p, dict) else {}
        about = str(p.get("about", "") or "")
        vocab = str(p.get("vocabulary", "") or "")
        return about, vocab, format_commands(p.get("commands", []))

    # --- Hafıza ---
    def memory_enabled(self) -> bool:
        try:
            return bool(self._mem is not None and self._mem.enabled)
        except Exception as exc:  # noqa: BLE001
            log.debug("Hafıza durumu okunamadı: %s", exc)
            return False

    def load_memories(self, limit: int = 50) -> list:
        if not self.memory_enabled():
            return []
        try:
            return list(self._mem.list_all())[:limit]
        except Exception as exc:  # noqa: BLE001
            log.warning("Hafıza okunamadı: %s", exc)
            return []

    def search_memories(self, query: str) -> list:
        if not self.memory_enabled() or not (query or "").strip():
            return []
        try:
            return list(self._mem.search_raw(query))
        except Exception as exc:  # noqa: BLE001
            log.warning("Hafıza aranamadı: %s", exc)
            return []

    def delete_memory(self, memory_id) -> bool:
        if not self.memory_enabled():
            return False
        try:
            self._mem.delete(memory_id)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("Hafıza silinemedi: %s", exc)
            return False

    # --- durum güncelleme (menubar tick'inden, ANA THREAD) ---
    def update(self, state, message: str = "") -> None:
        self._last_state = state
        if self._window is None or self._status_field is None:
            return
        try:
            self._status_field.setStringValue_(message or str(state))
        except Exception as exc:  # noqa: BLE001
            log.debug("Panel durum güncellenemedi: %s", exc)

    def update_runtime(self, event) -> None:
        """Project a structured runtime event; called only from the main UI thread."""
        snapshot = self._runtime.apply(event)
        if self._runtime_status_field is None:
            return
        try:
            elapsed = self._runtime.elapsed_seconds()
            elapsed_text = f" · {elapsed:.0f}s" if elapsed is not None else ""
            self._runtime_status_field.setStringValue_(
                f"{snapshot.status}{elapsed_text} · {snapshot.run_id or 'aktif koşu yok'}"
            )
            if self._runtime_timeline_tv is not None:
                lines = [
                    f"{item.step_id} · {item.tool} · {item.status}"
                    for item in snapshot.tools
                ]
                approval = f"Onay: {snapshot.approval_status}" if snapshot.approval_status else ""
                terminal = f"Sonuç: {snapshot.terminal_outcome}" if snapshot.terminal_outcome else ""
                self._runtime_timeline_tv.setString_("\n".join(lines + ([approval] if approval else []) + ([terminal] if terminal else [])) or "(runtime olayı bekleniyor)")
        except Exception as exc:  # noqa: BLE001
            log.debug("Runtime paneli güncellenemedi: %s", exc)

    # --- pencere yaşam döngüsü (native, ANA THREAD) ---
    def open(self) -> None:
        """Pencereyi kur/öne getir. pyobjc yoksa bildirim ipucu + no-op (çökmez)."""
        if not is_available():
            log.warning("pyobjc yok — kontrol paneli devre dışı.")
            return
        if self._window is not None:
            try:
                self._window.makeKeyAndOrderFront_(None)
            except Exception as exc:  # noqa: BLE001
                log.debug("Panel öne getirilemedi: %s", exc)
            return
        try:
            self._build()
        except Exception as exc:  # noqa: BLE001 — panel opsiyonel, app çökmesin
            log.warning("Panel kurulamadı (devre dışı): %s", exc)

    def close(self) -> None:
        if self._window is not None:
            try:
                self._window.orderOut_(None)
            except Exception as exc:  # noqa: BLE001
                log.debug("Panel gizlenemedi: %s", exc)

    # --- native görünüm (unit test edilmez) ---
    def _build(self) -> None:
        import AppKit

        panel_obj = self  # closure için

        class _Target(AppKit.NSObject):
            def onRecord_(self, _sender):
                panel_obj._on_record()
            def onCancel_(self, _sender):
                panel_obj._on_cancel()
            def onWake_(self, _sender):
                panel_obj._on_toggle_wake()
            def onDictation_(self, _sender):
                panel_obj._on_toggle_dictation()
            def onVault_(self, _sender):
                panel_obj._on_open_vault()
            def onSaveProfile_(self, _sender):
                panel_obj._save_profile_from_fields()
            def onMemRefresh_(self, _sender):
                panel_obj._render_memories()
            def onMemDelete_(self, _sender):
                panel_obj._delete_from_field()

        self._target = _Target.alloc().init()

        W, H = 480.0, 900.0
        style = (
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskMiniaturizable
        )
        rect = AppKit.NSMakeRect(0, 0, W, H)
        win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, AppKit.NSBackingStoreBuffered, False
        )
        win.setTitle_("Jarvis — Kontrol Paneli")
        win.setReleasedWhenClosed_(False)
        win.center()
        content = win.contentView()

        def label(text, x, y, w=440, size=12, bold=False):
            f = AppKit.NSTextField.labelWithString_(text)
            f.setFrame_(AppKit.NSMakeRect(x, y, w, 18))
            if bold:
                f.setFont_(AppKit.NSFont.boldSystemFontOfSize_(size))
            content.addSubview_(f)
            return f

        def button(title, x, y, sel, w=120):
            b = AppKit.NSButton.buttonWithTitle_target_action_(title, self._target, sel)
            b.setFrame_(AppKit.NSMakeRect(x, y, w, 28))
            content.addSubview_(b)
            return b

        def field(text, x, y, w=440, h=22):
            tf = AppKit.NSTextField.alloc().initWithFrame_(AppKit.NSMakeRect(x, y, w, h))
            tf.setStringValue_(text or "")
            content.addSubview_(tf)
            return tf

        def textview(text, x, y, w=440, h=80):
            scroll = AppKit.NSScrollView.alloc().initWithFrame_(AppKit.NSMakeRect(x, y, w, h))
            scroll.setHasVerticalScroller_(True)
            scroll.setBorderType_(AppKit.NSBezelBorder)
            tv = AppKit.NSTextView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, w, h))
            tv.setString_(text or "")
            scroll.setDocumentView_(tv)
            content.addSubview_(scroll)
            return tv

        # --- 1) Durum ---
        label("DURUM", 20, H - 34, size=13, bold=True)
        self._status_field = label(
            (self._last_state and str(self._last_state)) or "Hazır", 20, H - 58, size=12
        )

        # --- 2) Agent runtime ---
        label("AGENT RUNTIME", 20, H - 92, size=13, bold=True)
        self._runtime_status_field = label("idle · aktif koşu yok", 20, H - 116, size=12)
        self._runtime_timeline_tv = textview("(runtime olayı bekleniyor)", 20, H - 240, h=105)

        # --- 3) Kontrol ---
        label("KONTROL", 20, H - 270, size=13, bold=True)
        button("Dinle", 20, H - 304, b"onRecord:")
        button("Durdur", 150, H - 304, b"onCancel:")
        button("Wake Aç/Kapa", 280, H - 304, b"onWake:", w=140)
        button("Dikte Aç/Kapa", 20, H - 338, b"onDictation:", w=140)
        button("Vault'u Aç", 170, H - 338, b"onVault:")

        # --- 4) Beni Tanı ---
        about, vocab, cmds = self.profile_fields()
        label("BENİ TANI", 20, H - 374, size=13, bold=True)
        label("Hakkımda:", 20, H - 396, size=11)
        self._about_tv = textview(about, 20, H - 480, h=80)
        label("Sözlük (isim/terim):", 20, H - 504, size=11)
        self._vocab_field = field(vocab, 20, H - 528)
        label("Komutlar (say => intent):", 20, H - 550, size=11)
        self._cmds_tv = textview(cmds, 20, H - 634, h=80)
        button("Profili Kaydet", 20, H - 668, b"onSaveProfile:", w=140)

        # --- 5) Hafıza ---
        label("HAFIZA", 20, H - 706, size=13, bold=True)
        self._mem_field = field("", 20, H - 730, w=280)  # silinecek id
        button("Sil", 310, H - 732, b"onMemDelete:", w=60)
        button("Yenile", 380, H - 732, b"onMemRefresh:", w=80)
        self._mem_tv = textview("", 20, H - 784, h=44)
        self._render_memories()
        self.update_runtime(None)

        win.makeKeyAndOrderFront_(None)
        self._window = win

    def _save_profile_from_fields(self) -> None:
        self.save_profile(
            about=self._about_tv.string(),
            vocabulary=self._vocab_field.stringValue(),
            commands_text=self._cmds_tv.string(),
        )
        if self._status_field is not None:
            self._status_field.setStringValue_("Profil kaydedildi ✓")

    def _render_memories(self) -> None:
        if self._mem_tv is None:
            return
        if not self.memory_enabled():
            self._mem_tv.setString_("(hafıza kapalı)")
            return
        items = self.load_memories()
        lines = [self._mem_line(i) for i in items] or ["(kayıt yok)"]
        self._mem_tv.setString_("\n".join(lines))

    @staticmethod
    def _mem_line(item) -> str:
        if isinstance(item, dict):
            mid = item.get("id", "?")
            text = item.get("memory") or item.get("text") or item.get("data") or item
            return f"[{mid}] {text}"
        return str(item)

    def _delete_from_field(self) -> None:
        mid = self._mem_field.stringValue().strip()
        if mid and self.delete_memory(mid):
            self._mem_field.setStringValue_("")
            self._render_memories()
