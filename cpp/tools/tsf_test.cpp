// End-to-end test of the registered input method: opens a rich edit box,
// switches this program (only) to 兩儀輸入法, types key scripts with SendInput and
// checks the text that comes out.
//   sz_tsftest <script file> [--tsf]
// Script lines:  keys => expected text
// keys: characters as typed, {ENTER} {ESC} {BACK} {DOWN} {UP} {LEFT} {RIGHT} {TAB}
// {#5} (a numpad key) {SPACE} {SHIFT} (a Shift tap) {SBACK} (Shift+Backspace) {SSPACE} (Shift+Space)
// {WAIT} (half a second). Lines starting with # are comments.
// A line starting with {PAGE}: the keys go to a window like a web page with no text box in
// focus (browsers mark its TSF context disabled and empty), {NOFOCUS}: to a window the app
// turned the input method off for (ImmAssociateContext NULL: games...), {PLAIN}: to a plain window with no text box (a media player's
// shortcuts; nothing marks it, the input method can't tell); the text is then what that window got as WM_CHAR (the input method
// must leave those keys alone: YouTube's J K L). {PASSWORD}: to the text box marked as a
// password box (input scope IS_PASSWORD; the input method must leave the keys alone).
#include <windows.h>
#include <imm.h>
#include <InputScope.h>
#include <msctf.h>
#include <richedit.h>
#include <wrl/client.h>

#include <cstdio>
#include <string>
#include <vector>

#include "json.h"
#include "util.h"

using Microsoft::WRL::ComPtr;

// what a password box says about itself (SetInputScope, from msctf.dll)
static void set_input_scope(HWND hwnd, InputScope scope) {
    using Fn = HRESULT(WINAPI*)(HWND, InputScope);
    static Fn fn = reinterpret_cast<Fn>(GetProcAddress(LoadLibraryW(L"msctf.dll"), "SetInputScope"));
    if (fn) fn(hwnd, scope);
}

static const CLSID CLSID_DualityIME ={0x7F9854C9, 0xF3D8, 0x42DB, {0xBB, 0x12, 0x9F, 0x4D, 0xD0, 0x3A, 0x60, 0x90}};
static const GUID GUID_Profile = {0xBFDAC825, 0xE67C, 0x4E87, {0xB6, 0x44, 0xE6, 0xB5, 0xF7, 0xB3, 0xD0, 0x8B}};

static HWND g_edit;

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

static HWND g_main;
static HWND g_page, g_plain, g_noime;
static std::wstring g_page_text;

static LRESULT CALLBACK page_proc(HWND h, UINT m, WPARAM w, LPARAM l) {
    if (m == WM_CHAR && w >= 0x20) g_page_text.push_back(static_cast<wchar_t>(w));
    if (m == WM_GETDLGCODE) return DLGC_WANTALLKEYS | DLGC_WANTCHARS;
    return DefWindowProcW(h, m, w, l);
}

static void send(std::vector<std::pair<WORD, bool>> events) {  // (vk, up)
    if (GetForegroundWindow() != g_main) {  // never type into another window
        std::printf("stopped: the test window lost the focus\n");
        std::fflush(stdout);
        ExitProcess(3);
    }
    std::vector<INPUT> in;
    for (auto [vk, up] : events) {
        INPUT i{};
        i.type = INPUT_KEYBOARD;
        i.ki.wVk = vk;
        i.ki.wScan = static_cast<WORD>(MapVirtualKeyW(vk, MAPVK_VK_TO_VSC));
        i.ki.dwFlags = up ? KEYEVENTF_KEYUP : 0;
        if (vk == VK_LEFT || vk == VK_RIGHT || vk == VK_UP || vk == VK_DOWN || vk == VK_RSHIFT)
            i.ki.dwFlags |= KEYEVENTF_EXTENDEDKEY;
        in.push_back(i);
    }
    SendInput(static_cast<UINT>(in.size()), in.data(), sizeof(INPUT));
    pump(40);
}

