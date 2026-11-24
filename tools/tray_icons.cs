using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.Drawing.Text;
using System.IO;

// Draws the taskbar icons the way Windows 11 shows its own input methods there (the Korean one's
// 가 / 한, for instance): the bare character, white, smooth, no tile behind it.
// Each comes white (for a dark taskbar) and black (for a light one): the input method shows the one
// for the taskbar's theme (cpp\tip\langbar.cpp, and the engine for the input method's own icon).
// Around the character goes a thin rim of the other colour. On the taskbar the icon is made for it
// cannot be seen; it is there for the moment after the theme changes, until Windows draws the icon
// again (its own icon only when the input method is switched): the character still reads.
public static class IconDraw {
    public const string FontName = "Microsoft JhengHei";
    const float Boost = 2.2f;   // strokes thinner than a pixel come out grey: lifted, as strong as Windows' own

    // ch at S x S pixels. all: every character of the set: they are drawn at one size and on one
    // line, as in a line of text, so the icons sit level next to each other on the taskbar.
    public static Bitmap Draw(string ch, string all, int S, bool black) {
        int rim = Math.Max(1, (int)Math.Round(S / 32.0));
        int box = S - 2 * rim, C = S * 3;
        // hinted up to 48px (stems on whole pixels), plain antialiasing above
        TextRenderingHint hint = S <= 48 ? TextRenderingHint.AntiAliasGridFit : TextRenderingHint.AntiAlias;
        // the largest size at which every character fits the box; where they all are (drawn from the same point)
        float lo = 2, hi = S * 3, em = 0;
        Rectangle bb = Rectangle.Empty;
        for (int it = 0; it < 18; it++) {
            float mid = (lo + hi) / 2;
            Rectangle u = Rectangle.Empty;
            bool fits = true, any = false;
            foreach (char c in all) {
                Rectangle r; Render(c.ToString(), C, mid, hint, S, out r);
                if (r.Width == 0) continue;
                any = true;
                if (r.Width > box || r.Height > box) fits = false;
                u = u.IsEmpty ? r : Rectangle.Union(u, r);
            }
            if (!any) lo = mid;
            else if (fits && u.Height <= box) { lo = mid; em = mid; bb = u; }
            else hi = mid;
        }
        var b = new Bitmap(S, S, PixelFormat.Format32bppArgb);
        if (em == 0) return b;
        Rectangle own;
        float[,] m = Render(ch, C, em, hint, S, out own);
        // across: this character centred; up and down: the line all of them stand on, centred
        int ox = (S - own.Width) / 2 - own.X, oy = (S - bb.Height) / 2 - bb.Y;
        var glyph = new float[S, S];
        for (int y = 0; y < S; y++)
            for (int x = 0; x < S; x++) {
                int sx = x - ox, sy = y - oy;
                float v = (sx < 0 || sy < 0 || sx >= C || sy >= C) ? 0 : m[sx, sy];
                glyph[x, y] = S <= 48 ? Math.Min(1, v * Boost) : v;
            }
        for (int y = 0; y < S; y++)
            for (int x = 0; x < S; x++) {
                float g = glyph[x, y], around = 0;
                for (int dy = -rim; dy <= rim; dy++)
                    for (int dx = -rim; dx <= rim; dx++) {
                        int xx = x + dx, yy = y + dy;
                        if (xx < 0 || yy < 0 || xx >= S || yy >= S) continue;
                        float v = glyph[xx, yy] * (dx == 0 || dy == 0 ? 1f : 0.6f);   // (lighter on the diagonals)
                        if (v > around) around = v;
                    }
                float a = g + (1 - g) * around;                     // the character over its rim
                int c = a <= 0 ? 0 : (int)Math.Round(255 * g / a);  // (white character, black rim)
                if (black) c = 255 - c;
                b.SetPixel(x, y, Color.FromArgb((int)Math.Round(a * 255), c, c, c));
            }
        return b;
    }

    // the character in white on black on a C x C canvas, as coverage 0..1; bb: where it is
    static float[,] Render(string ch, int C, float em, TextRenderingHint hint, int S, out Rectangle bb) {
        var m = new float[C, C];
        using (var b = new Bitmap(C, C, PixelFormat.Format32bppArgb))
        using (var g = Graphics.FromImage(b))
        using (var f = new Font(FontName, em, FontStyle.Regular, GraphicsUnit.Pixel)) {
            g.Clear(Color.Black);
            g.TextRenderingHint = hint;
            g.DrawString(ch, f, Brushes.White, S / 2, S / 2, StringFormat.GenericTypographic);
            int x0 = C, y0 = C, x1 = -1, y1 = -1;
            BitmapData d = b.LockBits(new Rectangle(0, 0, C, C), ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
            var px = new byte[d.Stride * C];
            System.Runtime.InteropServices.Marshal.Copy(d.Scan0, px, 0, px.Length);
            b.UnlockBits(d);
            for (int y = 0; y < C; y++)
                for (int x = 0; x < C; x++) {
                    float v = px[y * d.Stride + x * 4 + 1] / 255f;
                    m[x, y] = v;
                    if (v > 0.25f) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
                }
            bb = x1 < 0 ? Rectangle.Empty : new Rectangle(x0, y0, x1 - x0 + 1, y1 - y0 + 1);
        }
        return m;
    }

    // .ico: 32bpp BMP entries below 256px, PNG for 256px (what rc.exe and old loaders expect)
    public static void SaveIco(string path, List<Bitmap> images) {
        var blobs = new List<byte[]>();
        foreach (Bitmap b in images) blobs.Add(b.Width >= 256 ? Png(b) : Dib(b));
        using (var w = new BinaryWriter(File.Create(path))) {
            w.Write((ushort)0); w.Write((ushort)1); w.Write((ushort)images.Count);
            int offset = 6 + 16 * images.Count;
            for (int i = 0; i < images.Count; i++) {
                int s = images[i].Width;
                w.Write((byte)(s >= 256 ? 0 : s)); w.Write((byte)(s >= 256 ? 0 : s));
                w.Write((byte)0); w.Write((byte)0); w.Write((ushort)1); w.Write((ushort)32);
                w.Write(blobs[i].Length); w.Write(offset);
                offset += blobs[i].Length;
            }
            foreach (byte[] blob in blobs) w.Write(blob);
        }
    }

    static byte[] Png(Bitmap b) { using (var ms = new MemoryStream()) { b.Save(ms, ImageFormat.Png); return ms.ToArray(); } }

    static byte[] Dib(Bitmap b) {
        int s = b.Width, maskStride = ((s + 31) / 32) * 4;
        using (var ms = new MemoryStream())
        using (var w = new BinaryWriter(ms)) {
            w.Write(40); w.Write(s); w.Write(s * 2); w.Write((ushort)1); w.Write((ushort)32);
            w.Write(0); w.Write(s * s * 4 + maskStride * s); w.Write(0); w.Write(0); w.Write(0); w.Write(0);
            for (int y = s - 1; y >= 0; y--)
                for (int x = 0; x < s; x++) { Color c = b.GetPixel(x, y); w.Write(c.B); w.Write(c.G); w.Write(c.R); w.Write(c.A); }
            for (int y = s - 1; y >= 0; y--) {
                var row = new byte[maskStride];
                for (int x = 0; x < s; x++) if (b.GetPixel(x, y).A == 0) row[x / 8] |= (byte)(0x80 >> (x % 8));
                w.Write(row);
            }
            return ms.ToArray();
        }
    }
}
