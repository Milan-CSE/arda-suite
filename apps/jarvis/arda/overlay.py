"""Yüzen amblem (Wispr Flow tarzı) — macOS NSPanel overlay. YALNIZ UI.

Kenarsız + non-activating panel → ekranda durur, tıklanınca ÖNDEKİ uygulamanın odağını
ÇALMAZ (yapıştırma sağlam kalır). pyobjc lazy import: kurulu değilse app çökmez, amblem
sessizce devre dışı kalır. Tüm AppKit çağrıları ana thread'de yapılmalı (çağıran sorumlu).
"""
from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger(__name__)

# Durum → RGB (0–1). Saf; testte AppKit gerekmez.
_COLORS = {
    "idle": (0.5, 0.5, 0.5),
    "listening": (0.9, 0.2, 0.2),
    "transcribing": (0.2, 0.5, 0.9),
    "pasting": (0.65, 0.4, 0.9),
    "done": (0.2, 0.8, 0.3),
    "error": (0.85, 0.6, 0.15),
}


def state_color(state: str) -> tuple[float, float, float]:
    """Dikte durumunu amblem rengine eşle (bilinmeyen → idle gri)."""
    return _COLORS.get(state, _COLORS["idle"])


def is_available() -> bool:
    """pyobjc/AppKit kurulu mu (amblem çizilebilir mi)?"""
    try:
        import AppKit  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


class OverlayController:
    """Yüzen amblemi yönetir. pyobjc yoksa tüm metodlar sessiz no-op."""

    def __init__(self, on_toggle: Callable[[], None]) -> None:
        self._on_toggle = on_toggle
        self._panel = None
        self._view = None

    def show(self) -> None:
        """Amblemi kur ve göster (ANA THREAD). pyobjc yoksa/başarısızsa sessiz geç."""
        if self._panel is not None:
            try:
                self._panel.orderFrontRegardless()
            except Exception as exc:  # noqa: BLE001
                log.debug("Amblem öne getirilemedi: %s", exc)
            return
        try:
            self._build()
        except Exception as exc:  # noqa: BLE001 — amblem opsiyonel, app çökmesin
            log.warning("Amblem kurulamadı (devre dışı): %s", exc)

    def hide(self) -> None:
        if self._panel is not None:
            try:
                self._panel.orderOut_(None)
            except Exception as exc:  # noqa: BLE001
                log.debug("Amblem gizlenemedi: %s", exc)

    def set_state(self, state: str) -> None:
        """Amblem rengini güncelle (ANA THREAD). Panel yoksa no-op."""
        if self._view is None:
            return
        try:
            self._view.setColorRGB_(state_color(state))
        except Exception as exc:  # noqa: BLE001
            log.debug("Amblem durumu güncellenemedi: %s", exc)

    def _build(self) -> None:
        """NSPanel + tıklanabilir/sürüklenebilir yuvarlak görünüm kur (lazy AppKit)."""
        import AppKit
        import objc

        W, H = 96.0, 34.0
        screen = AppKit.NSScreen.mainScreen().frame()
        x = (screen.size.width - W) / 2.0
        y = 60.0  # alt-orta
        rect = AppKit.NSMakeRect(x, y, W, H)
        style = AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel
        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, AppKit.NSBackingStoreBuffered, False
        )
        panel.setLevel_(AppKit.NSStatusWindowLevel)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
        )

        on_toggle = self._on_toggle

        class _PillView(AppKit.NSView):
            def initWithFrame_(self, frame):
                self = objc.super(_PillView, self).initWithFrame_(frame)
                if self is None:
                    return None
                self._rgb = _COLORS["idle"]
                self._drag = False
                return self

            def acceptsFirstMouse_(self, _event):
                # Non-activating panel'de ilk tık yutulmasın.
                return True

            def setColorRGB_(self, rgb):
                self._rgb = (float(rgb[0]), float(rgb[1]), float(rgb[2]))
                self.setNeedsDisplay_(True)

            def drawRect_(self, _rect):
                import Quartz as CG
                b = self.bounds()
                W, H = b.size.width, b.size.height
                r, g, bb = self._rgb

                # Pill arka planı
                AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.10, 0.88).set()
                pill = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    b, H / 2.0, H / 2.0
                )
                pill.fill()

                # State ring — renkli dış çerçeve
                AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, bb, 0.9).set()
                inset = AppKit.NSInsetRect(b, 1.5, 1.5)
                ring = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    inset, (H - 3) / 2.0, (H - 3) / 2.0
                )
                ring.setLineWidth_(1.5)
                ring.stroke()

                # Şimşek sembolü — beyaz, pill içinde ortalı
                ctx = AppKit.NSGraphicsContext.currentContext().CGContext()
                mark_size = H * 0.65
                mx = (H - mark_size) / 2.0
                my = (H - mark_size) / 2.0
                CG.CGContextSaveGState(ctx)
                CG.CGContextTranslateCTM(ctx, mx, my)
                s = mark_size / 22.0

                white = CG.CGColorCreateGenericGray(1.0, 0.95)
                CG.CGContextSetFillColorWithColor(ctx, white)

                # Şimşek polygon: (x, y) y-up koordinatında
                def p(sx, sy):
                    return (sx * s, (22 - sy) * s)

                pts = [p(11, 1), p(4, 13), p(9, 13), p(7, 21), p(18, 9), p(13, 9)]
                path = CG.CGPathCreateMutable()
                CG.CGPathMoveToPoint(path, None, *pts[0])
                for pt in pts[1:]:
                    CG.CGPathAddLineToPoint(path, None, *pt)
                CG.CGPathCloseSubpath(path)
                CG.CGContextAddPath(ctx, path)
                CG.CGContextFillPath(ctx)

                CG.CGContextRestoreGState(ctx)

                # Durum etiketi — şimşeğin sağında
                state_labels = {
                    (0.5, 0.5, 0.5): "",
                    (0.9, 0.2, 0.2): "Durdur",   # kayıt sürüyor; amblem tıklaması kaydı durdurur
                    (0.2, 0.5, 0.9): "yazıyor",
                    (0.65, 0.4, 0.9): "yapıştır",
                    (0.2, 0.8, 0.3): "hazır",
                    (0.85, 0.6, 0.15): "hata",
                }
                label = state_labels.get((r, g, bb), "")
                if label:
                    attrs = {
                        AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(9),
                        AppKit.NSForegroundColorAttributeName: AppKit.NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.75),
                    }
                    AppKit.NSString.stringWithString_(label).drawAtPoint_withAttributes_(
                        AppKit.NSMakePoint(H + 2, (H - 11) / 2.0), attrs
                    )

            def mouseDown_(self, _event):
                self._drag = False

            def mouseDragged_(self, event):
                self._drag = True
                win = self.window()
                origin = win.frame().origin
                origin.x += event.deltaX()
                origin.y -= event.deltaY()
                win.setFrameOrigin_(origin)

            def mouseUp_(self, _event):
                if not self._drag:
                    on_toggle()

        view = _PillView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, W, H))
        panel.setContentView_(view)
        panel.orderFrontRegardless()
        self._panel = panel
        self._view = view
