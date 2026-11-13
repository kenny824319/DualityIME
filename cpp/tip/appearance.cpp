#include "appearance.h"

#include <wincodec.h>
#include <wrl/client.h>

#include <algorithm>
#include <cmath>

using Microsoft::WRL::ComPtr;

namespace sz {

namespace {

// (one of each per UI thread: the IME's windows and its TextService are per thread, and one process - explorer -
// may have several; nothing is shared between them)
thread_local Appearance g_look;
thread_local std::string g_last;  // the appearance object and folder g_look was made from

// the reference size of each window (DIP): the photo's scale comes from it, so a window growing or shrinking
// while typing only shows more or less of the photo (settings/ui/app.js draws its preview the same way)
struct Ref {
    const char* window;
    double w, h;
};
constexpr Ref REFS[] = {{"cand", 220, 300}, {"tip", 200, 44}, {"mode", 56, 48}, {"fill", 360, 240},
                        {"keyboard", 600, 280}, {"bar", 420, 44}};

std::wstring wide_from_utf8(const std::string& s) {
    int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), static_cast<int>(s.size()), nullptr, 0);
    std::wstring w(n > 0 ? n : 0, L'\0');
    if (n > 0) MultiByteToWideChar(CP_UTF8, 0, s.c_str(), static_cast<int>(s.size()), w.data(), n);
    return w;
}

bool parse_color(const Json* v, COLORREF& out) {
    if (!v || !v->is_string()) return false;
    const std::string& s = v->as_string();
    if (s.size() != 7 || s[0] != '#') return false;
    unsigned value = 0;
    for (size_t i = 1; i < 7; ++i) {
        char c = s[i];
        int d = c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
        if (d < 0) return false;
        value = value * 16 + d;
    }
    out = RGB((value >> 16) & 0xff, (value >> 8) & 0xff, value & 0xff);
    return true;
}

double number(const Json* o, const char* key, double def, double lo, double hi) {
    const Json* v = o ? o->get(key) : nullptr;
    return v && v->is_number() ? std::clamp(v->as_number(), lo, hi) : def;
}

// a file name of the appearance folder (<slot>.jpg or .png): nothing with a path in it
bool safe_name(const std::wstring& n) {
    return !n.empty() && n.find_first_of(L"\\/:") == std::wstring::npos && n.find(L"..") == std::wstring::npos;
}

Look parse_look(const Json* o, const Look& base, bool window) {
    Look l = base;
    if (!o || !o->is_object()) return l;
    l.fade = number(o, "fade", base.fade, 0, 85);
    l.zoom = number(o, "zoom", base.zoom, 100, 300);
    l.x = number(o, "x", base.x, 0, 1);
    l.y = number(o, "y", base.y, 0, 1);
    if (const Json* im = o->get("image"); im && im->is_string()) {
        std::wstring n = wide_from_utf8(im->as_string());
        l.image = safe_name(n) ? n : L"";
        l.own_image = window;
    }
    return l;
}

COLORREF mix(COLORREF a, COLORREF b, double t) {  // t of b
    auto ch = [&](int x, int y) { return static_cast<int>(std::lround(x + (y - x) * t)); };
    return RGB(ch(GetRValue(a), GetRValue(b)), ch(GetGValue(a), GetGValue(b)), ch(GetBValue(a), GetBValue(b)));
}

// darker on a light color, lighter on a dark one
COLORREF shade(COLORREF c, double t) { return mix(c, luminance(c) > 0.5 ? RGB(0, 0, 0) : RGB(255, 255, 255), t); }

// ---- photos: loaded once (WIC), kept until the file changes ----------------------------------------------------
struct Photo {
    std::wstring path;
    FILETIME written{};
    HBITMAP bmp = nullptr;  // 32 bpp, premultiplied alpha
    int w = 0, h = 0;
    bool alpha = false;  // (a PNG with see-through pixels: drawn over the background color)
    ~Photo() {
        if (bmp) DeleteObject(bmp);
    }
};
thread_local Photo g_photos[8];  // (the photo of "all" and one per window at most)

