#!/usr/bin/env python3
"""Generate the macOS app icon (.icns) — an eighth note on a rounded black tile.

Usage:  python make_icon.py [dark|light]
Writes AppIcon.icns plus a 1024px preview PNG next to this script.
"""
import math
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent
S    = 1024          # final canvas
SS   = 4             # supersampling factor for smooth edges
W    = S * SS

# macOS Big Sur icon grid: the art occupies 824/1024 of the canvas.
TILE   = int(824 * SS)
INSET  = (W - TILE) // 2

VARIANTS = {
    # note colour, tile gradient (top, bottom)
    "dark":  ((0x3a, 0x3a, 0x42), (0x1a, 0x1a, 0x1f), (0x05, 0x05, 0x07)),
    "light": ((0xf2, 0xf2, 0xf5), (0x1a, 0x1a, 0x1f), (0x05, 0x05, 0x07)),
}


def vertical_gradient(size, top, bottom):
    strip = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / (size - 1)
        strip.putpixel((0, y), tuple(
            round(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return strip.resize((size, size), Image.BICUBIC)


def quad_bezier(p0, p1, p2, steps=80):
    pts = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        pts.append((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                    u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))
    return pts


def superellipse_mask(size, inset, exponent=5.0, steps=1200):
    """macOS-style squircle: a superellipse, not a plain rounded rectangle."""
    a = b = (size - 2 * inset) / 2
    cx = cy = size / 2
    pts = []
    for i in range(steps):
        t = 2 * math.pi * i / steps
        ct, st = math.cos(t), math.sin(t)
        pts.append((
            cx + a * math.copysign(abs(ct) ** (2 / exponent), ct),
            cy + b * math.copysign(abs(st) ** (2 / exponent), st),
        ))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).polygon(pts, fill=255)
    return mask


def note_mask():
    """Alpha mask of an eighth note, cropped tight to the glyph."""
    D = 1600                       # scratch design space
    mask = Image.new("L", (D, D), 0)
    d = ImageDraw.Draw(mask)

    stem_x, stem_w = 820, 92
    stem_top, stem_bot = 300, 1130

    # Stem, square at the top so the flag merges into it without a notch.
    d.rectangle([stem_x, stem_top, stem_x + stem_w, stem_bot], fill=255)

    # Flag: sweeps out of the stem and curls back, meeting it lower down.
    # Both curves start/end inside the stem so the union stays seamless.
    outer = quad_bezier((stem_x, stem_top), (1240, 400), (1140, 720))
    inner = quad_bezier((1140, 720), (1160, 505), (stem_x, 590))
    d.polygon(outer + inner, fill=255)

    # Note head: an ellipse tilted the way engraved noteheads are.
    hw, hh = 470, 350
    head = Image.new("L", (hw, hh), 0)
    ImageDraw.Draw(head).ellipse([0, 0, hw - 1, hh - 1], fill=255)
    head = head.rotate(20, resample=Image.BICUBIC, expand=True)
    # Sit the stem's end near the head's centre line so it stays tucked
    # inside the ellipse instead of poking out underneath it.
    mask.paste(head, (int(stem_x + stem_w - head.width * 0.90),
                      int(stem_bot - head.height * 0.52)), head)

    return mask.crop(mask.getbbox())


def build(variant):
    note_rgb, grad_top, grad_bot = VARIANTS[variant]

    icon = Image.new("RGBA", (W, W), (0, 0, 0, 0))

    tile = vertical_gradient(W, grad_top, grad_bot).convert("RGBA")
    icon.paste(tile, (0, 0), superellipse_mask(W, INSET))

    # Scale the glyph to a fixed share of the tile, then centre it.
    glyph = note_mask()
    target_h = int(TILE * 0.60)
    target_w = round(glyph.width * target_h / glyph.height)
    glyph = glyph.resize((target_w, target_h), Image.LANCZOS)

    placed = Image.new("L", (W, W), 0)
    placed.paste(glyph, ((W - target_w) // 2, (W - target_h) // 2))
    note = Image.new("RGBA", (W, W), note_rgb + (255,))
    icon.paste(note, (0, 0), placed)

    return icon.resize((S, S), Image.LANCZOS)


def main():
    variant = sys.argv[1] if len(sys.argv) > 1 else "dark"
    if variant not in VARIANTS:
        sys.exit(f"unknown variant {variant!r}; pick one of {list(VARIANTS)}")

    icon = build(variant)
    preview = HERE / f"icon_{variant}.png"
    icon.save(preview)

    iconset = HERE / "AppIcon.iconset"
    iconset.mkdir(exist_ok=True)
    for base in (16, 32, 128, 256, 512):
        icon.resize((base, base), Image.LANCZOS).save(
            iconset / f"icon_{base}x{base}.png")
        icon.resize((base * 2, base * 2), Image.LANCZOS).save(
            iconset / f"icon_{base}x{base}@2x.png")

    icns = HERE / "AppIcon.icns"
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)],
                   check=True)
    print(f"wrote {icns.name} and {preview.name} ({variant})")


if __name__ == "__main__":
    main()
