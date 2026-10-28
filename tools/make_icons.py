"""Regenerate every icon from icons/*.svg (needs Pillow):

    python tools/make_icons.py

cpp/tip/icon_profile.ico (中英混打, the input method's own; installer/app.ico is the same) and
icon.ico (its inverse, the mode button) from icons/中英混打.svg, icon_en.ico (全英輸入, the mode
button in English mode) from icons/全英輸入.svg, and settings/ui/logo.svg a copy of icons/中英混打.svg.
"""
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# The 中英混打 icon drawn pixel by pixel where the vector one is too small to stay sharp:
# its strokes and the gaps between them are about a pixel each, so any edge off the pixel
# grid turns grey. Same strokes as icons/中英混打.svg, one pixel wide.
MIXED_ICON_PIXELS = {
    16: [
        "................",
        "................",
        "................",
        "....#...........",
        "....#...........",
        ".########.#.....",
        ".#..#..#.##.....",
        ".#..#.#.#..#....",
        ".######.#..#....",
        "....#..#.##.#...",
        "....#.#.#####...",
        "....#.#.#....#..",
        ".....#.#.####.#.",
        "................",
        "................",
        "................",
    ],
    20: [
        "....................",
        "....................",
        "....................",
        "....................",
        ".....#..............",
        ".....#..............",
        ".....#..............",
        "..#########..##.....",
        "..#..#...#..#.#.....",
        "..#..#..#..#...#....",
        "..#######..#.#.#....",
        ".....#....#.##..#...",
        ".....#...#.##....#..",
        ".....#..#.########..",
        ".....#..#.#.......#.",
        ".......#.#.######.#.",
        "....................",
        "....................",
        "....................",
        "....................",
    ],
}

# The 全英輸入 icon by hand at the same sizes, for the same reason.
ENGLISH_ICON_PIXELS = {
    16: [
        "................",
        "................",
        "................",
        ".......##.......",
        "......####......",
        ".....##..##.....",
        ".....##..##.....",
        "....##....##....",
        "....##....##....",
        "...##.#######...",
        "..##........##..",
        "..##.######.##..",
        ".##..........##.",
        "................",
        "................",
        "................",
    ],
    20: [
        "....................",
        "....................",
        "....................",
        "....................",
        ".........##.........",
        "........####........",
        ".......##..##.......",
        ".......##..##.......",
        "......##....##......",
        "......##....##......",
        ".....##.#######.....",
        ".....##.#######.....",
        "....##........##....",
        "....##.######.##....",
        "...##.########.##...",
        "..##............##..",
        "....................",
        "....................",
        "....................",
        "....................",
    ],
}


ICON_SIZES = (256, 64, 48, 40, 32, 24, 20, 16)


def icon_frames(cover, view, bounds, paper, ink, pixels={}):
    """One picture per icon size, from cover(size, k, dx, dy, ss): how much of each pixel the ink
    covers (0-255) with the view x view artwork scaled by k and moved by dx, dy; bounds is where
    the ink sits in the artwork. Sizes in pixels are drawn by hand ("#" is ink) instead."""
    from PIL import Image

    def grey(c):
        """The share of ink sitting in half-covered pixels: what makes a small icon look blurred."""
        h = c.histogram()
        return sum(n * min(v, 255 - v) for v, n in enumerate(h)) / max(sum(n * v for v, n in enumerate(h)), 1)

    def fitted(size):
        """At 32px and below the strokes are a pixel or two wide: try sizes a little up or down
        and shifts under a pixel, keep the one whose edges land closest to whole pixels, then
        push what is still half covered towards ink or paper."""
        best = None
        for step in range(13):
            k = size / view * (0.96 + step * 0.015)
            for i in range(8):
                for j in range(8):
                    dx = size / 2 - view / 2 * k + (i - 4) / 8
                    dy = size / 2 - view / 2 * k + (j - 4) / 8
                    x0, y0, x1, y1 = (v * k + d for v, d in zip(bounds, (dx, dy, dx, dy)))
                    if min(x0, y0) < 0.6 or max(x1, y1) > size - 0.6:
                        continue
                    c = cover(size, k, dx, dy, 8)
                    if best is None or grey(c) < best[0]:
                        best = (grey(c), k, dx, dy)
        lo, hi = 0.25 * 255, 0.65 * 255
        return cover(size, *best[1:], 16).point(lambda v: round(min(max((v - lo) / (hi - lo), 0), 1) * 255))

    def draw(size):
        if size in pixels:
            c = Image.new("L", (size, size))
            c.putdata([255 if ch == "#" else 0 for row in pixels[size] for ch in row])
        elif size <= 32:
            c = fitted(size)
        else:
            c = cover(size, size / view, 0, 0, 16)
        return Image.composite(Image.new("RGB", c.size, ink), Image.new("RGB", c.size, paper), c)

    return [draw(n) for n in ICON_SIZES]


