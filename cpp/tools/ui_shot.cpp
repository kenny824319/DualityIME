// The input method's windows drawn as the DLL draws them (cpp/tip/ui.cpp, appearance.cpp), in this process, each
// saved as a BMP - to see a change of the windows without installing the DLL (符號鍵盤自訂 #69: emoji in colour, ten
// tabs of 8-cell names; the hint box at the caret, #73).
//   sz_uishot <out folder> [--photo <a folder holding photo.png>]
// Writes keyboard.bmp, keyboard_page2.bmp and hint_<n>.bmp; with --photo, the same with the custom look and that
// photo (keyboard_photo.bmp, ...).
#include <windows.h>
#include <objbase.h>
#include <psapi.h>

#include <cstdio>
#include <string>
#include <vector>

#include "appearance.h"
#include "json.h"
#include "ui.h"

namespace sz {
HINSTANCE g_module = GetModuleHandleW(nullptr);  // (the DLL's dllmain.cpp has it there; here this program)
}

using namespace sz;

static void pump(DWORD ms) {
    DWORD end = GetTickCount() + ms;
    do {
        MSG msg;
        while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
        Sleep(5);
    } while (static_cast<LONG>(GetTickCount() - end) < 0);
}

// the window as it is on screen (DWM's copy: PrintWindow with PW_RENDERFULLCONTENT), as a 32-bit BMP
static bool save(HWND hwnd, const std::wstring& path) {
    RECT rc;
    if (!hwnd || !GetWindowRect(hwnd, &rc)) return false;
    int w = rc.right - rc.left, h = rc.bottom - rc.top;
    HDC screen = GetDC(nullptr);
    HDC mem = CreateCompatibleDC(screen);
    BITMAPINFO bi{};
    bi.bmiHeader = {sizeof(BITMAPINFOHEADER), w, -h, 1, 32, BI_RGB};
    void* bits = nullptr;
    HBITMAP bmp = CreateDIBSection(screen, &bi, DIB_RGB_COLORS, &bits, nullptr, 0);
    HGDIOBJ old = SelectObject(mem, bmp);
    bool ok = PrintWindow(hwnd, mem, 2 /* PW_RENDERFULLCONTENT */) != 0;
    if (!ok) ok = BitBlt(mem, 0, 0, w, h, screen, rc.left, rc.top, SRCCOPY) != 0;
    if (ok) {
        BITMAPFILEHEADER bf{0x4D42, static_cast<DWORD>(sizeof bf + sizeof(BITMAPINFOHEADER) + w * h * 4), 0, 0,
                            sizeof bf + sizeof(BITMAPINFOHEADER)};
        FILE* f = _wfopen(path.c_str(), L"wb");
        if (f) {
            fwrite(&bf, sizeof bf, 1, f);
            fwrite(&bi.bmiHeader, sizeof(BITMAPINFOHEADER), 1, f);
            fwrite(bits, 4, static_cast<size_t>(w) * h, f);
            fclose(f);
        } else {
            ok = false;
        }
    }
    SelectObject(mem, old);
    DeleteObject(bmp);
    DeleteDC(mem);
    ReleaseDC(nullptr, screen);
    std::wprintf(L"%ls %ls (%dx%d)\n", ok ? L"saved" : L"FAILED", path.c_str(), w, h);
    return ok;
}