bool load(const std::wstring& path, Photo& p) {
    ComPtr<IWICImagingFactory> f;
    if (FAILED(CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&f)))) return false;
    ComPtr<IWICBitmapDecoder> dec;
    if (FAILED(f->CreateDecoderFromFilename(path.c_str(), nullptr, GENERIC_READ, WICDecodeMetadataCacheOnDemand, &dec)))
        return false;
    ComPtr<IWICBitmapFrameDecode> frame;
    if (FAILED(dec->GetFrame(0, &frame))) return false;
    ComPtr<IWICFormatConverter> conv;
    if (FAILED(f->CreateFormatConverter(&conv)) ||
        FAILED(conv->Initialize(frame.Get(), GUID_WICPixelFormat32bppPBGRA, WICBitmapDitherTypeNone, nullptr, 0,
                                WICBitmapPaletteTypeCustom)))
        return false;
    UINT w = 0, h = 0;
    if (FAILED(conv->GetSize(&w, &h)) || !w || !h || w > 8000 || h > 8000) return false;
    BITMAPINFO bi{};
    bi.bmiHeader.biSize = sizeof bi.bmiHeader;
    bi.bmiHeader.biWidth = static_cast<LONG>(w);
    bi.bmiHeader.biHeight = -static_cast<LONG>(h);
    bi.bmiHeader.biPlanes = 1;
    bi.bmiHeader.biBitCount = 32;
    void* bits = nullptr;
    HBITMAP bmp = CreateDIBSection(nullptr, &bi, DIB_RGB_COLORS, &bits, nullptr, 0);
    if (!bmp) return false;
    if (FAILED(conv->CopyPixels(nullptr, w * 4, w * h * 4, static_cast<BYTE*>(bits)))) {
        DeleteObject(bmp);
        return false;
    }
    bool alpha = false;
    const BYTE* px = static_cast<const BYTE*>(bits);
    for (size_t i = 3; i < static_cast<size_t>(w) * h * 4 && !alpha; i += 4) alpha = px[i] != 255;
    if (p.bmp) DeleteObject(p.bmp);
    p.bmp = bmp;
    p.alpha = alpha;
    p.w = static_cast<int>(w);
    p.h = static_cast<int>(h);
    return true;
}

const Photo* photo(const std::wstring& path) {
    WIN32_FILE_ATTRIBUTE_DATA fa{};
    if (!GetFileAttributesExW(path.c_str(), GetFileExInfoStandard, &fa)) return nullptr;  // (gone, or not ours to read)
    Photo* slot = nullptr;
    for (auto& p : g_photos)
        if (p.path == path) slot = &p;
    if (slot && slot->bmp && CompareFileTime(&slot->written, &fa.ftLastWriteTime) == 0) return slot;
    if (!slot)
        for (auto& p : g_photos)
            if (p.path.empty() || !p.bmp) {
                slot = &p;
                break;
            }
    if (!slot) slot = &g_photos[0];
    slot->path = path;
    slot->written = fa.ftLastWriteTime;
    if (!load(path, *slot)) {
        if (slot->bmp) DeleteObject(slot->bmp);
        slot->bmp = nullptr;
        return nullptr;
    }
    return slot;
}

}  // namespace

