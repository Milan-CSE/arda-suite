"""macOS menü çubuğu app'i — ajan döngüsünü canlı UI'ya bağlar.

Thread modeli (kritik):
- Worker thread: `agent.run_forever` çalışır; UI'a ASLA dokunmaz. Yalnızca
  `on_event(AgentEvent)` ile olay yayınlar → thread-safe `queue.Queue`'ya konur.
- Ana thread: `rumps.Timer` kuyruğu boşaltıp ikon/başlık/menüyü günceller.
- UI → worker: `Controls` (manuel tetik + wake aç/kapa), thread-safe.
- Global kısayol (pynput) ayrı thread'de çalışır; yalnızca `Controls.request_manual()`
  çağırır (UI'a dokunmaz). pynput yoksa/izin yoksa app ÇÖKMEZ — menüden "Şimdi Kaydet".

rumps ve pynput lazy import edilir → bu modül onlarsız da import edilebilir (test).
"""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import threading

from arda import dictation, overlay, panel, speak
from arda.agent import AgentDeps, run_forever
from arda.config import save_local
from arda.constants import ASSISTANT_NAME, TTS_VOICE, TTS_VOICE_OPTIONS
from arda.models import AgentEvent, AgentState

log = logging.getLogger(__name__)

_ASSETS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
_ICON_PATH = os.path.join(_ASSETS, "icon_menubar.png")

# Durum başlıkları (ikon sabit kalır; küçük harf metin durum ipucu verir).
_ICON = {state: "" for state in AgentState}  # title boş → rumps sadece ikonu gösterir

# Her durum için menubar title overlay'i (ikon yanında görünen kısa metin).
_STATE_BADGE = {
    AgentState.LISTENING: "",
    AgentState.WOKE: "·",
    AgentState.RECORDING: "●",
    AgentState.TRANSCRIBING: "…",
    AgentState.THINKING: "…",
    AgentState.DONE: "✓",
    AgentState.ERROR: "!",
}
_LABEL = {
    AgentState.LISTENING: "Hazır — konuşmak için ⌘⇧A",
    AgentState.WOKE: "Uyandı…",
    AgentState.RECORDING: "Dinliyor…",
    AgentState.TRANSCRIBING: "Yazıya çeviriyor…",
    AgentState.THINKING: "Düşünüyor…",
    AgentState.DONE: "Yazıldı ✓",
    AgentState.ERROR: "Hata oluştu (inbox'a düştü)",
}
_RUNTIME_LABEL = {
    "agent.run.started": "Agent çalışıyor…",
    "agent.planning.started": "Planlıyor…",
    "agent.tool.started": "Araç çalışıyor…",
    "agent.approval.requested": "Onay bekleniyor…",
    "agent.approval.resolved": "Onay işlendi…",
    "agent.run.completed": "Agent tamamlandı ✓",
    "agent.run.failed": "Agent başarısız oldu !",
    "agent.run.cancelled": "Agent iptal edildi",
    "agent.run.timed_out": "Agent zaman aşımına uğradı",
}
# "Durdur" bu durumlarda aktiftir → kullanıcı kaydı/işlemeyi/konuşmayı her an kesebilir.
_STOPPABLE_STATES = frozenset(
    {AgentState.WOKE, AgentState.RECORDING, AgentState.TRANSCRIBING, AgentState.THINKING}
)


def doctor_report() -> str:
    """Build the user-facing first-run health report shown by the menu item."""
    from arda import permissions
    from arda.platform_runtime import runtime_report

    return permissions.permission_report() + "\n\n" + runtime_report()

# Ayarlardaki ses seçenekleri (tek kaynak: constants.TTS_VOICE_OPTIONS; mevcut ses işaretlenir).
_VOICES = list(TTS_VOICE_OPTIONS)
# VAD eşiği ön ayarları: etiket → energy_threshold (None = otomatik kalibrasyon).
_VAD_PRESETS = [("Otomatik (kalibre)", None), ("Düşük eşik", 0.005),
                ("Orta", 0.01), ("Yüksek eşik", 0.02)]
