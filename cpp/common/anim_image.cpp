#include "anim_image.h"

#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <wincodec.h>
#include <wrl/client.h>

#include <algorithm>
#include <array>
#include <cstring>

using Microsoft::WRL::ComPtr;

namespace sz {

namespace {

constexpr size_t MAX_FILE = 64u << 20;  // (a file larger than this is no photo to play)

bool read_all(const std::wstring& path, std::vector<uint8_t>& out) {
    HANDLE f = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
                           OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (f == INVALID_HANDLE_VALUE) return false;
    LARGE_INTEGER size{};
    bool ok = GetFileSizeEx(f, &size) && size.QuadPart > 0 && static_cast<unsigned long long>(size.QuadPart) <= MAX_FILE;
    if (ok) {
        out.resize(static_cast<size_t>(size.QuadPart));
        DWORD got = 0;
        ok = ReadFile(f, out.data(), static_cast<DWORD>(out.size()), &got, nullptr) && got == out.size();
    }
    CloseHandle(f);
    return ok;
}

int played_delay(long long ms) { return ms <= 10 ? 100 : static_cast<int>(std::min<long long>(ms, 60000)); }

ComPtr<IWICImagingFactory> factory() {
    ComPtr<IWICImagingFactory> f;
    CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&f));
    return f;
}

// a decoded frame as 32-bit premultiplied BGRA of its own size
bool pixels(IWICImagingFactory* f, IWICBitmapSource* src, UINT& w, UINT& h, std::vector<uint8_t>& out) {
    ComPtr<IWICFormatConverter> conv;
    if (FAILED(f->CreateFormatConverter(&conv)) ||
        FAILED(conv->Initialize(src, GUID_WICPixelFormat32bppPBGRA, WICBitmapDitherTypeNone, nullptr, 0,
                                WICBitmapPaletteTypeCustom)) ||
        FAILED(conv->GetSize(&w, &h)) || !w || !h || w > 8000 || h > 8000)
        return false;
    out.assign(static_cast<size_t>(w) * h * 4, 0);
    return SUCCEEDED(conv->CopyPixels(nullptr, w * 4, static_cast<UINT>(out.size()), out.data()));
}

// a frame's pixels onto the picture at (x, y): over what is there (premultiplied), or replacing it
void put(std::vector<uint8_t>& canvas, int cw, int ch, const std::vector<uint8_t>& px, int fw, int fh, int x, int y,
         bool over) {
    for (int r = 0; r < fh; ++r) {
        int cy = y + r;
        if (cy < 0 || cy >= ch) continue;
        for (int c = 0; c < fw; ++c) {
            int cx = x + c;
            if (cx < 0 || cx >= cw) continue;
            const uint8_t* s = &px[(static_cast<size_t>(r) * fw + c) * 4];
            uint8_t* d = &canvas[(static_cast<size_t>(cy) * cw + cx) * 4];
            if (!over || s[3] == 255) {
                std::memcpy(d, s, 4);
            } else if (s[3]) {
                int keep = 255 - s[3];
                for (int i = 0; i < 4; ++i) d[i] = static_cast<uint8_t>(s[i] + (d[i] * keep + 127) / 255);
            }
        }
    }
}

void clear(std::vector<uint8_t>& canvas, int cw, int ch, int x, int y, int w, int h) {
    for (int r = std::max(0, y); r < std::min(ch, y + h); ++r) {
        int x0 = std::max(0, x), x1 = std::min(cw, x + w);
        if (x1 > x0) std::memset(&canvas[(static_cast<size_t>(r) * cw + x0) * 4], 0, static_cast<size_t>(x1 - x0) * 4);
    }
}

int ui2(IWICMetadataQueryReader* q, const wchar_t* name, int def) {
    PROPVARIANT v;
    PropVariantInit(&v);
    int out = def;
    if (SUCCEEDED(q->GetMetadataByName(name, &v))) {
        if (v.vt == VT_UI2) out = v.uiVal;
        else if (v.vt == VT_UI1) out = v.bVal;
    }
    PropVariantClear(&v);
    return out;
}

