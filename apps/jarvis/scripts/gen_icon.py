"""Menubar template icon — şimşek sembolü, 22x22 + 44x44 PNG."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import Quartz

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")


def draw_bolt(ctx, size: float) -> None:
    """Şimşek sembolü. Referans grid: 22x22, y-up."""
    s = size / 22.0
    black = Quartz.CGColorCreateGenericGray(0.0, 1.0)
    Quartz.CGContextSetFillColorWithColor(ctx, black)

    # Şimşek polygon (y-up: origin sol-alt)
    # SVG'deki (x, y-down) → CG (x, size - y-down)
    pts = [
        (11*s, (22-2)*s),   # üst
        (4*s,  (22-24)*s),  # orta-sol  ← dikkat: 24 > 22, clip olur
        (9*s,  (22-24)*s),  # iç-sol
        (7*s,  (22-42)*s),  # alt-uç
        (16*s, (22-18)*s),  # sağ-dış
        (11*s, (22-18)*s),  # iç-sağ
    ]
    # y koordinatlarını [0, size] arasına sıkıştır
    pts_cg = [(x, max(0, min(size, y))) for x, y in pts]

    path = Quartz.CGPathCreateMutable()
    Quartz.CGPathMoveToPoint(path, None, pts_cg[0][0], pts_cg[0][1])
    for x, y in pts_cg[1:]:
        Quartz.CGPathAddLineToPoint(path, None, x, y)
    Quartz.CGPathCloseSubpath(path)
    Quartz.CGContextAddPath(ctx, path)
    Quartz.CGContextFillPath(ctx)


def draw_bolt_clean(ctx, size: float) -> None:
    """Şimşek — grid 22x22, koordinatlar doğrudan CG (y-up, origin sol-alt)."""
    s = size / 22.0
    black = Quartz.CGColorCreateGenericGray(0.0, 1.0)
    Quartz.CGContextSetFillColorWithColor(ctx, black)

    # Noktalar y-up koordinat sisteminde (sol-alt = 0,0):
    # SVG y-down → CG y-up dönüşümü: cg_y = size - svg_y
    # SVG noktaları (22x22 grid'de):
    #   üst:     (11, 1)
    #   sol:     (4,  13)
    #   iç-sol:  (9,  13)
    #   alt-uç:  (7,  21)
    #   sağ-dış: (18, 9)
    #   iç-sağ:  (13, 9)
    def p(sx, sy):
        return (sx * s, (22 - sy) * s)

    pts = [p(11, 1), p(4, 13), p(9, 13), p(7, 21), p(18, 9), p(13, 9)]

    path = Quartz.CGPathCreateMutable()
    Quartz.CGPathMoveToPoint(path, None, *pts[0])
    for pt in pts[1:]:
        Quartz.CGPathAddLineToPoint(path, None, *pt)
    Quartz.CGPathCloseSubpath(path)
    Quartz.CGContextAddPath(ctx, path)
    Quartz.CGContextFillPath(ctx)


def save_png(size: int, path: str) -> None:
    cs = Quartz.CGColorSpaceCreateDeviceRGB()
    ctx = Quartz.CGBitmapContextCreate(
        None, size, size, 8, 0, cs,
        Quartz.kCGImageAlphaPremultipliedLast,
    )
    Quartz.CGContextClearRect(ctx, Quartz.CGRectMake(0, 0, size, size))
    draw_bolt_clean(ctx, size)
    img = Quartz.CGBitmapContextCreateImage(ctx)
    url = Quartz.CFURLCreateFromFileSystemRepresentation(None, path.encode(), len(path), False)
    dest = Quartz.CGImageDestinationCreateWithURL(url, "public.png", 1, None)
    Quartz.CGImageDestinationAddImage(dest, img, None)
    Quartz.CGImageDestinationFinalize(dest)
    print(f"  ✓ {path}  ({size}x{size})")


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    save_png(22,  os.path.join(OUT_DIR, "icon_menubar.png"))
    save_png(44,  os.path.join(OUT_DIR, "icon_menubar@2x.png"))
    save_png(128, os.path.join(OUT_DIR, "icon_mark_128.png"))
    print("Tamamlandı.")