# Uyandırma kelimesi motorları
_WAKE_BACKENDS = [
    ("openWakeWord (hafif)", "oww"),
    ("Whisper (doğru)", "whisper"),
    ("Vosk (offline)", "vosk"),
    ("Parakeet (İngilizce)", "parakeet"),
]


def run(agent_deps: AgentDeps, *, ack: str, controls, cfg: dict,
        dictation_transcriber=None, mem_store=None) -> None:
    """Menü çubuğu uygulamasını başlat (ana thread'i bloklar).

    dictation_transcriber verilirse (ve dictation.enabled) yüzen dikte amblemi +
    ⌘⇧D kısayolu kurulur (Wispr Flow tarzı sesli yazma)."""
    import rumps

    events: "queue.Queue[AgentEvent]" = queue.Queue()
    dict_enabled = dictation_transcriber is not None and bool(cfg.get("dictation", {}).get("enabled", False))

    def on_event(event: AgentEvent) -> None:
        # Worker thread'den çağrılır: UI'a DOKUNMA — sadece kuyruğa koy + meşgul bayrağı.
        controls.set_busy_for(event.state)
        events.put(event)

    class ArdaApp(rumps.App):
        def __init__(self) -> None:
            icon = _ICON_PATH if os.path.exists(_ICON_PATH) else None
            super().__init__(ASSISTANT_NAME, icon=icon, title=_STATE_BADGE[AgentState.LISTENING], quit_button="Çıkış")
            if icon:
                self.template = True  # macOS dark/light mode otomatik adaptasyonu
            self._status = rumps.MenuItem(_LABEL[AgentState.LISTENING])
            self._record = rumps.MenuItem("🎙️  Dinle  (⌘⇧A)", callback=self._on_record)
            self._wake = rumps.MenuItem("Uyandırma kelimesi (her-zaman-dinle)", callback=self._on_toggle_wake)
            self._wake.state = controls.wake_enabled()
            # Dikte amblemi (Wispr Flow tarzı). Amblem durumları worker → ana thread kuyruğu.
            self._overlay_q: "queue.Queue[str]" = queue.Queue()
            self._overlay = overlay.OverlayController(on_toggle=controls.request_dictation) if dict_enabled else None
            self._panel = panel.ControlPanel(
                controls=controls, cfg=cfg, mem_store=mem_store,
                on_record=lambda: controls.request_manual(),
                on_cancel=lambda: self._on_cancel(None),
                on_toggle_wake=self._panel_toggle_wake,
                on_toggle_dictation=self._panel_toggle_dictation,
                on_open_vault=lambda: self._on_open_vault(None),
            )
            self._dict_item = rumps.MenuItem("Dikte amblemi  (⌘⇧D)", callback=self._on_toggle_overlay)
            self._dict_item.state = dict_enabled
            self._stop = rumps.MenuItem("⏹  Durdur", callback=None)  # THINKING'de aktif
            self.menu = [
                self._status,
                None,
                self._record,
                self._stop,
                None,
                self._wake,
                self._wake_backend_menu(rumps),
                self._dict_item,
                self._voice_menu(rumps),
                self._vad_menu(rumps),
                rumps.MenuItem("🪟  Panel'i Aç  (⌘⇧P)", callback=self._on_open_panel),
                rumps.MenuItem("Vault'u Aç", callback=self._on_open_vault),
                rumps.MenuItem("🩺  İzinleri Denetle (Doctor)", callback=self._on_check_permissions),
            ]

            # TÜM global kısayollar TEK listener'da (⌘⇧A + ⌘⇧P + varsa ⌘⇧D).
            # İki ayrı GlobalHotKeys açılışta eşzamanlı TIS init → HIToolbox SIGABRT (uygulama açılmaz).
            self._hotkey = _start_hotkeys(controls, cfg, include_dictation=dict_enabled)
            # Worker thread'i Timer'dan başlat (AppKit runloop tam kurulduktan sonra).
            # __init__'te başlatmak CoreAudio/AVAudioSession'ı runloop hazır olmadan açar
            # → intermittent segfault (Faz 2 wake/sounddevice ile tetikleniyor).
            self._agent_started = False
            self._timer = rumps.Timer(self._drain_events, 0.2)
            self._timer.start()

        # --- ana thread: kuyruğu boşalt, UI'ı güncelle ---
        def _drain_events(self, _timer) -> None:
            if not self._agent_started:
                self._agent_started = True
                threading.Thread(
                    target=run_forever,
                    kwargs={"deps": agent_deps, "ack": ack, "on_event": on_event},
                    daemon=True,
                    name="arda-agent",
                ).start()
                # Açılır açılmaz dinle: ⌘⇧A beklemeden ilk dinleme turunu tetikle.
                # request_manual sadece bir Event set eder → agent thread hazır olunca tüketir.
                if cfg.get("app", {}).get("listen_on_start", False):
                    controls.request_manual()
                # Dikte: amblemi göster + ⌘⇧D + worker (AppKit runloop hazır olduktan SONRA).
                if self._overlay is not None:
                    self._overlay.show()
                    # ⌘⇧D kısayolu artık __init__'teki tek listener'da kayıtlı (bkz. _start_hotkeys).
                    threading.Thread(
                        target=_dictation_worker,
                        kwargs={"controls": controls, "cfg": cfg,
                                "transcriber": dictation_transcriber, "overlay_q": self._overlay_q},
                        daemon=True,
                        name="arda-dictation",
                    ).start()
            # Dikte amblem durumlarını ana thread'de uygula (her tick — agent olayından bağımsız).
            if self._overlay is not None:
                try:
                    while True:
                        self._overlay.set_state(self._overlay_q.get_nowait())
                except queue.Empty:
                    pass
            # Panel açma sinyalini her tick'te tüket (agent olayından bağımsız) — boş
            # batch'te erken dönüş ⌘⇧P'nin bir sonraki olaya kadar gecikmesine yol açmasın.
            if controls.consume_open_panel():
                self._panel.open()
            batch = []
            try:
                while True:
                    batch.append(events.get_nowait())
            except queue.Empty:
                pass
            if not batch:
                return
            # Tüm partiyi gez: DONE/ERROR bildirimleri kaybolmasın (DONE'dan hemen
            # sonra LISTENING gelir; sadece sonuncuyu alırsak 'yazıldı' bildirimi düşer).
            last_runtime = None
            for ev in batch:
                if ev.runtime is not None:
                    last_runtime = ev.runtime
                    self._panel.update_runtime(ev.runtime)
                if ev.state is AgentState.DONE and ev.message:
                    _notify(rumps, ASSISTANT_NAME, "", ev.message)
                elif ev.state is AgentState.ERROR:
                    _notify(rumps, ASSISTANT_NAME, "", ev.message or "Hata — inbox'a kaydedildi")
            # İkon/başlık partinin son durumunu yansıtır.
            last = batch[-1]
            self.title = _STATE_BADGE.get(last.state, "")
            # THINKING'de olay bir mesaj taşıyorsa (agent adım ilerlemesi) onu canlı göster;
            # yoksa duruma göre sabit etiket. Diğer durumlar her zaman sabit etikette kalır.
            if last.state is AgentState.THINKING and last_runtime is not None:
                self._status.title = _RUNTIME_LABEL.get(last_runtime.event_type, last.message or "Agent çalışıyor…")
            elif last.state is AgentState.THINKING and last.message:
                self._status.title = last.message
            else:
                self._status.title = _LABEL.get(last.state, str(last.state))
            # "Durdur" düğmesi: TÜM meşgul durumlarda aktif (kayıt/uyandı/yazıya-çevirme/
            # düşünme) → kullanıcı her an durdurabilir, çıkış yapmak zorunda kalmaz.
            if last.state in _STOPPABLE_STATES:
                self._stop.set_callback(self._on_cancel)
            else:
                self._stop.set_callback(None)
            # Kontrol paneli açıksa canlı durumu yansıt (kapalıysa ucuz no-op).
            self._panel.update(last.state, self._status.title)

        # --- menü eylemleri (hepsi ana thread) ---
        def _on_cancel(self, _) -> None:
            speak.interrupt()               # konuşuyorsa TTS'i hemen sustur
            controls.request_cancel()       # kayıt/agent koşusunu kes (should_stop)
            self._status.title = "Durduruldu."
            self._stop.set_callback(None)   # çift tık engelle

        def _on_record(self, _) -> None:
            if not controls.request_manual():
                _notify(rumps, ASSISTANT_NAME, "", "Şu an meşgul — kayıt/işleme sürüyor.")

        def _on_toggle_wake(self, sender) -> None:
            sender.state = not sender.state
            controls.set_wake(bool(sender.state))
            _persist({"app": {"wake_enabled": bool(sender.state)}})

        def _on_toggle_overlay(self, sender) -> None:
            # Dikte transcriber yoksa (dictation.enabled=false ile başlatıldıysa) işlem yok.
            if self._overlay is None:
                _notify(rumps, ASSISTANT_NAME, "", "Dikte kapalı — config.yaml → dictation.enabled: true ve yeniden başlat.")
                return
            sender.state = not sender.state
            self._overlay.show() if sender.state else self._overlay.hide()
            _persist({"dictation": {"enabled": bool(sender.state)}})

        def _on_open_vault(self, _) -> None:
            path = str(cfg.get("vault", {}).get("path", ""))
            if path:
                subprocess.run(["open", path], check=False)

        def _on_open_panel(self, _) -> None:
            self._panel.open()

        def _on_check_permissions(self, _) -> None:
            from arda import permissions
            try:
                report = doctor_report()
                status = permissions.check_all_permissions()
                if not status.get("accessibility") or not status.get("microphone"):
                    resp = rumps.alert(
                        title=f"{ASSISTANT_NAME} İzin Durumu",
                        message=report + "\n\nSistem Ayarları'nı açmak ister misiniz?",
                        ok="Sistem Ayarları",
                        cancel="Kapat",
                    )
                    if resp == 1:
                        if not status.get("accessibility"):
                            permissions.open_accessibility_settings()
                        elif not status.get("microphone"):
                            permissions.open_microphone_settings()
                else:
                    rumps.alert(title=f"{ASSISTANT_NAME} İzin Durumu", message=report, ok="Tamam")
            except Exception as exc:  # noqa: BLE001
                log.warning("İzin denetimi gösterilemedi: %s", exc)

        def _panel_toggle_wake(self) -> bool:
            new = not controls.wake_enabled()
            controls.set_wake(new)
            self._wake.state = new  # menü onay işaretini de eşitle
            _persist({"app": {"wake_enabled": new}})
            return new

        def _panel_toggle_dictation(self) -> bool:
            if self._overlay is None:
                return False
            new = not bool(self._dict_item.state)
            self._dict_item.state = new
            self._overlay.show() if new else self._overlay.hide()
            _persist({"dictation": {"enabled": new}})
            return new

        # --- ayar alt-menüleri ---
        def _voice_menu(self, rumps):
            root = rumps.MenuItem("Ses")
            current = cfg.get("tts", {}).get("voice", TTS_VOICE)
            for v in _VOICES:
                item = rumps.MenuItem(v, callback=self._make_set_voice(v))
                item.state = (v == current)
                root.add(item)
            return root

        def _make_set_voice(self, voice):
            def _cb(_sender):
                cfg.setdefault("tts", {})["voice"] = voice          # canlı etki
                _persist({"tts": {"voice": voice}})
            return _cb

        def _vad_menu(self, rumps):
            root = rumps.MenuItem("VAD eşiği")
            current = cfg.get("vad", {}).get("energy_threshold")
            for label, value in _VAD_PRESETS:
                item = rumps.MenuItem(label, callback=self._make_set_vad(value))
                item.state = (value == current)
                root.add(item)
            return root

        def _make_set_vad(self, value):
            def _cb(_sender):
                cfg.setdefault("vad", {})["energy_threshold"] = value  # canlı etki
                _persist({"vad": {"energy_threshold": value}})
            return _cb

        def _wake_backend_menu(self, rumps):
            root = rumps.MenuItem("Wake motoru")
            current = cfg.get("wake", {}).get("backend", "oww")
            for label, val in _WAKE_BACKENDS:
                item = rumps.MenuItem(label, callback=self._make_set_wake_backend(val, label))
                item.state = (val == current)
                root.add(item)
            return root

        def _make_set_wake_backend(self, backend, label):
            def _cb(_sender):
                cfg.setdefault("wake", {})["backend"] = backend
                _persist({"wake": {"backend": backend}})
                _notify(rumps, ASSISTANT_NAME, "", f"Wake motoru '{label}' olarak ayarlandı.")
            return _cb

    ArdaApp().run()