// ---- GIF: WIC's frames ------------------------------------------------------------------------------------------
bool load_gif(const std::wstring& path, AnimImage& out, size_t max_bytes) {
    ComPtr<IWICImagingFactory> f = factory();
    ComPtr<IWICBitmapDecoder> dec;
    if (!f || FAILED(f->CreateDecoderFromFilename(path.c_str(), nullptr, GENERIC_READ, WICDecodeMetadataCacheOnLoad, &dec)))
        return false;
    UINT n = 0;
    if (FAILED(dec->GetFrameCount(&n)) || n < 2) return false;
    ComPtr<IWICMetadataQueryReader> top;
    int cw = 0, ch = 0, loops = 1;  // (no loop block: played once)
    if (SUCCEEDED(dec->GetMetadataQueryReader(&top))) {
        cw = ui2(top.Get(), L"/logscrdesc/Width", 0);
        ch = ui2(top.Get(), L"/logscrdesc/Height", 0);
        PROPVARIANT v;
        PropVariantInit(&v);
        if (SUCCEEDED(top->GetMetadataByName(L"/appext/application", &v)) && v.vt == (VT_UI1 | VT_VECTOR) &&
            v.caub.cElems == 11 &&
            (!std::memcmp(v.caub.pElems, "NETSCAPE2.0", 11) || !std::memcmp(v.caub.pElems, "ANIMEXTS1.0", 11))) {
            PropVariantClear(&v);
            if (SUCCEEDED(top->GetMetadataByName(L"/appext/data", &v)) && v.vt == (VT_UI1 | VT_VECTOR) &&
                v.caub.cElems >= 4 && v.caub.pElems[0] > 0 && v.caub.pElems[1] == 1) {
                int count = v.caub.pElems[2] | (v.caub.pElems[3] << 8);
                loops = count == 0 ? 0 : count + 1;  // (NETSCAPE n: n plays more, as browsers)
            }
        }
        PropVariantClear(&v);
    }
    struct Raw {
        std::vector<uint8_t> px;
        UINT w = 0, h = 0;
        int x = 0, y = 0, disposal = 0, delay = 0;
    };
    std::vector<Raw> raws(n);
    for (UINT i = 0; i < n; ++i) {
        ComPtr<IWICBitmapFrameDecode> fr;
        if (FAILED(dec->GetFrame(i, &fr)) || !pixels(f.Get(), fr.Get(), raws[i].w, raws[i].h, raws[i].px)) return false;
        ComPtr<IWICMetadataQueryReader> q;
        if (SUCCEEDED(fr->GetMetadataQueryReader(&q))) {
            raws[i].x = ui2(q.Get(), L"/imgdesc/Left", 0);
            raws[i].y = ui2(q.Get(), L"/imgdesc/Top", 0);
            raws[i].disposal = ui2(q.Get(), L"/grctlext/Disposal", 0);
            raws[i].delay = ui2(q.Get(), L"/grctlext/Delay", 0) * 10;
        }
        if (!cw || !ch) {  // (no screen size: the first frame's)
            cw = static_cast<int>(raws[i].w) + raws[i].x;
            ch = static_cast<int>(raws[i].h) + raws[i].y;
        }
    }
    if (cw <= 0 || ch <= 0 || cw > 8000 || ch > 8000 || static_cast<size_t>(cw) * ch * 4 * n > max_bytes) return false;
    std::vector<uint8_t> canvas(static_cast<size_t>(cw) * ch * 4, 0), saved;
    out = AnimImage{};
    out.w = cw;
    out.h = ch;
    out.loops = loops;
    for (UINT i = 0; i < n; ++i) {
        const Raw& r = raws[i];
        if (i > 0) {  // the previous frame's disposal: to see-through (as browsers, not the background colour), or back
            const Raw& p = raws[i - 1];
            if (p.disposal == 2) clear(canvas, cw, ch, p.x, p.y, static_cast<int>(p.w), static_cast<int>(p.h));
            else if (p.disposal == 3 && !saved.empty()) canvas = saved;
        }
        if (r.disposal == 3) saved = canvas;
        put(canvas, cw, ch, r.px, static_cast<int>(r.w), static_cast<int>(r.h), r.x, r.y, true);
        out.frames.push_back(canvas);
        out.delays_ms.push_back(played_delay(r.delay));
    }
    return true;
}

// ---- APNG: its own chunks, each frame a PNG of its own for WIC --------------------------------------------------
uint32_t be32(const uint8_t* p) { return (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) | (uint32_t(p[2]) << 8) | p[3]; }
uint16_t be16(const uint8_t* p) { return static_cast<uint16_t>((p[0] << 8) | p[1]); }