double luminance(COLORREF c) {
    auto lin = [](int v) {
        double x = v / 255.0;
        return x <= 0.03928 ? x / 12.92 : std::pow((x + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * lin(GetRValue(c)) + 0.7152 * lin(GetGValue(c)) + 0.0722 * lin(GetBValue(c));
}

Appearance parse_appearance(const Json* a, const std::wstring& dir) {
    Appearance out;
    out.dir = dir;
    if (!a || !a->is_object()) return out;
    const Json* on = a->get("on");
    out.on = on && on->is_bool() && on->as_bool();
    parse_color(a->get("bg"), out.bg);
    parse_color(a->get("text"), out.text);
    parse_color(a->get("select"), out.select);
    out.all = parse_look(a->get("all"), Look{}, false);
    if (const Json* ws = a->get("windows"); ws && ws->is_object())
        for (auto& [k, v] : ws->members()) {
            bool known = false;
            for (auto& r : REFS) known = known || k == r.window;
            if (!known || !v.is_object()) continue;
            Look l = parse_look(&v, out.all, true);
            if (!l.own_image) l.image = out.all.image;
            out.windows[k] = l;
        }
    return out;
}

void set_appearance(Appearance a) { g_look = std::move(a); }

void set_appearance_from(const Json* a, const std::wstring& dir) {
    // (every reply carries the settings: made again only when they changed)
    std::string key = (a ? a->dump() : std::string()) + "|";
    for (wchar_t c : dir) key += std::to_string(static_cast<unsigned>(c)) + ",";
    if (key == g_last) return;
    g_last = key;
    g_look = parse_appearance(a, dir);
}

const Appearance& appearance() { return g_look; }

Theme derive_theme(COLORREF bg, COLORREF text, COLORREF select) {
    Theme t{};
    t.bg = bg;
    t.key = bg;  // (the background of the candidate list, 快速填入 and the bar)
    t.key_face = shade(bg, 0.06);
    t.key_border = shade(bg, 0.12);
    t.dim = shade(bg, 0.04);
    t.text = text;
    t.label = mix(text, bg, 0.45);
    t.accent = select;
    t.accent_text = luminance(select) > 0.55 ? RGB(0, 0, 0) : RGB(255, 255, 255);
    t.border = mix(text, bg, 0.75);
    t.hint = bg;
    t.hint_border = t.border;
    t.hint_text = text;
    return t;
}

void glass(HDC dc, int x, int y, int w, int h, COLORREF color, BYTE alpha, int radius) {
    if (w <= 0 || h <= 0) return;
    HDC src = CreateCompatibleDC(dc);
    BITMAPINFO bi{};
    bi.bmiHeader.biSize = sizeof bi.bmiHeader;
    bi.bmiHeader.biWidth = 1;
    bi.bmiHeader.biHeight = 1;
    bi.bmiHeader.biPlanes = 1;
    bi.bmiHeader.biBitCount = 32;
    void* bits = nullptr;
    HBITMAP one = CreateDIBSection(dc, &bi, DIB_RGB_COLORS, &bits, nullptr, 0);
    if (!one) {
        DeleteDC(src);
        return;
    }
    *static_cast<DWORD*>(bits) = (GetRValue(color) << 16) | (GetGValue(color) << 8) | GetBValue(color);
    HGDIOBJ old = SelectObject(src, one);
    HRGN clip = radius > 0 ? CreateRoundRectRgn(x, y, x + w + 1, y + h + 1, radius, radius) : nullptr;
    if (clip) SelectClipRgn(dc, clip);
    BLENDFUNCTION bf{AC_SRC_OVER, 0, alpha, 0};
    AlphaBlend(dc, x, y, w, h, src, 0, 0, 1, 1, bf);
    if (clip) {
        SelectClipRgn(dc, nullptr);
        DeleteObject(clip);
    }
    SelectObject(src, old);
    DeleteObject(one);
    DeleteDC(src);
}

bool draw_background(HDC dc, double scale, int w, int h, const char* window, int radius, COLORREF border) {
    const Appearance& a = g_look;
    if (!a.on || a.dir.empty()) return false;
    auto it = a.windows.find(window);
    const Look& l = it != a.windows.end() ? it->second : a.all;
    if (l.image.empty()) return false;
    const Photo* p = photo(a.dir + L"\\" + l.image);
    if (!p) return false;
    double rw = 220, rh = 300;
    for (auto& r : REFS)
        if (std::string(window) == r.window) rw = r.w, rh = r.h;
    rw *= scale;
    rh *= scale;
    double k = std::max(rw / p->w, rh / p->h) * l.zoom / 100;
    k = std::max({k, static_cast<double>(w) / p->w, static_cast<double>(h) / p->h});  // (still covers a bigger window)
    double dw = p->w * k, dh = p->h * k;
    double left = std::clamp(rw / 2 - l.x * dw, w - dw, 0.0);
    double top = std::clamp(rh / 2 - l.y * dh, h - dh, 0.0);
    int r = static_cast<int>(radius * scale);
    HRGN clip = CreateRoundRectRgn(0, 0, w + 1, h + 1, r, r);
    SelectClipRgn(dc, clip);
    HDC src = CreateCompatibleDC(dc);
    HGDIOBJ old = SelectObject(src, p->bmp);
    int x0 = static_cast<int>(std::floor(left)), y0 = static_cast<int>(std::floor(top));
    int x1 = static_cast<int>(std::ceil(dw)), y1 = static_cast<int>(std::ceil(dh));
    if (p->alpha) {  // see-through pixels show the background color (as the page's preview)
        HBRUSH b = CreateSolidBrush(a.bg);
        RECT all{0, 0, w, h};
        FillRect(dc, &all, b);
        DeleteObject(b);
        BLENDFUNCTION bf{AC_SRC_OVER, 0, 255, AC_SRC_ALPHA};
        AlphaBlend(dc, x0, y0, x1, y1, src, 0, 0, p->w, p->h, bf);
    } else {
        SetStretchBltMode(dc, HALFTONE);
        SetBrushOrgEx(dc, 0, 0, nullptr);
        StretchBlt(dc, x0, y0, x1, y1, src, 0, 0, p->w, p->h, SRCCOPY);
    }
    SelectObject(src, old);
    DeleteDC(src);
    COLORREF veil = luminance(a.text) > 0.5 ? RGB(0, 0, 0) : RGB(255, 255, 255);
    glass(dc, 0, 0, w, h, veil, static_cast<BYTE>(std::lround(l.fade * 255 / 100)), 0);
    SelectClipRgn(dc, nullptr);
    DeleteObject(clip);
    // the border, as the box draws it
    HPEN pen = CreatePen(PS_SOLID, std::max(1, static_cast<int>(scale)), border);
    HGDIOBJ op = SelectObject(dc, pen), ob = SelectObject(dc, GetStockObject(NULL_BRUSH));
    RoundRect(dc, 0, 0, w, h, r, r);
    SelectObject(dc, op);
    SelectObject(dc, ob);
    DeleteObject(pen);
    return true;
}

}  // namespace sz