static void tap(WORD vk, bool shift = false) {
    if (shift) send({{VK_LSHIFT, false}, {vk, false}, {vk, true}, {VK_LSHIFT, true}});
    else send({{vk, false}, {vk, true}});
}

static std::wstring text() {
    int n = GetWindowTextLengthW(g_edit);
    std::wstring s(n + 1, L'\0');
    GetWindowTextW(g_edit, s.data(), n + 1);
    s.resize(n);
    return s;
}

static std::string utf8(const std::wstring& w) {
    sz::ustr u;
    for (size_t i = 0; i < w.size(); ++i) {
        char32_t c = w[i];
        if (c >= 0xD800 && c < 0xDC00 && i + 1 < w.size()) c = 0x10000 + ((c - 0xD800) << 10) + (w[++i] - 0xDC00);
        u.push_back(c);
    }
    return sz::to_utf8(u);
}

// {SHOT}: the screen around the test window, to %TEMP%\sz_shot<n>.bmp
static void shot() {
    static int n = 0;
    RECT r;
    GetWindowRect(g_main, &r);
    r.top -= 260;
    r.bottom += 420;
    r.right += 200;
    int w = r.right - r.left, h = r.bottom - r.top;
    HDC screen = GetDC(nullptr), mem = CreateCompatibleDC(screen);
    HBITMAP bmp = CreateCompatibleBitmap(screen, w, h);
    HGDIOBJ old = SelectObject(mem, bmp);
    BitBlt(mem, 0, 0, w, h, screen, r.left, r.top, SRCCOPY | CAPTUREBLT);
    BITMAPINFOHEADER bi{sizeof bi, w, -h, 1, 24, BI_RGB};
    int stride = (w * 3 + 3) & ~3;
    std::vector<BYTE> px(static_cast<size_t>(stride) * h);
    GetDIBits(mem, bmp, 0, h, px.data(), reinterpret_cast<BITMAPINFO*>(&bi), DIB_RGB_COLORS);
    SelectObject(mem, old);
    DeleteObject(bmp);
    DeleteDC(mem);
    ReleaseDC(nullptr, screen);
    wchar_t tmp[MAX_PATH];
    GetTempPathW(MAX_PATH, tmp);
    std::wstring path = std::wstring(tmp) + L"sz_shot" + std::to_wstring(++n) + L".bmp";
    BITMAPFILEHEADER bf{0x4D42, static_cast<DWORD>(sizeof bf + sizeof bi + px.size()), 0, 0, sizeof bf + sizeof bi};
    FILE* f = _wfopen(path.c_str(), L"wb");
    if (!f) return;
    fwrite(&bf, sizeof bf, 1, f);
    fwrite(&bi, sizeof bi, 1, f);
    fwrite(px.data(), 1, px.size(), f);
    fclose(f);
}

static LRESULT CALLBACK proc(HWND h, UINT m, WPARAM w, LPARAM l) {
    if (m == WM_SIZE && g_edit) MoveWindow(g_edit, 0, 0, LOWORD(l), HIWORD(l), TRUE);
    if (m == WM_DESTROY) PostQuitMessage(0);
    return DefWindowProcW(h, m, w, l);
}