uint32_t crc32(const uint8_t* p, size_t n, uint32_t c = 0xffffffffu) {
    // (made once, safely from any thread: every UI thread of a program may load a photo)
    static const std::array<uint32_t, 256> table = [] {
        std::array<uint32_t, 256> t{};
        for (uint32_t i = 0; i < 256; ++i) {
            uint32_t k = i;
            for (int j = 0; j < 8; ++j) k = k & 1 ? 0xedb88320u ^ (k >> 1) : k >> 1;
            t[i] = k;
        }
        return t;
    }();
    for (size_t i = 0; i < n; ++i) c = table[(c ^ p[i]) & 0xff] ^ (c >> 8);
    return c;
}

void put_be32(std::vector<uint8_t>& v, uint32_t x) {
    for (int s = 24; s >= 0; s -= 8) v.push_back(static_cast<uint8_t>(x >> s));
}

void chunk(std::vector<uint8_t>& png, const char* type, const uint8_t* data, size_t n) {
    put_be32(png, static_cast<uint32_t>(n));
    size_t at = png.size();
    png.insert(png.end(), type, type + 4);
    if (n) png.insert(png.end(), data, data + n);
    put_be32(png, crc32(&png[at], n + 4) ^ 0xffffffffu);
}

bool decode_png(IWICImagingFactory* f, const std::vector<uint8_t>& png, UINT& w, UINT& h, std::vector<uint8_t>& out) {
    ComPtr<IWICStream> s;
    ComPtr<IWICBitmapDecoder> dec;
    ComPtr<IWICBitmapFrameDecode> fr;
    return SUCCEEDED(f->CreateStream(&s)) &&
           SUCCEEDED(s->InitializeFromMemory(const_cast<BYTE*>(png.data()), static_cast<DWORD>(png.size()))) &&
           SUCCEEDED(f->CreateDecoderFromStream(s.Get(), &GUID_ContainerFormatPng, WICDecodeMetadataCacheOnDemand, &dec)) &&
           SUCCEEDED(dec->GetFrame(0, &fr)) && pixels(f, fr.Get(), w, h, out);
}

