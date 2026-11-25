"""自訂外觀的動圖 (#56): the DLL's decoder (cpp/common/anim_image.cpp, through `sz_trace --anim <file>`) gives the
frames Pillow composites, with their delays and loop count - GIF (WIC's frames, their disposal) and APNG (its own
chunk reader, dispose and blend). Pillow writes the files into a temporary folder; skipped without Pillow or the
built sz_trace.exe (cpp\\build.cmd)."""
import hashlib
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "cpp", "build", "x64-Release", "sz_trace.exe")

try:
    from PIL import Image, ImageDraw, ImageSequence
except ImportError:  # pragma: no cover
    Image = None


def rgba_hash(im):
    """sha1 of the frame's RGBA bytes, see-through pixels as 0 0 0 0 (their colour means nothing)."""
    px = bytearray(im.convert("RGBA").tobytes())
    for i in range(0, len(px), 4):
        if px[i + 3] == 0:
            px[i:i + 4] = b"\0\0\0\0"
    return hashlib.sha1(bytes(px)).hexdigest()


def pillow_frames(path):
    """(w, h, [(delay_ms as the DLL plays it, hash)], loops as total plays, 0 forever) by Pillow."""
    with Image.open(path) as im:
        frames = []
        skip = 1 if im.info.get("default_image") else 0  # (an APNG's default image is not one of its frames)
        for k, fr in enumerate(ImageSequence.Iterator(im)):
            if k < skip:
                continue
            d = fr.info.get("duration", 0) or 0
            frames.append((100 if d <= 10 else int(round(d)), rgba_hash(fr)))
        loop = im.info.get("loop")
        if im.format == "GIF":
            loops = 1 if loop is None else (0 if loop == 0 else loop + 1)  # (NETSCAPE n: n more plays, as browsers)
        else:
            loops = loop or 0
        return im.size[0], im.size[1], frames, loops


def gif_bytes(w, h, palette, frames, loop=None):
    """A GIF written by hand, so each frame's rectangle and disposal are exactly as given (Pillow chooses its own
    rectangles: it redraws what a disposal would restore). palette: up to 128 (r, g, b); frames: (x, y, fw, fh,
    disposal, delay_cs, transparent index or None, indices row by row). LZW with literal codes only, a clear code
    every 120 (the code size never grows past 8)."""
    pal = list(palette) + [(0, 0, 0)] * (128 - len(palette))
    out = bytearray(b"GIF89a" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + bytes([0xF6, 0, 0]))
    out += bytes(c for rgb in pal for c in rgb)
    if loop is not None:
        out += b"\x21\xFF\x0BNETSCAPE2.0\x03\x01" + loop.to_bytes(2, "little") + b"\x00"
    for x, y, fw, fh, disposal, delay, transparent, indices in frames:
        flags = (disposal << 2) | (1 if transparent is not None else 0)
        out += bytes([0x21, 0xF9, 4, flags]) + delay.to_bytes(2, "little") + bytes([transparent or 0, 0])
        out += b"\x2C" + b"".join(v.to_bytes(2, "little") for v in (x, y, fw, fh)) + b"\x00"
        codes, bits, n = [128], 0, 0
        for k, ix in enumerate(indices):
            if k and k % 120 == 0:
                codes.append(128)
            codes.append(ix)
        codes.append(129)
        data = bytearray()
        for c in codes:  # (8-bit codes: min code size 7)
            bits |= c << n
            n += 8
            while n >= 8:
                data.append(bits & 0xFF)
                bits >>= 8
                n -= 8
        out += b"\x07"
        for i in range(0, len(data), 255):
            out += bytes([len(data[i:i + 255])]) + data[i:i + 255]
        out += b"\x00"
    return bytes(out + b"\x3B")


def decoded(path):
    out = subprocess.run([EXE, "--anim", path], capture_output=True, timeout=60)
    lines = out.stdout.decode("utf-8").split()
    if out.returncode != 0 or len(lines) < 4:
        return None, out.stderr.decode("utf-8", "replace") + out.stdout.decode("utf-8", "replace")
    w, h, n, loops = (int(x) for x in lines[:4])
    rest = lines[4:]
    return (w, h, [(int(rest[2 * k]), rest[2 * k + 1]) for k in range(n)], loops), ""


