#!/usr/bin/env python3
"""Generate mlrVp app icon from the parallelogram logo design.

The mlrV brand is the parallelogram shape on a dark background (#292929).
This script generates two icon variants:

  Python-style (default, active):
    icon.png / icon.ico  — diagonal blue/yellow split matching the Python logo

  Original red (backup):
    icon_red.png / icon_red.ico  — magenta (#c60059) solid fill
"""

from PIL import Image, ImageDraw
import os

# Original red
LOGO_MAGENTA = (198, 0, 89)        # #c60059

# Python logo colours
PY_BLUE      = (75, 139, 190)      # #4B8BBE
PY_YELLOW    = (255, 212, 59)      # #FFD43B

BG_DARK      = (41, 41, 41)        # #292929


def _parallelogram_vertices(size):
    """Return the four parallelogram vertices (tl, tr, br, bl) for a given icon size."""
    canvas_l = size * 0.14
    canvas_r = size * 0.86
    canvas_t = size * 0.28
    canvas_b = size * 0.72
    cw = canvas_r - canvas_l
    ch = canvas_b - canvas_t
    tl = (canvas_l + 18/55 * cw, canvas_t + 0/23 * ch)
    tr = (canvas_l + 53/55 * cw, canvas_t + 0/23 * ch)
    br = (canvas_l + 43/55 * cw, canvas_t + 23/23 * ch)
    bl = (canvas_l +  8/55 * cw, canvas_t + 23/23 * ch)
    return tl, tr, br, bl


def _make_base(size):
    """Create an image with just the dark rounded-rect background."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = int(size * 0.06)
    radius = int(size * 0.18)
    draw.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=radius,
        fill=BG_DARK,
    )
    return img, draw


def make_icon(size=256):
    """Python-logo long-way split (TR→BL diagonal) — blue on top / yellow on bottom."""
    img, draw = _make_base(size)
    tl, tr, br, bl = _parallelogram_vertices(size)

    # Diagonal from TR corner down to BL corner splits the shape into:
    #   upper half (blue): tl, tr, bl
    #   lower half (yellow): tr, br, bl
    draw.polygon([tl, tr, bl], fill=PY_BLUE)
    draw.polygon([tr, br, bl], fill=PY_YELLOW)

    return img


def make_icon_diagonal(size=256):
    """Python-logo short-way split (TL→BR diagonal) — blue upper-left / yellow lower-right."""
    img, draw = _make_base(size)
    tl, tr, br, bl = _parallelogram_vertices(size)

    draw.polygon([tl, tr, br], fill=PY_BLUE)
    draw.polygon([tl, br, bl], fill=PY_YELLOW)

    return img


def make_icon_red(size=256):
    """Original solid magenta/red icon (backup)."""
    img, draw = _make_base(size)
    tl, tr, br, bl = _parallelogram_vertices(size)
    draw.polygon([tl, tr, br, bl], fill=LOGO_MAGENTA)
    return img


def _save_icon_set(make_fn, out_dir, png_name, ico_name, icns_name):
    sizes = [16, 24, 32, 48, 64, 128, 256]
    icons = [make_fn(s) for s in sizes]

    png_path = os.path.join(out_dir, png_name)
    icons[-1].save(png_path, "PNG")
    print(f"Created {png_path}")

    ico_path = os.path.join(out_dir, ico_name)
    icons[0].save(ico_path, format="ICO", sizes=[(s, s) for s in sizes],
                  append_images=icons[1:])
    print(f"Created {ico_path}")

    # macOS .icns — Pillow supports sizes 16, 32, 48, 128, 256, 512, 1024
    icns_sizes = [16, 32, 48, 128, 256, 512]
    icns_imgs = [make_fn(s) for s in icns_sizes]
    icns_path = os.path.join(out_dir, icns_name)
    icns_imgs[0].save(icns_path, format="ICNS", append_images=icns_imgs[1:])
    print(f"Created {icns_path}")


def main():
    out_dir = os.path.join(os.path.dirname(__file__), "mlrvp", "resources")
    os.makedirs(out_dir, exist_ok=True)

    # Active icon — long-way split (TR→BL), blue top / yellow bottom
    _save_icon_set(make_icon, out_dir, "icon.png", "icon.ico", "icon.icns")

    # Variant — short-way split (TL→BR), blue upper-left / yellow lower-right
    _save_icon_set(make_icon_diagonal, out_dir, "icon_diagonal.png", "icon_diagonal.ico", "icon_diagonal.icns")

    # Backup — original solid red/magenta
    _save_icon_set(make_icon_red, out_dir, "icon_red.png", "icon_red.ico", "icon_red.icns")


if __name__ == "__main__":
    main()