def _persist(updates: dict) -> None:
    """Ayarı config.local.yaml'a yaz; hata UI'ı çökertmesin."""
    try:
        save_local(updates)
    except Exception as exc:  # noqa: BLE001
        log.warning("Ayar kaydedilemedi (yok sayıldı): %s", exc)


def _notify(rumps, title: str, subtitle: str, message: str) -> None:
    """rumps bildirimi; bundle dışı çalıştırmada hata verirse sessizce geç."""
    try:
        rumps.notification(title, subtitle, message)
    except Exception as exc:  # noqa: BLE001
        log.debug("Bildirim gösterilemedi: %s", exc)


def _dictation_hotkey_spec(cfg) -> str:
    """config'teki dikte kısayolunu pynput GlobalHotKeys formatına çevir (ör. cmd+shift+d → <cmd>+<shift>+d)."""
    combo = str(cfg.get("dictation", {}).get("hotkey", "cmd+shift+d"))
    return "+".join(
        f"<{p}>" if p in ("cmd", "shift", "ctrl", "alt") else p
        for p in combo.lower().split("+")
    )


def _start_hotkeys(controls, cfg, *, include_dictation: bool):
    """TÜM global kısayolları TEK bir GlobalHotKeys listener'ında kur → tek thread → tek TIS init.

    KÖK NEDEN (regresyon): her ayrı GlobalHotKeys kendi thread'ini başlatır; pynput'un macOS
    backend'i o thread'de TISCopyCurrentKeyboardInputSource çağırır. İki listener açılışta
    eşzamanlı kalkınca HIToolbox "TIS/TSM iki thread'de birden" deyip SIGABRT atar (uygulama
    açılmaz). Tek listener bu eşzamanlılığı ortadan kaldırır.

    Kısayollar UI'a DOKUNMAZ — yalnızca `Controls`'a sinyal koyar; ana thread `_drain_events`
    tick'inde tüketir. pynput yok/izin yoksa ÇÖKME — None dön (menü fallback'leri çalışır)."""
    try:
        from pynput import keyboard
    except Exception as exc:  # noqa: BLE001 — pynput kurulu değil
        log.warning("pynput yok; global kısayollar devre dışı. Menü öğelerini kullan: %s", exc)
        return None
    mapping = {
        "<cmd>+<shift>+a": controls.request_manual,
        "<cmd>+<shift>+p": controls.request_open_panel,
    }
    if include_dictation:
        mapping[_dictation_hotkey_spec(cfg)] = controls.request_dictation
    try:
        hk = keyboard.GlobalHotKeys(mapping)
        hk.daemon = True
        hk.start()
        return hk
    except Exception as exc:  # noqa: BLE001 — erişilebilirlik izni yok vb.
        log.warning("Global kısayollar kurulamadı (izin yok?); menü öğelerini kullan: %s", exc)
        return None


def _dictation_worker(*, controls, cfg, transcriber, overlay_q) -> None:
    """Dikte tetiğini bekle → tur koştur. Durumlar kuyruğa (ana thread amblemi günceller).

    UI'a ASLA dokunmaz (mevcut thread modeli). Tek tur hatası worker'ı öldürmez."""
    while True:
        if not controls.wait_for_dictation(timeout=1.0):
            continue
        controls.begin_dictation()   # tur aktif → amblem tıklaması artık DURDURur
        try:
            dictation.run_dictation_cycle(
                cfg, transcriber, overlay_q.put,
                should_stop=controls.dictation_should_stop,
            )
        except Exception as exc:  # noqa: BLE001 — bir tur hatası döngüyü sürdürür
            log.warning("Dikte turu hatası: %s", exc)
            overlay_q.put("error")
        finally:
            controls.end_dictation()