@unittest.skipUnless(Image is not None and os.path.exists(EXE), "Pillow or sz_trace.exe (cpp\\build.cmd) missing")
class AnimImageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def check(self, path):
        want = pillow_frames(path)
        got, err = decoded(path)
        self.assertIsNotNone(got, err)
        self.assertEqual(got[:2], want[:2], "size")
        self.assertEqual(got[3], want[3], "loops")
        self.assertEqual([d for d, _ in got[2]], [d for d, _ in want[2]], "delays")
        for k, ((_, g), (_, w)) in enumerate(zip(got[2], want[2])):
            self.assertEqual(g, w, f"frame {k}")
        self.assertEqual(len(got[2]), len(want[2]), "frames")
        return got

    def test_gif_of_whole_frames(self):
        # (delays 50 / 0 / 200: 0 plays as 100, as browsers do; loop 0: forever)
        frames = []
        for k, colour in enumerate([(200, 30, 30), (30, 160, 60), (40, 60, 220)]):
            im = Image.new("RGB", (40, 30), (250, 250, 250))
            ImageDraw.Draw(im).rectangle([4 + 8 * k, 3, 20 + 8 * k, 25], fill=colour)
            frames.append(im)
        path = os.path.join(self.tmp, "a.gif")
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=[50, 0, 200], loop=0)
        got = self.check(path)
        self.assertEqual((got[0], got[1], len(got[2]), got[3]), (40, 30, 3, 0))
        self.assertEqual([d for d, _ in got[2]], [50, 100, 200])

    def test_gif_patch_restored_to_previous(self):
        # (frame 2 a 10x10 red patch disposed "restore to previous", frame 3 a blue patch elsewhere: frame 3 is frame
        # 1's picture with the blue patch - no red; no loop block: played once)
        palette = [(240, 240, 200), (20, 20, 20), (220, 0, 0), (0, 0, 220)]
        whole = [1 if r < 10 else 0 for r in range(30) for _ in range(40)]
        frames = [(0, 0, 40, 30, 1, 10, None, whole), (15, 12, 10, 10, 3, 10, None, [2] * 100),
                  (28, 18, 10, 10, 1, 10, None, [3] * 100)]
        path = os.path.join(self.tmp, "b.gif")
        with open(path, "wb") as f:
            f.write(gif_bytes(40, 30, palette, frames))
        got = self.check(path)
        self.assertEqual(got[3], 1)
        with Image.open(path) as im:  # (and Pillow itself restored it: frame 3 has no red at the patch)
            im.seek(2)
            self.assertEqual(im.convert("RGB").getpixel((20, 16)), (240, 240, 200))

    def test_gif_patch_disposed_to_see_through(self):
        # (disposal 2 clears the patch to see-through - as browsers, not to the background colour; frames with a
        # transparent index draw only their other pixels; loop 3: 4 plays)
        palette = [(240, 240, 200), (20, 20, 20), (220, 0, 0), (0, 0, 220)]
        whole = [1 if (r + c) % 7 == 0 else 0 for r in range(30) for c in range(40)]
        holey = [2 if (r + c) % 2 else 3 for r in range(10) for c in range(10)]
        frames = [(0, 0, 40, 30, 1, 5, None, whole), (5, 5, 10, 10, 2, 7, 3, holey),
                  (20, 10, 10, 10, 1, 1, None, [3] * 100)]
        path = os.path.join(self.tmp, "e.gif")
        with open(path, "wb") as f:
            f.write(gif_bytes(40, 30, palette, frames, loop=3))
        got, err = decoded(path)
        self.assertIsNotNone(got, err)
        self.assertEqual((got[0], got[1], got[3], [d for d, _ in got[2]]), (40, 30, 4, [50, 70, 100]))
        want = pillow_frames(path)
        self.assertEqual([h for _, h in got[2][:2]], [h for _, h in want[2][:2]])
        # (frame 3 written out here: Pillow fills a disposal-2 patch with the colour of the transparent index, opaque
        # blue, where browsers clear it)
        third = Image.new("RGBA", (40, 30))
        third.putdata([palette[i] + (255,) for i in whole])
        for r in range(5, 15):
            for c in range(5, 15):
                third.putpixel((c, r), (0, 0, 0, 0))
        for r in range(10, 20):
            for c in range(20, 30):
                third.putpixel((c, r), (0, 0, 220, 255))
        self.assertEqual(got[2][2][1], rgba_hash(third))

    def test_apng_blend_over_dispose_previous(self):
        frames = []
        for k in range(3):
            im = Image.new("RGBA", (32, 24), (0, 0, 0, 0))
            ImageDraw.Draw(im).ellipse([2 + 6 * k, 2, 14 + 6 * k, 20], fill=(255, 40 * k, 0, 255))
            frames.append(im)
        path = os.path.join(self.tmp, "c.png")
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=[80, 120, 160], loop=0,
                       disposal=[0, 2, 1], blend=[0, 1, 1])
        got = self.check(path)
        self.assertEqual((len(got[2]), got[3]), (3, 0))

    def test_apng_default_image_outside_the_animation(self):
        # (the default image - what a viewer without APNG shows - is no frame: the animation starts at the first fcTL)
        still = Image.new("RGBA", (30, 30), (90, 90, 90, 255))
        frames = []
        for k in range(2):
            im = Image.new("RGBA", (30, 30), (255, 255, 255, 255))
            ImageDraw.Draw(im).rectangle([3 + 10 * k, 5, 12 + 10 * k, 25], fill=(0, 120, 0, 255))
            frames.append(im)
        path = os.path.join(self.tmp, "d.png")
        still.save(path, save_all=True, append_images=frames, default_image=True, duration=[100, 100, 100], loop=1)
        got = self.check(path)
        self.assertEqual((len(got[2]), got[3]), (2, 1))

    def frames_at(self, path, times):
        out = subprocess.run([EXE, "--anim-at", path] + [str(t) for t in times], capture_output=True, timeout=60)
        return [tuple(int(v) for v in line.split()) for line in out.stdout.decode("utf-8").splitlines()]

    def test_the_frame_shown_at_a_time(self):
        # (frame_at: delays 100 / 100 / 100 forever - the frame then and the time of the next change; played once: the
        # last frame stays, no change due - in its own time and after it)
        three = [(0, 0, 4, 4, 1, 10, None, [k] * 16) for k in range(3)]
        palette = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        forever = os.path.join(self.tmp, "forever.gif")
        with open(forever, "wb") as f:
            f.write(gif_bytes(4, 4, palette, three, loop=0))
        self.assertEqual(self.frames_at(forever, [0, 99, 100, 250, 300, 1234]),
                         [(0, 100), (0, 100), (1, 200), (2, 300), (0, 400), (0, 1300)])
        once = os.path.join(self.tmp, "once.gif")
        with open(once, "wb") as f:
            f.write(gif_bytes(4, 4, palette, three))
        self.assertEqual(self.frames_at(once, [0, 150, 250, 350, 99999]), [(0, 100), (1, 200), (2, -1), (2, -1), (2, -1)])

    def test_a_still_is_not_animated(self):
        # (a PNG without acTL, a one-frame GIF: no animation - the DLL loads them as stills)
        for name in ("still.png", "still.gif"):
            path = os.path.join(self.tmp, name)
            Image.new("RGB", (8, 8), (1, 2, 3)).save(path)
            out = subprocess.run([EXE, "--anim", path], capture_output=True, timeout=60)
            self.assertEqual(out.stdout.decode("utf-8").strip(), "still", name)

    def test_the_users_files(self):
        # (docs/superpowers/items/56-animated-photos.md 量測: on the user's computer only)
        folder = os.path.join(os.path.expanduser("~"), "Pictures", "For DualityIME")
        files = {"egypt_happydance.gif": (370, 294, 20, 0, 2000), "梅露露汪汪叫.png": (240, 240, 8, 0, 320)}
        if not all(os.path.exists(os.path.join(folder, f)) for f in files):
            self.skipTest("the user's test files are not on this computer")
        for name, (w, h, n, loops, total) in files.items():
            got, err = decoded(os.path.join(folder, name))
            self.assertIsNotNone(got, err)
            self.assertEqual((got[0], got[1], len(got[2]), got[3], sum(d for d, _ in got[2])), (w, h, n, loops, total),
                             name)
            if name.endswith(".png"):
                self.check(os.path.join(folder, name))


if __name__ == "__main__":
    unittest.main()