int wmain(int argc, wchar_t** argv) {
    if (argc < 2) {
        std::fprintf(stderr, "usage: sz_tsftest <script> [--tsf]\n");
        return 2;
    }
    std::string script;
    if (!sz::read_file(argv[1], script)) return 2;
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);  // like current apps
    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    LoadLibraryW(L"msftedit.dll");
    WNDCLASSW wc{};
    wc.lpfnWndProc = proc;
    wc.hInstance = GetModuleHandleW(nullptr);
    wc.lpszClassName = L"SzTsfTest";
    RegisterClassW(&wc);
    HWND main = CreateWindowW(L"SzTsfTest", L"sz_tsftest", WS_OVERLAPPEDWINDOW | WS_VISIBLE, 200, 420, 700, 300,
                              nullptr, nullptr, wc.hInstance, nullptr);
    g_edit = CreateWindowExW(0, MSFTEDIT_CLASS, L"", WS_CHILD | WS_VISIBLE | ES_MULTILINE, 0, 0, 700, 300, main,
                             nullptr, wc.hInstance, nullptr);
    SendMessageW(g_edit, EM_SETLANGOPTIONS, 0, SendMessageW(g_edit, EM_GETLANGOPTIONS, 0, 0) | IMF_UIFONTS);
    // --tsf: the edit box talks TSF itself (like Word, Chrome) instead of through the IMM32 layer
    // --tip {clsid} {profile}: another zh-TW input method instead (to compare behaviour)
    // --lang 0c04: the language it is set up under (中文(香港), a setting), else 0404
    CLSID clsid = CLSID_DualityIME;
    GUID profile = GUID_Profile;
    LANGID lang = 0x0404;
    for (int i = 2; i < argc; ++i) {
        std::wstring a = argv[i];
        if (a == L"--tsf")
            SendMessageW(g_edit, EM_SETEDITSTYLE, SES_USECTF | SES_CTFALLOWEMBED | SES_CTFALLOWSMARTTAG,
                         SES_USECTF | SES_CTFALLOWEMBED | SES_CTFALLOWSMARTTAG);
        else if (a == L"--lang" && i + 1 < argc)
            lang = static_cast<LANGID>(wcstoul(argv[++i], nullptr, 16));
        else if (a == L"--tip" && i + 2 < argc) {
            CLSIDFromString(argv[i + 1], &clsid);
            CLSIDFromString(argv[i + 2], &profile);
            i += 2;
        }
    }
    // only this program switches input method
    ComPtr<ITfInputProcessorProfileMgr> mgr;
    HRESULT hr = CoCreateInstance(CLSID_TF_InputProcessorProfiles, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&mgr));
    if (SUCCEEDED(hr))
        hr = mgr->ActivateProfile(TF_PROFILETYPE_INPUTPROCESSOR, lang, clsid, profile, nullptr,
                                  TF_IPPMF_FORPROCESS | TF_IPPMF_DONTCARECURRENTINPUTLANGUAGE);
    if (FAILED(hr)) {
        std::printf("could not switch to the input method (0x%08lx): is it registered?\n", hr);
        return 1;
    }
    g_main = main;
    std::printf("profile active\n");
    std::fflush(stdout);
    // Windows lets the program the user is in hand the focus over: borrow its input state
    // (some input methods take a moment to start, or open a window of their own: try a few times)
    for (int attempt = 0; attempt < 10 && GetForegroundWindow() != main; ++attempt) {
        HWND fg = GetForegroundWindow();
        DWORD fg_thread = GetWindowThreadProcessId(fg, nullptr);
        AttachThreadInput(GetCurrentThreadId(), fg_thread, TRUE);
        SetForegroundWindow(main);
        BringWindowToTop(main);
        AttachThreadInput(GetCurrentThreadId(), fg_thread, FALSE);
        pump(300);
    }
    if (GetForegroundWindow() != main) {
        wchar_t title[256] = L"", cls[256] = L"";
        GetWindowTextW(GetForegroundWindow(), title, 256);
        GetClassNameW(GetForegroundWindow(), cls, 256);
        std::printf("stopped: could not bring the test window to the front (in front: %s / %s)\n",
                    utf8(cls).c_str(), utf8(title).c_str());
        return 3;
    }
    SetFocus(g_edit);
    pump(800);  // the engine may still be starting
    // the input method the keys will go to: the one asked for, or the test means nothing
    // (Windows may hand this window the input method of the program that was in front)
    auto active_is_ours = [&]() {
        TF_INPUTPROCESSORPROFILE prof{};
        return SUCCEEDED(mgr->GetActiveProfile(GUID_TFCAT_TIP_KEYBOARD, &prof)) &&
               prof.dwProfileType == TF_PROFILETYPE_INPUTPROCESSOR && IsEqualCLSID(prof.clsid, clsid) &&
               IsEqualGUID(prof.guidProfile, profile);
    };
    auto ensure_active = [&]() {
        for (int attempt = 0; attempt < 3 && !active_is_ours(); ++attempt) {
            mgr->ActivateProfile(TF_PROFILETYPE_INPUTPROCESSOR, lang, clsid, profile, nullptr,
                                 TF_IPPMF_FORPROCESS | TF_IPPMF_DONTCARECURRENTINPUTLANGUAGE);
            pump(300);
        }
        return active_is_ours();
    };
    if (!ensure_active()) {
        std::printf("stopped: another input method is active, not the one asked for\n");
        return 3;
    }

    // the "web page": its own window, and a document whose context is marked like Chrome's
    WNDCLASSW pc{};
    pc.lpfnWndProc = page_proc;
    pc.hInstance = wc.hInstance;
    pc.lpszClassName = L"SzTsfTestPage";
    RegisterClassW(&pc);
    g_page = CreateWindowW(L"SzTsfTestPage", L"", WS_CHILD | WS_VISIBLE, 0, 0, 1, 1, main, nullptr, wc.hInstance,
                           nullptr);
    g_plain = CreateWindowW(L"SzTsfTestPage", L"", WS_CHILD | WS_VISIBLE, 1, 0, 1, 1, main, nullptr, wc.hInstance,
                            nullptr);
    g_noime = CreateWindowW(L"SzTsfTestPage", L"", WS_CHILD | WS_VISIBLE, 2, 0, 1, 1, main, nullptr, wc.hInstance,
                            nullptr);
    ImmAssociateContext(g_noime, nullptr);
    ComPtr<ITfThreadMgr> tm;
    ComPtr<ITfDocumentMgr> page_doc;
    TfClientId cid = TF_CLIENTID_NULL;
    if (SUCCEEDED(CoCreateInstance(CLSID_TF_ThreadMgr, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&tm))) &&
        SUCCEEDED(tm->Activate(&cid)) && SUCCEEDED(tm->CreateDocumentMgr(&page_doc))) {
        ComPtr<ITfContext> ctx;
        TfEditCookie ec;
        if (SUCCEEDED(page_doc->CreateContext(cid, 0, nullptr, &ctx, &ec))) {
            ComPtr<ITfCompartmentMgr> cm;
            if (SUCCEEDED(ctx.As(&cm)))
                for (const GUID* g : {&GUID_COMPARTMENT_KEYBOARD_DISABLED, &GUID_COMPARTMENT_EMPTYCONTEXT}) {
                    ComPtr<ITfCompartment> comp;
                    VARIANT v;
                    VariantInit(&v);
                    v.vt = VT_I4;
                    v.lVal = 1;
                    if (SUCCEEDED(cm->GetCompartment(*g, &comp))) comp->SetValue(cid, &v);
                }
            page_doc->Push(ctx.Get());
        }
    }

    int failed = 0, passed = 0;
    size_t pos = 0;
    while (pos < script.size()) {
        size_t nl = script.find('\n', pos);
        if (nl == std::string::npos) nl = script.size();
        std::string line = script.substr(pos, nl - pos);
        pos = nl + 1;
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty() || line[0] == '#') continue;
        size_t arrow = line.find(" => ");
        std::string keys = arrow == std::string::npos ? line : line.substr(0, arrow);
        std::string expected = arrow == std::string::npos ? "" : line.substr(arrow + 4);
        SetWindowTextW(g_edit, L"");
        SetForegroundWindow(main);
        SetFocus(g_edit);
        pump(50);
        if (!ensure_active()) {
            std::printf("stopped: another input method became active\n");
            return 3;
        }
        bool to_page = false;
        if (keys.rfind("{PLAIN}", 0) == 0) {
            keys = keys.substr(7);
            g_page_text.clear();
            SetFocus(g_plain);
            pump(100);
            to_page = true;
        }
        bool password = keys.rfind("{PASSWORD}", 0) == 0;
        if (password) {
            keys = keys.substr(10);
            set_input_scope(g_edit, IS_PASSWORD);
            SetFocus(main);
            SetFocus(g_edit);
            pump(100);
        }
        if (keys.rfind("{NOFOCUS}", 0) == 0) {
            keys = keys.substr(9);
            g_page_text.clear();
            SetFocus(g_noime);
            pump(100);
            to_page = true;
        }
        for (const char* mode : {"{PAGE}"}) {
            if (keys.rfind(mode, 0) != 0) continue;
            if (!tm) {
                std::printf("stopped: no TSF thread manager\n");
                return 3;
            }
            ComPtr<ITfDocumentMgr> prev;
            tm->AssociateFocus(g_page, page_doc.Get(), &prev);
            keys = keys.substr(std::string(mode).size());
            g_page_text.clear();
            SetFocus(g_page);
            pump(100);
            to_page = true;
        }
        for (size_t i = 0; i < keys.size();) {
            if (keys[i] == '{') {
                size_t close = keys.find('}', i);
                std::string name = keys.substr(i + 1, close - i - 1);
                i = close + 1;
                static const std::pair<const char*, WORD> named[] = {
                    {"ENTER", VK_RETURN}, {"ESC", VK_ESCAPE}, {"BACK", VK_BACK}, {"DOWN", VK_DOWN}, {"UP", VK_UP},
                    {"LEFT", VK_LEFT}, {"RIGHT", VK_RIGHT}, {"TAB", VK_TAB}, {"SPACE", VK_SPACE}, {"HOME", VK_HOME},
                    {"END", VK_END}};
                bool done = false;
                for (auto& [n, vk] : named)
                    if (name == n) {
                        tap(vk);
                        done = true;
                    }
                if (done) continue;
                if (name == "SHIFT") send({{VK_LSHIFT, false}, {VK_LSHIFT, true}});
                else if (name.size() == 2 && name[0] == '#' && isdigit(static_cast<unsigned char>(name[1])))
                    tap(static_cast<WORD>(VK_NUMPAD0 + (name[1] - '0')));
                else if (name == "SBACK") tap(VK_BACK, true);
                else if (name == "SSPACE") tap(VK_SPACE, true);
                else if (name == "WAIT") pump(500);
                else if (name == "SHOT") {
                    pump(300);
                    shot();
                }
                continue;
            }
            SHORT vks = VkKeyScanW(static_cast<unsigned char>(keys[i]));
            tap(LOBYTE(vks), (HIBYTE(vks) & 1) != 0);
            ++i;
        }
        pump(150);
        std::string got = utf8(to_page ? g_page_text : text());
        while (!got.empty() && (got.back() == '\r' || got.back() == '\n')) got.pop_back();
        bool ok = got == expected;
        (ok ? passed : failed)++;
        std::printf("%s  %s => %s%s%s\n", ok ? "ok  " : "FAIL", keys.c_str(), got.c_str(),
                    ok ? "" : "   (expected ", ok ? "" : (expected + ")").c_str());
        std::fflush(stdout);
        // anything still being typed goes away before the next line
        tap(VK_ESCAPE);
        tap(VK_ESCAPE);
        if (password) {  // (TSF reads the input scope again when the focus comes back)
            set_input_scope(g_edit, IS_DEFAULT);
            SetFocus(main);
            SetFocus(g_edit);
            pump(100);
        }
    }
    if (tm) tm->Deactivate();
    std::printf("%d passed, %d failed\n", passed, failed);
    DestroyWindow(main);
    return failed ? 1 : 0;
}