int wmain(int argc, wchar_t** argv) {
    if (argc < 2) {
        std::fprintf(stderr, "usage: sz_uishot <out folder> [--photo <folder holding photo.png>]\n");
        return 2;
    }
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);  // (the photos are read through WIC: the app has COM, so must we)
    std::wstring out = argv[1];
    if (out == L"--time") {
        // what the keyboard costs to draw (#69 Task 4: colour through Direct2D): the first show (Direct2D and
        // DirectWrite made then), then 50 repaints; this process's memory before and after
        std::vector<std::wstring> names = {L"一般", L"數學", L"特殊"}, keys;
        for (wchar_t c : std::wstring(L"`1234567890-=《》〈〉“”【】（）〔〕＼※＊○●「」『』：；、！？…‥—｛｝，。／"))
            keys.push_back(std::wstring(1, c));
        if (!(argc > 2 && std::wstring(argv[2]) == L"plain")) {  // (--time plain: the built-in page, no emoji)
            keys[1] = L"😀";
            keys[2] = L"👩‍💻";
        }
        PROCESS_MEMORY_COUNTERS before{}, after{};
        GetProcessMemoryInfo(GetCurrentProcess(), &before, sizeof before);
        LARGE_INTEGER f, t0, t1, t2;
        QueryPerformanceFrequency(&f);
        SymbolKeyboard kb;
        RECT caret{400, 300, 402, 320};
        QueryPerformanceCounter(&t0);
        kb.show(0, 1, names, keys, caret);
        HWND hwnd = FindWindowW(L"DualityIMESymbols", nullptr);
        UpdateWindow(hwnd);
        QueryPerformanceCounter(&t1);
        for (int i = 0; i < 50; ++i) {
            InvalidateRect(hwnd, nullptr, FALSE);
            UpdateWindow(hwnd);
        }
        QueryPerformanceCounter(&t2);
        GetProcessMemoryInfo(GetCurrentProcess(), &after, sizeof after);
        std::printf("first show and paint: %.1f ms\nrepaint: %.2f ms (50)\nworking set: %.1f -> %.1f MB\n",
                    1000.0 * (t1.QuadPart - t0.QuadPart) / f.QuadPart, 1000.0 * (t2.QuadPart - t1.QuadPart) / f.QuadPart / 50,
                    before.WorkingSetSize / 1048576.0, after.WorkingSetSize / 1048576.0);
        return 0;
    }
    std::wstring photo_dir = argc > 3 && std::wstring(argv[2]) == L"--photo" ? argv[3] : L"";
    CreateDirectoryW(out.c_str(), nullptr);

    // ten pages shown, names of 8 cells (4 CJK, 8 ASCII), a page of one's own with emoji (joiners, a flag, a keycap)
    std::vector<std::wstring> names = {L"表情 :)", L"一般", L"數學", L"特殊", L"圈圈數字", L"Emoji123", L"箭頭符號",
                                       L"希臘字母", L"顏文字", L"我的符號"};
    std::vector<std::wstring> emoji(47, L"");
    const wchar_t* faces[] = {L"♪", L"😀", L"😂", L"🥲", L"😍", L"👍🏽", L"🙏", L"👩‍💻", L"❤️", L"🇹🇼", L"1️⃣", L"✨", L"🔥",
                              L"★", L"é", L"中", L"A", L"《", L"》", L"※", L"🐱", L"🐶", L"🍜", L"☕"};
    for (size_t i = 0; i < std::size(faces); ++i) emoji[i] = faces[i];
    std::vector<std::wstring> builtin;
    for (wchar_t c : std::wstring(L"`1234567890-=《》〈〉“”【】（）〔〕＼※＊○●「」『』：；、！？…‥—｛｝，。／")) builtin.push_back(std::wstring(1, c));
    RECT caret{400, 300, 402, 320};

    for (int round = 0; round < (photo_dir.empty() ? 1 : 2); ++round) {
        std::wstring tag = round ? L"_photo" : L"";
        if (round) {
            bool ok = false;
            Json a = Json::parse("{\"on\": true, \"bg\": \"#1d2340\", \"text\": \"#e6e9ff\", \"select\": \"#6c7cff\", "
                                 "\"all\": {\"image\": \"photo.png\", \"fade\": 45}}", &ok);
            set_appearance(parse_appearance(&a, photo_dir));
        }
        {
            SymbolKeyboard kb;
            kb.show(0, 1, names, emoji, caret);
            pump(400);
            save(FindWindowW(L"DualityIMESymbols", nullptr), out + L"\\keyboard" + tag + L".bmp");
            kb.show(1, 30, names, builtin, caret);
            pump(400);
            save(FindWindowW(L"DualityIMESymbols", nullptr), out + L"\\keyboard_page2" + tag + L".bmp");
            kb.hide();
        }
        int n = 0;
        for (const wchar_t* text : {L"中", L"Insert 沒有放開"}) {
            HintWindow hint;
            hint.look = "mode";
            hint.show(text, caret, 0, false, true);
            pump(300);
            save(FindWindowW(L"DualityIMEHint", nullptr), out + L"\\hint_" + std::to_wstring(++n) + tag + L".bmp");
            hint.hide();
        }
    }
    return 0;
}