def save_ico(frames, path):
    frames[0].save(path, sizes=[f.size for f in frames], append_images=frames[1:])


def svg_frames(name, pixels={}):
    """Draw icons/<name>.svg (its rect and polygon shapes, in order) at each icon size, the sizes
    in pixels drawn by hand instead."""
    import re
    from PIL import Image, ImageDraw
    svg = open(os.path.join(ROOT, "icons", name + ".svg"), encoding="utf-8").read()
    view = float(re.search(r'viewBox="0 0 ([\d.]+)', svg).group(1))
    shapes = []
    for tag, attrs in re.findall(r"<(rect|polygon)\b([^>]*)/>", svg):
        a = dict(re.findall(r'([\w-]+)="([^"]*)"', attrs))
        if tag == "rect":
            x, y, w, h = (float(a[k]) for k in ("x", "y", "width", "height"))
            pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        else:
            nums = [float(n) for n in re.split(r"[\s,]+", a["points"].strip())]
            pts = list(zip(nums[::2], nums[1::2]))
        shapes.append((a["fill"], pts))

    paper = shapes[0][0]  # the background rect
    ink = next(fill for fill, _ in shapes if fill != paper)
    xs = [x for _, pts in shapes[1:] for x, _ in pts]
    ys = [y for _, pts in shapes[1:] for _, y in pts]

    def cover(size, k, dx, dy, ss):
        big = Image.new("L", (size * ss, size * ss))
        pen = ImageDraw.Draw(big)
        for fill, pts in shapes:
            pen.polygon([((x * k + dx) * ss, (y * k + dy) * ss) for x, y in pts], fill=255 if fill == ink else 0)
        return big.resize((size, size), Image.BOX)

    return icon_frames(cover, view, (min(xs), min(ys), max(xs), max(ys)), paper, ink, pixels)


def make_mixed_icons():
    """icons/中英混打.svg as a multi-size .ico, and again with every colour flipped for the mode
    button (light on dark, so it stands apart from the input method's own icon beside it);
    icons/全英輸入.svg as it is (dark on light) for the English mode."""
    from PIL import ImageOps
    frames = svg_frames("中英混打", MIXED_ICON_PIXELS)
    tip = os.path.join(ROOT, "cpp", "tip")
    save_ico(frames, os.path.join(tip, "icon_profile.ico"))
    # the program's own icon too: setup.exe, settings.exe, the Start menu, Settings > Apps
    save_ico(frames, os.path.join(ROOT, "installer", "app.ico"))
    save_ico([ImageOps.invert(f) for f in frames], os.path.join(tip, "icon.ico"))
    save_ico(svg_frames("全英輸入", ENGLISH_ICON_PIXELS), os.path.join(tip, "icon_en.ico"))
    # the settings app's own logo (its side bar and 關於 page)
    shutil.copyfile(os.path.join(ROOT, "icons", "中英混打.svg"), os.path.join(ROOT, "settings", "ui", "logo.svg"))


if __name__ == "__main__":
    make_mixed_icons()
