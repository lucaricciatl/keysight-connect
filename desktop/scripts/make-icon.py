#!/usr/bin/env python3
"""Generate build/icon.png (1024x1024) for the macOS bundle.

electron-builder turns a 1024px PNG into .icns automatically.  The mark is an
I-V curve on the app's dark panel colour, so the Dock icon matches the UI.

Rendering notes, so a re-run stays clean:
  * the squircle is a real superellipse mask (not a rounded rectangle)
  * the curve is painted as overlapping discs, which gives a stroke of exactly
    constant width with round caps — no polyline joint artefacts
  * everything is drawn at 3x and Lanczos-downsampled

    python3 scripts/make-icon.py
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "build" / "icon.png"

SIZE = 1024
SS = 3
W = SIZE * SS

INSET = 0.098          # macOS leaves ~10% padding around the squircle
SQUIRCLE_N = 5.0       # superellipse exponent; 5 reads as an Apple squircle

TEAL = (95, 208, 192)
PANEL_TOP = (28, 43, 58)
PANEL_BOTTOM = (6, 10, 15)


def gradient(size: int) -> Image.Image:
    t = np.linspace(0.0, 1.0, size)[:, None]
    top = np.array(PANEL_TOP, dtype=float)
    bottom = np.array(PANEL_BOTTOM, dtype=float)
    rows = top[None, :] + (bottom - top)[None, :] * t
    arr = np.repeat(rows[:, None, :], size, axis=1).astype(np.uint8)
    return Image.fromarray(arr)


def squircle_mask(size: int) -> Image.Image:
    """Superellipse |x|^n + |y|^n <= 1 — smooth continuous curvature."""
    pad = INSET * size
    half = (size / 2) - pad
    c = size / 2.0
    y, x = np.mgrid[0:size, 0:size]
    nx = np.abs((x + 0.5 - c) / half)
    ny = np.abs((y + 0.5 - c) / half)
    inside = (nx ** SQUIRCLE_N + ny ** SQUIRCLE_N) <= 1.0
    # a couple of pixels of feathering so the edge is not hard-aliased
    d = (nx ** SQUIRCLE_N + ny ** SQUIRCLE_N) ** (1.0 / SQUIRCLE_N)
    alpha = np.clip((1.0 - d) * half * 1.6, 0.0, 1.0)
    alpha = np.where(inside, np.maximum(alpha, 0.0), 0.0)
    return Image.fromarray((alpha * 255).astype(np.uint8))


def brush(draw: ImageDraw.ImageDraw, points, radius: float, fill) -> None:
    """Paint a constant-width, round-capped stroke as overlapping discs."""
    for x, y in points:
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=fill)


def line(draw: ImageDraw.ImageDraw, p0, p1, radius: float, fill) -> None:
    """Straight stroke with the same round-capped disc brush."""
    (x0, y0), (x1, y1) = p0, p1
    dist = math.hypot(x1 - x0, y1 - y0)
    steps = max(2, int(dist / max(1.0, radius * 0.4)))
    pts = [(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
           for i in range(steps + 1)]
    brush(draw, pts, radius, fill)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)

    panel = gradient(W).convert("RGBA")
    layer = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    layer.paste(panel, (0, 0), squircle_mask(W))

    # plot box, geometrically centred in the panel
    left, right = 0.5 * W - 0.305 * W, 0.5 * W + 0.305 * W
    bottom, top = 0.5 * W + 0.275 * W, 0.5 * W - 0.275 * W

    # --- glow under everything -------------------------------------------
    glow = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    steps = 420
    k = 3.2

    def curve_point(t: float) -> tuple[float, float]:
        x = left + (right - left) * t
        e = (math.exp(k * t) - 1.0) / (math.exp(k) - 1.0)
        return x, bottom - (bottom - top) * e

    curve = [curve_point(i / steps) for i in range(steps + 1)]
    brush(gdraw, curve, 19 * SS, TEAL + (70,))
    layer = Image.alpha_composite(
        layer, glow.filter(ImageFilter.GaussianBlur(15 * SS))
    )

    draw = ImageDraw.Draw(layer)

    # --- axes (the curve starts exactly at the origin) ---------------------
    axis = TEAL + (58,)
    line(draw, (left - 30 * SS, bottom), (right + 30 * SS, bottom), 4 * SS, axis)
    line(draw, (left, bottom), (left, top - 24 * SS), 4 * SS, axis)

    # --- curve ------------------------------------------------------------
    brush(draw, curve, 12 * SS, TEAL + (255,))

    # --- measurement nodes -------------------------------------------------
    for t in (0.30, 0.70):
        cx, cy = curve_point(t)
        r_out, r_in = 28 * SS, 15 * SS
        draw.ellipse([cx - r_out, cy - r_out, cx + r_out, cy + r_out], fill=TEAL + (255,))
        draw.ellipse([cx - r_in, cy - r_in, cx + r_in, cy + r_in], fill=(7, 12, 17, 255))

    # --- rim: keeps the dark panel readable on a dark wallpaper ------------
    # Drawn just inside the mask boundary so the squircle's feathered edge
    # never clips the stroke (clipping is what makes it look jagged).
    c = W / 2.0
    half = (W / 2) - INSET * W - 9
    rim = []
    for i in range(1440):
        a = 2 * math.pi * i / 1440
        ca, sa = math.cos(a), math.sin(a)
        rim.append((
            c + half * math.copysign(abs(ca) ** (2.0 / SQUIRCLE_N), ca),
            c + half * math.copysign(abs(sa) ** (2.0 / SQUIRCLE_N), sa),
        ))
    brush(draw, rim, 4.5 * SS, (255, 255, 255, 34))

    out = layer.resize((SIZE, SIZE), Image.LANCZOS)
    out.save(OUT)
    print(f"wrote {OUT} ({out.size[0]}x{out.size[1]}, {len(curve)} curve points)")


if __name__ == "__main__":
    main()
