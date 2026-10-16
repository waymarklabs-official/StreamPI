#!/usr/bin/env python3
"""StreamPI tile maker.

Builds matching art for every service listed by `streampi-app --list`:
  <id>.png         1280x720 landscape tile (logo or name on brand color)
  <id>-fanart.jpg  1920x1080 dark gradient background in the brand color

Logos are optional: drop a transparent PNG at ~/tile-src/<id>.png and re-run.
Without one, the tile shows the service name in clean type instead.

Usage:  python3 make_tiles.py            (writes into the add-on's resources/)
        python3 make_tiles.py --out DIR  (writes somewhere else to preview)
"""

import argparse
import os
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

LAUNCHER = "/usr/local/bin/streampi-app"
ADDON_RES = os.path.expanduser(
    "~/.kodi/addons/plugin.video.streampi.apps/resources")
LOGO_DIR = os.path.expanduser("~/tile-src")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

TILE = (1280, 720)
FANART = (1920, 1080)

# id: (tile background, accent used for text and fanart glow)
THEME = {
    "netflix":   ("#141414", "#E50914"),
    "prime":     ("#0F171E", "#00A8E1"),
    "paramount": ("#0064FF", "#FFFFFF"),
    "espn":      ("#CC0000", "#FFFFFF"),
    "spotify":   ("#121212", "#1DB954"),
    "youtubetv": ("#0F0F0F", "#FF0000"),
    "youtube":   ("#FF0000", "#FFFFFF"),
    "terminal":  ("#0C0C0C", "#33FF66"),
    "wifilogin": ("#0B1E33", "#4FC3F7"),
}
DEFAULT_THEME = ("#1C1C24", "#8A8AFF")   # any service you add later


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def services():
    out = subprocess.run([LAUNCHER, "--list"], capture_output=True,
                         text=True, timeout=15).stdout
    rows = []
    for line in out.splitlines()[1:]:
        if not line.strip():
            continue
        svc, name = line[:12].strip(), line[13:27].strip()
        if svc:
            rows.append((svc, name or svc.title()))
    return rows


def font(size):
    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        return ImageFont.load_default(size=size)


def make_tile(svc, name, bg, accent):
    img = Image.new("RGB", TILE, hex_rgb(bg))
    logo_path = os.path.join(LOGO_DIR, svc + ".png")

    if os.path.exists(logo_path):
        logo = Image.open(logo_path).convert("RGBA")
        max_w, max_h = int(TILE[0] * 0.6), int(TILE[1] * 0.45)
        scale = min(max_w / logo.width, max_h / logo.height)
        logo = logo.resize((int(logo.width * scale), int(logo.height * scale)),
                           Image.LANCZOS)
        pos = ((TILE[0] - logo.width) // 2, (TILE[1] - logo.height) // 2)
        img.paste(logo, pos, logo)
    else:
        draw = ImageDraw.Draw(img)
        size = 150
        while size > 40:
            f = font(size)
            box = draw.textbbox((0, 0), name, font=f)
            if box[2] - box[0] <= TILE[0] * 0.8:
                break
            size -= 10
        w, h = box[2] - box[0], box[3] - box[1]
        draw.text(((TILE[0] - w) / 2 - box[0], (TILE[1] - h) / 2 - box[1]),
                  name, font=f, fill=hex_rgb(accent))
    return img


def make_fanart(accent):
    """Brand-colored glow fading to near black, top-right to bottom-left."""
    small = Image.new("RGB", (192, 108))
    ar, ag, ab = hex_rgb(accent)
    base = (10, 10, 14)
    px = small.load()
    for y in range(108):
        for x in range(192):
            # distance from the top-right corner, 0..1
            d = min(1.0, (((191 - x) / 192) ** 2 + (y / 108) ** 2) ** 0.5)
            t = (1 - d) ** 2 * 0.55
            px[x, y] = (int(base[0] + (ar - base[0]) * t),
                        int(base[1] + (ag - base[1]) * t),
                        int(base[2] + (ab - base[2]) * t))
    return small.resize(FANART, Image.BICUBIC)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=ADDON_RES)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    rows = services()
    if not rows:
        sys.exit(f"No services from {LAUNCHER} --list")

    for svc, name in rows:
        bg, accent = THEME.get(svc, DEFAULT_THEME)
        make_tile(svc, name, bg, accent).save(
            os.path.join(args.out, svc + ".png"))
        make_fanart(accent).save(
            os.path.join(args.out, svc + "-fanart.jpg"), quality=90)
        has_logo = os.path.exists(os.path.join(LOGO_DIR, svc + ".png"))
        print(f"{svc:<12} {'logo' if has_logo else 'text'} tile + fanart")

    print(f"\nWrote art to {args.out}")


if __name__ == "__main__":
    main()