bool load_apng(const std::vector<uint8_t>& file, AnimImage& out, size_t max_bytes) {
    static const uint8_t SIG[8] = {0x89, 'P', 'N', 'G', '\r', '\n', 0x1a, '\n'};
    if (file.size() < 8 || std::memcmp(file.data(), SIG, 8)) return false;
    struct Frame {
        uint32_t w = 0, h = 0, x = 0, y = 0;
        int delay = 0, dispose = 0, blend = 0;
        std::vector<uint8_t> z;  // the zlib data (IDAT's, or fdAT's without their sequence numbers)
    };
    std::vector<uint8_t> ihdr, shared;  // (shared: PLTE and tRNS, as chunks, for every frame's PNG)
    std::vector<Frame> frames;
    bool actl = false, seen_idat = false, frame_open = false;
    uint32_t plays = 0;
    size_t pos = 8;
    while (pos + 12 <= file.size()) {
        uint32_t n = be32(&file[pos]);
        if (n > file.size() - pos - 12) return false;
        const char* type = reinterpret_cast<const char*>(&file[pos + 4]);
        const uint8_t* d = &file[pos + 8];
        if (!std::memcmp(type, "IHDR", 4) && n == 13) {
            ihdr.assign(d, d + 13);
        } else if (!std::memcmp(type, "acTL", 4) && n == 8 && !seen_idat) {
            actl = true;
            plays = be32(d + 4);
        } else if ((!std::memcmp(type, "PLTE", 4) || !std::memcmp(type, "tRNS", 4)) && !seen_idat) {
            shared.insert(shared.end(), &file[pos], &file[pos + 12 + n]);
        } else if (!std::memcmp(type, "fcTL", 4) && n == 26) {
            Frame fr;
            fr.w = be32(d + 4);
            fr.h = be32(d + 8);
            fr.x = be32(d + 12);
            fr.y = be32(d + 16);
            int num = be16(d + 20), den = be16(d + 22);
            fr.delay = played_delay(num * 1000LL / (den ? den : 100));
            fr.dispose = d[24];
            fr.blend = d[25];
            frames.push_back(std::move(fr));
            frame_open = true;
        } else if (!std::memcmp(type, "IDAT", 4)) {
            seen_idat = true;
            if (frame_open && frames.size() == 1) frames.back().z.insert(frames.back().z.end(), d, d + n);
        } else if (!std::memcmp(type, "fdAT", 4) && n >= 4 && frame_open && !frames.empty()) {
            frames.back().z.insert(frames.back().z.end(), d + 4, d + n);
        } else if (!std::memcmp(type, "IEND", 4)) {
            break;
        }
        pos += 12 + n;
    }
    if (!actl || ihdr.size() != 13 || frames.size() < 2) return false;
    int cw = static_cast<int>(be32(&ihdr[0])), ch = static_cast<int>(be32(&ihdr[4]));
    if (cw <= 0 || ch <= 0 || cw > 8000 || ch > 8000 || static_cast<size_t>(cw) * ch * 4 * frames.size() > max_bytes)
        return false;
    ComPtr<IWICImagingFactory> f = factory();
    if (!f) return false;
    std::vector<uint8_t> canvas(static_cast<size_t>(cw) * ch * 4, 0), saved;
    out = AnimImage{};
    out.w = cw;
    out.h = ch;
    out.loops = static_cast<int>(std::min<uint32_t>(plays, 100000));
    for (size_t i = 0; i < frames.size(); ++i) {
        Frame& fr = frames[i];
        if (fr.z.empty() || !fr.w || !fr.h || fr.x + fr.w > static_cast<uint32_t>(cw) || fr.y + fr.h > static_cast<uint32_t>(ch))
            return false;
        std::vector<uint8_t> png(SIG, SIG + 8), head = ihdr;
        for (int k = 0; k < 4; ++k) {
            head[k] = static_cast<uint8_t>(fr.w >> (24 - 8 * k));
            head[4 + k] = static_cast<uint8_t>(fr.h >> (24 - 8 * k));
        }
        chunk(png, "IHDR", head.data(), head.size());
        png.insert(png.end(), shared.begin(), shared.end());
        chunk(png, "IDAT", fr.z.data(), fr.z.size());
        chunk(png, "IEND", nullptr, 0);
        std::vector<uint8_t> px;
        UINT w = 0, h = 0;
        if (!decode_png(f.Get(), png, w, h, px) || w != fr.w || h != fr.h) return false;
        int dispose = fr.dispose == 2 && i == 0 ? 1 : fr.dispose;  // (the first frame's "previous" is "background")
        if (dispose == 2) saved = canvas;
        put(canvas, cw, ch, px, static_cast<int>(w), static_cast<int>(h), static_cast<int>(fr.x), static_cast<int>(fr.y),
            fr.blend == 1);
        out.frames.push_back(canvas);
        out.delays_ms.push_back(fr.delay);
        if (dispose == 1) clear(canvas, cw, ch, static_cast<int>(fr.x), static_cast<int>(fr.y), static_cast<int>(w),
                                static_cast<int>(h));
        else if (dispose == 2) canvas = saved;
        std::vector<uint8_t>().swap(fr.z);
    }
    return true;
}

}  // namespace

bool load_anim(const std::wstring& path, AnimImage& out, size_t max_bytes) {
    std::vector<uint8_t> file;
    if (!read_all(path, file) || file.size() < 8) return false;
    if (!std::memcmp(file.data(), "GIF87a", 6) || !std::memcmp(file.data(), "GIF89a", 6)) {
        std::vector<uint8_t>().swap(file);
        return load_gif(path, out, max_bytes);
    }
    return load_apng(file, out, max_bytes);
}

int frame_at(const std::vector<int>& delays_ms, int loops, long long elapsed_ms, long long* next_ms) {
    long long total = 0;
    for (int d : delays_ms) total += std::max(d, 1);
    int n = static_cast<int>(delays_ms.size());
    if (next_ms) *next_ms = -1;
    if (n == 0 || total <= 0) return 0;
    if (elapsed_ms < 0) elapsed_ms = 0;
    long long cycle = elapsed_ms / total;
    if (loops > 0 && cycle >= loops) return n - 1;
    long long t = elapsed_ms % total, at = 0;
    for (int k = 0; k < n; ++k) {
        at += std::max(delays_ms[k], 1);
        if (t < at) {
            bool last = loops > 0 && cycle == loops - 1 && k == n - 1;
            if (next_ms && !last) *next_ms = cycle * total + at;
            return k;
        }
    }
    return n - 1;
}

}  // namespace sz
