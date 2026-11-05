#!/usr/bin/env python3
"""Write the toolbar icons: the app logo's cube (half triangle mesh, half
solid), redrawn simply with bold lines so it reads at 16-32 px on Fusion's
light toolbar. No tile: the cube sits on a transparent background.

    ../.venv/bin/python tools/make_icons.py      (needs Pillow; the add-in does not)

Writes mssaddin/resources/outline/{16x16,32x32,64x64}.png in deep blue and
the "-dark" variants (bright cyan) Fusion uses for the dark theme, plus
store/icon120.png (Marketplace listing) and store/icon32.png (bundle)."""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), 'mssaddin', 'resources', 'outline')
STORE = os.path.join(os.path.dirname(HERE), 'store')


def draw(size, dark):
    """Drawn at 8x on a 100-unit grid, then scaled down so it stays smooth."""
    S = size * 8
    k = S / 100.0
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def P(x, y):                             # grow the art to fill the box
        return ((50 + (x - 50) * 1.07) * k, (50 + (y - 50) * 1.07) * k)
    # the hexagon outline and the near corner (C), as in the logo
    T, UL, LL, B, LR, UR, C = P(50, 6), P(9, 26), P(9, 74), P(50, 94), P(91, 74), P(91, 26), P(50, 46)
    n1, n2 = P(30, 36), P(28, 60)            # two mesh nodes on the left face
    line = (80, 220, 255, 255) if dark else (0, 110, 200, 255)
    top = (120, 200, 240, 255) if dark else (110, 190, 235, 255)
    side = (30, 120, 190, 255) if dark else (20, 95, 170, 255)
    mesh = (20, 40, 60, 200) if dark else (220, 238, 250, 255)
    d.polygon([T, UR, C], fill=top)
    d.polygon([C, UR, LR, B], fill=side)
    d.polygon([T, UL, LL, B, C], fill=mesh)
    w = max(2, round(S * 0.058))
    for a, b in ((UL, n1), (n1, T), (n1, C), (n1, n2), (P(9, 50), n2), (n2, B), (n2, C), (LL, n2)):
        d.line([a, b], fill=line, width=round(w * 0.55))
    for a, b in ((T, UR), (UR, LR), (LR, B), (B, LL), (LL, UL), (UL, T), (UR, C), (C, UL), (C, B)):
        d.line([a, b], fill=line, width=w)
    r = w * 0.8
    for p in (T, UL, LL, B, C, n1, n2):
        d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=line)
    return img.resize((size, size), Image.LANCZOS)


def store_icon(size):
    """The Marketplace listing icon (120 px) and the PackageContents icon
    (32 px). Autodesk wants it "legible and bordered or framed", so the
    cube sits on a white rounded tile with a thin blue frame."""
    S = size * 8
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad, r, w = S * 0.03, S * 0.16, max(8, round(S * 0.035))
    d.rounded_rectangle([pad, pad, S - pad, S - pad], radius=r,
                        fill=(255, 255, 255, 255), outline=(0, 110, 200, 255), width=w)
    cube = draw(size, False).resize((round(S * 0.72),) * 2, Image.LANCZOS)
    off = round((S - cube.width) / 2)
    img.alpha_composite(cube, (off, off))
    return img.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(STORE, exist_ok=True)
    for size in (120, 32):
        store_icon(size).save(os.path.join(STORE, 'icon{}.png'.format(size)))
    os.makedirs(OUT, exist_ok=True)
    for size in (16, 32, 64):
        draw(size, False).save(os.path.join(OUT, '{0}x{0}.png'.format(size)))
        draw(size, True).save(os.path.join(OUT, '{0}x{0}-dark.png'.format(size)))
    # Retina: double-size art for the 16 and 32 point slots (both dark spellings)
    for size in (16, 32):
        draw(size * 2, False).save(os.path.join(OUT, '{0}x{0}@2x.png'.format(size)))
        for name in ('{0}x{0}-dark@2x.png', '{0}x{0}@2x-dark.png'):
            draw(size * 2, True).save(os.path.join(OUT, name.format(size)))
    print('icons written to ' + OUT)


if __name__ == '__main__':
    main()
