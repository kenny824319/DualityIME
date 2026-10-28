#include "text_service.h"

#include <InputScope.h>
#include <shellapi.h>

#include <algorithm>

#include "globals.h"
#include "langbar.h"
#include "pipe.h"
#include "util.h"

namespace sz {

namespace {

constexpr ULONGLONG SHIFT_TAP_MS = 500;    // holding Shift longer than this is not a tap
constexpr ULONGLONG DOUBLE_CTRL_MS = 400;  // two Ctrl taps this close together: 框選查詢
constexpr ULONGLONG UNDO_MS = 15000;       // Shift+Backspace takes back text sent this long ago at most
constexpr WORD UNDO_MARK = VK_F24;         // sent after the Backspaces that delete it

// debugging: lines go to %TEMP%\dualityime_debug.log, only if that file exists. Never what is typed:
// sizes, positions and kinds only
void debug(const std::string& line) {
    wchar_t tmp[MAX_PATH];
    if (!GetTempPathW(MAX_PATH, tmp)) return;
    std::wstring path = std::wstring(tmp) + L"dualityime_debug.log";
    if (GetFileAttributesW(path.c_str()) == INVALID_FILE_ATTRIBUTES) return;
    FILE* f = _wfopen(path.c_str(), L"ab");
    if (!f) return;
    std::string l = line + "\n";
    fwrite(l.data(), 1, l.size(), f);
    fclose(f);
}

bool down(int vk) { return (GetKeyState(vk) & 0x8000) != 0; }
bool is_numpad(WPARAM vk) { return vk >= VK_NUMPAD0 && vk <= VK_DIVIDE; }
bool printable(wchar_t ch) { return ch >= 0x20 && ch < 0x7f; }

std::wstring wide(const std::string& utf8) {
    ustr u = from_utf8(utf8);
    std::wstring w;
    for (char32_t c : u) {
        if (c >= 0x10000) {
            c -= 0x10000;
            w.push_back(static_cast<wchar_t>(0xD800 + (c >> 10)));
            w.push_back(static_cast<wchar_t>(0xDC00 + (c & 0x3FF)));
        } else {
            w.push_back(static_cast<wchar_t>(c));
        }
    }
    return w;
}

std::string narrow(const std::wstring& w) {
    ustr u;
    for (size_t i = 0; i < w.size(); ++i) {
        char32_t c = w[i];
        if (c >= 0xD800 && c < 0xDC00 && i + 1 < w.size()) c = 0x10000 + ((c - 0xD800) << 10) + (w[++i] - 0xDC00);
        u.push_back(c);
    }
    return to_utf8(u);
}

// UTF-16 offset of the code point at `index` (the engine counts code points)
int utf16_offset(const std::wstring& w, int index) {
    int pos = 0;
    for (int k = 0; k < index && pos < static_cast<int>(w.size()); ++k)
        pos += (w[pos] >= 0xD800 && w[pos] < 0xDC00) ? 2 : 1;
    return pos;
}

const char* special_of(WPARAM vk) {
    switch (vk) {
        case VK_BACK: return "BACK";
        case VK_DELETE: return "DEL";
        case VK_RETURN: return "ENTER";
        case VK_ESCAPE: return "ESC";
        case VK_LEFT: return "LEFT";
        case VK_RIGHT: return "RIGHT";
        case VK_UP: return "UP";
        case VK_DOWN: return "DOWN";
        case VK_HOME: return "HOME";
        case VK_END: return "END";
        case VK_PRIOR: return "PGUP";
        case VK_NEXT: return "PGDN";
        case VK_TAB: return "TAB";
    }
    return nullptr;
}

void run(const std::wstring& file, const std::wstring& args, int show) {
    ShellExecuteW(nullptr, L"open", file.c_str(), args.empty() ? nullptr : args.c_str(), module_dir(g_module).c_str(),
                  show);
}

void open_settings() { run(module_dir(g_module) + L"settings.exe", L"", SW_SHOWNORMAL); }  // (the settings app)

void open_lookup() { run(module_dir(g_module) + L"lookup.exe", L"", SW_SHOWNORMAL); }

// ---- edit sessions ------------------------------------------------------------------------

class EditSession : public ITfEditSession {
public:
    explicit EditSession(std::function<void(TfEditCookie)> fn) : fn_(std::move(fn)) {}
    STDMETHODIMP QueryInterface(REFIID riid, void** ppv) override {
        if (!ppv) return E_INVALIDARG;
        *ppv = nullptr;
        if (riid == IID_IUnknown || riid == IID_ITfEditSession) *ppv = static_cast<ITfEditSession*>(this);
        if (!*ppv) return E_NOINTERFACE;
        AddRef();
        return S_OK;
    }
    STDMETHODIMP_(ULONG) AddRef() override { return InterlockedIncrement(&refs_); }
    STDMETHODIMP_(ULONG) Release() override {
        LONG r = InterlockedDecrement(&refs_);
        if (!r) delete this;
        return r;
    }
    STDMETHODIMP DoEditSession(TfEditCookie ec) override {
        try {
            fn_(ec);
        } catch (...) {
        }
        return S_OK;
    }

private:
    LONG refs_ = 1;
    std::function<void(TfEditCookie)> fn_;
};

// ---- display attribute: the dotted underline under the text being typed -------------------

class AttributeInfo : public ITfDisplayAttributeInfo {
public:
    STDMETHODIMP QueryInterface(REFIID riid, void** ppv) override {
        if (!ppv) return E_INVALIDARG;
        *ppv = nullptr;
        if (riid == IID_IUnknown || riid == IID_ITfDisplayAttributeInfo) *ppv = static_cast<ITfDisplayAttributeInfo*>(this);
        if (!*ppv) return E_NOINTERFACE;
        AddRef();
        return S_OK;
    }
    STDMETHODIMP_(ULONG) AddRef() override { return InterlockedIncrement(&refs_); }
    STDMETHODIMP_(ULONG) Release() override {
        LONG r = InterlockedDecrement(&refs_);
        if (!r) delete this;
        return r;
    }
    STDMETHODIMP GetGUID(GUID* guid) override {
        *guid = GUID_AttrInput;
        return S_OK;
    }
    STDMETHODIMP GetDescription(BSTR* desc) override {
        *desc = SysAllocString(L"兩儀輸入法 組字中");
        return *desc ? S_OK : E_OUTOFMEMORY;
    }
    STDMETHODIMP GetAttributeInfo(TF_DISPLAYATTRIBUTE* attr) override {
        *attr = TF_DISPLAYATTRIBUTE{};
        attr->crText.type = TF_CT_NONE;
        attr->crBk.type = TF_CT_NONE;
        attr->lsStyle = TF_LS_DOT;
        attr->fBoldLine = FALSE;
        attr->crLine.type = TF_CT_NONE;
        attr->bAttr = TF_ATTR_INPUT;
        return S_OK;
    }
    STDMETHODIMP SetAttributeInfo(const TF_DISPLAYATTRIBUTE*) override { return E_NOTIMPL; }
    STDMETHODIMP Reset() override { return S_OK; }

private:
    LONG refs_ = 1;
};

class EnumAttributeInfo : public IEnumTfDisplayAttributeInfo {
public:
    explicit EnumAttributeInfo(int pos = 0) : pos_(pos) {}
    STDMETHODIMP QueryInterface(REFIID riid, void** ppv) override {
        if (!ppv) return E_INVALIDARG;
        *ppv = nullptr;
        if (riid == IID_IUnknown || riid == IID_IEnumTfDisplayAttributeInfo)
            *ppv = static_cast<IEnumTfDisplayAttributeInfo*>(this);
        if (!*ppv) return E_NOINTERFACE;
        AddRef();
        return S_OK;
    }
    STDMETHODIMP_(ULONG) AddRef() override { return InterlockedIncrement(&refs_); }
    STDMETHODIMP_(ULONG) Release() override {
        LONG r = InterlockedDecrement(&refs_);
        if (!r) delete this;
        return r;
    }
    STDMETHODIMP Clone(IEnumTfDisplayAttributeInfo** out) override {
        *out = new EnumAttributeInfo(pos_);
        return S_OK;
    }
    STDMETHODIMP Next(ULONG count, ITfDisplayAttributeInfo** info, ULONG* fetched) override {
        ULONG n = 0;
        while (n < count && pos_ < 1) {
            info[n++] = new AttributeInfo();
            ++pos_;
        }
        if (fetched) *fetched = n;
        return n == count ? S_OK : S_FALSE;
    }
    STDMETHODIMP Reset() override {
        pos_ = 0;
        return S_OK;
    }
    STDMETHODIMP Skip(ULONG count) override {
        pos_ = std::min<int>(1, pos_ + static_cast<int>(count));
        return pos_ >= 1 ? S_FALSE : S_OK;
    }

private:
    LONG refs_ = 1;
    int pos_;
};

}  // namespace

// ---- COM ------------------------------------------------------------------------------------

TextService::TextService() { DllAddRef(); }

TextService::~TextService() { DllRelease(); }

STDMETHODIMP TextService::QueryInterface(REFIID riid, void** ppv) {
    if (!ppv) return E_INVALIDARG;
    *ppv = nullptr;
    if (riid == IID_IUnknown || riid == IID_ITfTextInputProcessor || riid == IID_ITfTextInputProcessorEx)
        *ppv = static_cast<ITfTextInputProcessorEx*>(this);
    else if (riid == IID_ITfThreadMgrEventSink)
        *ppv = static_cast<ITfThreadMgrEventSink*>(this);
    else if (riid == IID_ITfKeyEventSink)
        *ppv = static_cast<ITfKeyEventSink*>(this);
    else if (riid == IID_ITfCompositionSink)
        *ppv = static_cast<ITfCompositionSink*>(this);
    else if (riid == IID_ITfDisplayAttributeProvider)
        *ppv = static_cast<ITfDisplayAttributeProvider*>(this);
    else if (riid == IID_ITfCompartmentEventSink)
        *ppv = static_cast<ITfCompartmentEventSink*>(this);
    else if (riid == IID_ITfThreadFocusSink)
        *ppv = static_cast<ITfThreadFocusSink*>(this);
    else if (riid == IID_ITfTextLayoutSink)
        *ppv = static_cast<ITfTextLayoutSink*>(this);
    if (!*ppv) return E_NOINTERFACE;
    AddRef();
    return S_OK;
}

STDMETHODIMP_(ULONG) TextService::AddRef() { return InterlockedIncrement(&refs_); }

STDMETHODIMP_(ULONG) TextService::Release() {
    LONG r = InterlockedDecrement(&refs_);
    if (!r) delete this;
    return r;
}

// ---- activation -------------------------------------------------------------------------------

STDMETHODIMP TextService::Activate(ITfThreadMgr* tm, TfClientId id) { return ActivateEx(tm, id, 0); }

STDMETHODIMP TextService::ActivateEx(ITfThreadMgr* tm, TfClientId id, DWORD) {
    try {
        thread_mgr_ = tm;
        client_id_ = id;
        {
            wchar_t path[MAX_PATH * 2];
            DWORD n = GetModuleFileNameW(nullptr, path, static_cast<DWORD>(std::size(path)));
            std::wstring p(path, n);
            app_ = lower(narrow(p.substr(p.find_last_of(L"\\/") + 1)));
            exe_path_ = p;
            CharLowerBuffW(exe_path_.data(), static_cast<DWORD>(exe_path_.size()));
        }
        ComPtr<ITfSource> source;
        if (SUCCEEDED(tm->QueryInterface(IID_PPV_ARGS(&source)))) {
            source->AdviseSink(IID_ITfThreadMgrEventSink, static_cast<ITfThreadMgrEventSink*>(this), &tm_cookie_);
            source->AdviseSink(IID_ITfThreadFocusSink, static_cast<ITfThreadFocusSink*>(this), &focus_cookie_);
        }
        ComPtr<ITfKeystrokeMgr> keys;
        if (SUCCEEDED(tm->QueryInterface(IID_PPV_ARGS(&keys))))
            keys->AdviseKeyEventSink(client_id_, static_cast<ITfKeyEventSink*>(this), TRUE);
        ComPtr<ITfCategoryMgr> cats;
        if (SUCCEEDED(CoCreateInstance(CLSID_TF_CategoryMgr, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&cats))))
            cats->RegisterGUID(GUID_AttrInput, &attr_atom_);
        // keyboard on, Chinese
        english_ = false;
        set_compartment(GUID_COMPARTMENT_KEYBOARD_OPENCLOSE, TRUE);
        set_compartment(GUID_COMPARTMENT_KEYBOARD_INPUTMODE_CONVERSION, TF_CONVERSIONMODE_NATIVE);
        advise_compartment(GUID_COMPARTMENT_KEYBOARD_OPENCLOSE, openclose_cookie_, true);
        advise_compartment(GUID_COMPARTMENT_KEYBOARD_INPUTMODE_CONVERSION, conversion_cookie_, true);
        button_.Attach(new LangBarButton(this));
        ComPtr<ITfLangBarItemMgr> bar;
        if (SUCCEEDED(tm->QueryInterface(IID_PPV_ARGS(&bar))))
            button_added_ = SUCCEEDED(bar->AddItem(button_.Get()));
        // the settings, and the engine warmed up before the first key
        Json reply;
        Json req = Json::object();
        req["op"] = Json("state");
        if (ask(req, reply)) read_reply(reply);
        // 開啟時的模式 (a setting): English from the start if so
        if (cfg_.start_mode == "english") {
            english_ = true;
            update_mode_ui();
        }
        activated_at_ = GetTickCount();
        // switched to while a document has the focus: that is the first focus
        ComPtr<ITfDocumentMgr> focused;
        first_focus_at_ = SUCCEEDED(tm->GetFocus(&focused)) && focused ? GetTickCount() : 0;
        debug("activate: " + app_ + " start=" + cfg_.start_mode);
    } catch (...) {
    }
    return S_OK;
}

STDMETHODIMP TextService::Deactivate() {
    try {
        if (composition_ && comp_context_) {
            ComPtr<ITfContext> ctx = comp_context_;
            edit(ctx.Get(), [this](TfEditCookie ec) {
                if (composition_) composition_->EndComposition(ec);  // what is on screen stays
            });
        }
        composition_.Reset();
        comp_context_.Reset();
        watch_layout(nullptr);
        reset_engine();
        hide_ui();
        if (thread_mgr_) {
            ComPtr<ITfSource> source;
            if (SUCCEEDED(thread_mgr_->QueryInterface(IID_PPV_ARGS(&source)))) {
                if (tm_cookie_ != TF_INVALID_COOKIE) source->UnadviseSink(tm_cookie_);
                if (focus_cookie_ != TF_INVALID_COOKIE) source->UnadviseSink(focus_cookie_);
            }
            ComPtr<ITfKeystrokeMgr> keys;
            if (SUCCEEDED(thread_mgr_->QueryInterface(IID_PPV_ARGS(&keys)))) keys->UnadviseKeyEventSink(client_id_);
            advise_compartment(GUID_COMPARTMENT_KEYBOARD_OPENCLOSE, openclose_cookie_, false);
            advise_compartment(GUID_COMPARTMENT_KEYBOARD_INPUTMODE_CONVERSION, conversion_cookie_, false);
            if (button_ && button_added_) {
                ComPtr<ITfLangBarItemMgr> bar;
                if (SUCCEEDED(thread_mgr_->QueryInterface(IID_PPV_ARGS(&bar)))) bar->RemoveItem(button_.Get());
            }
        }
        if (button_) button_->detach();
        button_.Reset();
        button_added_ = false;
        tm_cookie_ = focus_cookie_ = TF_INVALID_COOKIE;
        sent_range_.Reset();
        sent_context_.Reset();
        cand_win_.reset();
        hint_win_.reset();
        tip_win_.reset();
        mode_win_.reset();
        keyboard_.reset();
        thread_mgr_.Reset();
        client_id_ = TF_CLIENTID_NULL;
    } catch (...) {
    }
    return S_OK;
}

// ---- focus --------------------------------------------------------------------------------

STDMETHODIMP TextService::OnSetFocus(ITfDocumentMgr* focus, ITfDocumentMgr*) {
    // another text field: what was being typed stays where it is
    try {
        if (focus && !first_focus_at_) first_focus_at_ = GetTickCount();
        if (composition_ && comp_context_) {
            ComPtr<ITfContext> ctx = comp_context_;
            edit(ctx.Get(), [this](TfEditCookie ec) {
                if (composition_) composition_->EndComposition(ec);
            });
            composition_.Reset();
            comp_context_.Reset();
            reset_engine();
        }
        sent_range_.Reset();
        sent_context_.Reset();
        hide_ui();
    } catch (...) {
    }
    return S_OK;
}

STDMETHODIMP TextService::OnKillThreadFocus() {
    try {
        hide_ui();
        if (state_.symbol_page >= 0) close_symbols();  // the user went to another window
    } catch (...) {
    }
    return S_OK;
}

STDMETHODIMP TextService::OnCompositionTerminated(TfEditCookie, ITfComposition*) {
    // the app ended it (a click elsewhere...): start over
    try {
        composition_.Reset();
        comp_context_.Reset();
        reset_engine();
        hide_ui();
    } catch (...) {
    }
    return S_OK;
}

void TextService::reset_engine() {
    Json req = Json::object();
    req["op"] = Json("reset");
    Json reply;
    if (ask(req, reply)) read_reply(reply);
    else state_ = State{};
}

// ---- keys -------------------------------------------------------------------------------------

STDMETHODIMP TextService::OnTestKeyDown(ITfContext* ctx, WPARAM wp, LPARAM lp, BOOL* eaten) {
    *eaten = FALSE;
    try {
        *eaten = test_key_down(ctx, wp, lp);
    } catch (...) {
    }
    return S_OK;
}

STDMETHODIMP TextService::OnKeyDown(ITfContext* ctx, WPARAM wp, LPARAM lp, BOOL* eaten) {
    *eaten = FALSE;
    try {
        *eaten = key_down(ctx, wp, lp);
    } catch (...) {
    }
    return S_OK;
}

STDMETHODIMP TextService::OnTestKeyUp(ITfContext*, WPARAM wp, LPARAM, BOOL* eaten) {
    *eaten = FALSE;  // key-ups go on to the app (an eaten Shift-up left apps thinking Shift was held)
    try {
        key_up(wp);
    } catch (...) {
    }
    return S_OK;
}

STDMETHODIMP TextService::OnKeyUp(ITfContext*, WPARAM wp, LPARAM, BOOL* eaten) {
    *eaten = FALSE;
    try {
        key_up(wp);
    } catch (...) {
    }
    return S_OK;
}

bool TextService::other_modifier() const {
    // Ctrl+Shift / Alt+Shift switch keyboards, Win+Shift+S takes a screenshot...: no Shift tap
    return down(VK_CONTROL) || down(VK_MENU) || down(VK_LWIN) || down(VK_RWIN);
}

bool TextService::is_width_key(WPARAM vk) const {
    // Shift+Space switches 全形／半形
    return vk == VK_SPACE && down(VK_SHIFT) && !other_modifier();
}

bool TextService::wants_undo(WPARAM) const {
    // Shift+Backspace right after text was sent: like the ASUS IME, nothing special
    // (the app deletes a character). undo() is kept for a setting later.
    return false;
}

bool TextService::direct() {
    // 遊戲模式: in a game keys go straight through, unless Shift was tapped to type in its chat
    return in_game() && !game_typing_;
}

bool TextService::just_started() const {
    // this program started less than 2 minutes ago
    FILETIME created, exited, kernel, user, now;
    if (!GetProcessTimes(GetCurrentProcess(), &created, &exited, &kernel, &user)) return false;
    GetSystemTimeAsFileTime(&now);
    auto ticks = [](const FILETIME& f) { return (static_cast<ULONGLONG>(f.dwHighDateTime) << 32) | f.dwLowDateTime; };
    return ticks(now) - ticks(created) < 120ULL * 10000000;  // (100 ns units)
}

bool TextService::in_game() {
    if (!cfg_.game_mode) return false;
    ULONGLONG now = GetTickCount64();
    // the games Windows knows (Game Bar's list: HKCU\System\GameConfigStore\Children\*\MatchedExeFullPath).
    // Read on a key press, never on a timer: once, and again only while this program has just started
    // (Windows adds a game as it starts the first time): every 10 s for its first 2 minutes, then never
    if (!known_game_ && (!games_read_at_ || (now - games_read_at_ > 10000 && just_started()))) {
        games_read_at_ = now;
        HKEY root;
        if (RegOpenKeyExW(HKEY_CURRENT_USER, L"System\\GameConfigStore\\Children", 0, KEY_READ, &root) == ERROR_SUCCESS) {
            wchar_t name[256];
            for (DWORD i = 0; !known_game_; ++i) {
                DWORD len = static_cast<DWORD>(std::size(name));
                if (RegEnumKeyExW(root, i, name, &len, nullptr, nullptr, nullptr, nullptr) != ERROR_SUCCESS) break;
                wchar_t exe[MAX_PATH * 2];
                DWORD size = sizeof(exe);
                if (RegGetValueW(root, name, L"MatchedExeFullPath", RRF_RT_REG_SZ, nullptr, exe, &size) != ERROR_SUCCESS) continue;
                CharLowerBuffW(exe, static_cast<DWORD>(wcslen(exe)));
                known_game_ = exe_path_ == exe;
            }
            RegCloseKey(root);
        }
        if (known_game_) debug("game mode: a game Windows knows: " + app_);
    }
    if (known_game_) return true;
    // a Direct3D program in exclusive full screen, and it is this one
    if (now - full_screen_at_ > 1000) {
        full_screen_at_ = now;
        QUERY_USER_NOTIFICATION_STATE q{};
        DWORD pid = 0;
        GetWindowThreadProcessId(GetForegroundWindow(), &pid);
        full_screen_ = SUCCEEDED(SHQueryUserNotificationState(&q)) && q == QUNS_RUNNING_D3D_FULL_SCREEN &&
                       pid == GetCurrentProcessId();
    }
    return full_screen_;
}

wchar_t TextService::char_of(WPARAM vk, LPARAM lp) const {
    BYTE ks[256];
    if (!GetKeyboardState(ks)) return 0;
    ks[VK_CONTROL] = ks[VK_LCONTROL] = ks[VK_RCONTROL] = 0;
    ks[VK_MENU] = ks[VK_LMENU] = ks[VK_RMENU] = 0;
    wchar_t buf[4];
    UINT scan = (lp >> 16) & 0xFF;
    int n = ToUnicodeEx(static_cast<UINT>(vk), scan, ks, buf, 4, 0x4 /* keep the keyboard state */,
                        GetKeyboardLayout(0));
    return n == 1 ? buf[0] : 0;
}

bool TextService::no_text_field(ITfContext* ctx) {
    // Keys only go to the input method where text can be typed. Not when:
    // - no text field has the focus (an app that turned the input method off for a window,
    //   a game...): no document, or no context in it
    // - the focus is on a web page itself, not a text box (YouTube's J K L, Gmail's shortcuts,
    //   a password box): Chrome, Edge, Brave and Firefox mark that context disabled / empty
    // - the text is read-only
    // - a password box that only says so (see password_field)
    static std::string last;  // (debug: which kind of field, when it changes; never the keys)
    auto note = [](const std::string& kind) {
        if (kind != last) debug(kind);
        last = kind;
    };
    ComPtr<ITfContext> top;
    bool given = ctx != nullptr;
    if (!ctx) {
        ComPtr<ITfDocumentMgr> doc;
        if (!thread_mgr_ || FAILED(thread_mgr_->GetFocus(&doc)) || !doc) return note("no field: no document"), true;
        if (FAILED(doc->GetTop(&top)) || !top) return note("no field: no context"), true;
        ctx = top.Get();
    }
    int flags[2] = {0, 0};  // disabled, empty
    ComPtr<ITfCompartmentMgr> mgr;
    if (SUCCEEDED(ctx->QueryInterface(IID_PPV_ARGS(&mgr)))) {
        const GUID* guids[2] = {&GUID_COMPARTMENT_KEYBOARD_DISABLED, &GUID_COMPARTMENT_EMPTYCONTEXT};
        for (int k = 0; k < 2; ++k) {
            ComPtr<ITfCompartment> comp;
            VARIANT v;
            VariantInit(&v);
            if (SUCCEEDED(mgr->GetCompartment(*guids[k], &comp)) && SUCCEEDED(comp->GetValue(&v)) && v.vt == VT_I4)
                flags[k] = v.lVal;
        }
    }
    TF_STATUS status{};
    bool readonly = SUCCEEDED(ctx->GetStatus(&status)) && (status.dwDynamicFlags & TF_SD_READONLY);
    bool password = !flags[0] && !flags[1] && !readonly && password_field(ctx);
    note("field: given=" + std::to_string(given) + " disabled=" + std::to_string(flags[0]) + " empty=" +
         std::to_string(flags[1]) + " readonly=" + std::to_string(readonly) + " password=" +
         std::to_string(password) + " static=" + std::to_string(status.dwStaticFlags));
    return flags[0] || flags[1] || readonly || password;
}

bool TextService::password_field(ITfContext* ctx) {
    // A password box that says so (input scope IS_PASSWORD) but leaves the input method on
    // (some WinUI / UWP password boxes, apps that draw their own): nothing is composed or
    // learned there, like the Microsoft IME. Read at once or not at all: a session run later
    // would come after this key (then it counts as an ordinary field).
    bool password = false;
    auto* session = new EditSession([&](TfEditCookie ec) {
        TF_SELECTION sel{};
        ULONG n = 0;
        if (FAILED(ctx->GetSelection(ec, TF_DEFAULT_SELECTION, 1, &sel, &n)) || !n || !sel.range) return;
        ComPtr<ITfRange> range;
        range.Attach(sel.range);
        ComPtr<ITfReadOnlyProperty> prop;
        // GUID_PROP_INPUTSCOPE (InputScope.h declares it; no library defines it), a property
        // the app gives (GetAppProperty), not one of the input methods' (GetProperty)
        static const GUID prop_input_scope = {0x1713dd5a, 0x68e7, 0x4a5b, {0x9a, 0xf6, 0x59, 0x2a, 0x59, 0x5c, 0x77, 0x8d}};
        if (FAILED(ctx->GetAppProperty(prop_input_scope, &prop)) || !prop) return;
        VARIANT v;
        VariantInit(&v);
        if (FAILED(prop->GetValue(ec, range.Get(), &v))) return;
        ComPtr<ITfInputScope> scope;
        if (v.vt == VT_UNKNOWN && v.punkVal && SUCCEEDED(v.punkVal->QueryInterface(IID_PPV_ARGS(&scope)))) {
            InputScope* scopes = nullptr;
            UINT count = 0;
            if (SUCCEEDED(scope->GetInputScopes(&scopes, &count)) && scopes) {
                password = std::find(scopes, scopes + count, IS_PASSWORD) != scopes + count;
                CoTaskMemFree(scopes);
            }
        }
        VariantClear(&v);
    });
    HRESULT hr_session = E_FAIL;
    ctx->RequestEditSession(client_id_, session, TF_ES_READ | TF_ES_SYNC, &hr_session);
    session->Release();
    return password;
}

bool TextService::test_key_down(ITfContext* ctx, WPARAM vk, LPARAM lp) {
    if (direct()) {
        // in a game: only a Shift tap matters (it starts typing in the game's chat, see key_up)
        if (vk == VK_SHIFT && cfg_.shift_toggle && !other_modifier()) {
            if (!shift_alone_) {
                shift_alone_ = true;
                shift_down_at_ = GetTickCount64();
            }
        } else {
            shift_alone_ = false;
        }
        return false;
    }
    if (!get_compartment(GUID_COMPARTMENT_KEYBOARD_OPENCLOSE)) return false;  // keyboard turned off
    ULONGLONG now = GetTickCount64();
    if (vk == VK_CONTROL && !down(VK_SHIFT) && !down(VK_MENU)) {
        if (!ctrl_alone_) {  // (not auto-repeat)
            ctrl_alone_ = true;
            ctrl_down_at_ = now;
        }
    } else {
        ctrl_alone_ = false;
        ctrl_tapped_at_ = 0;
    }
    if (undoing_) {
        if (vk == VK_BACK) return false;     // ours: let the app delete the sent text
        if (vk == UNDO_MARK) return true;    // all deleted: show the text again
    }
    if (wants_undo(vk)) return true;
    if (vk != VK_SHIFT) state_.can_undo = false;  // the caret may have moved on
    // a Shift tap (down and quickly up, nothing else pressed) switches Chinese/English
    if (vk == VK_SHIFT && cfg_.shift_toggle && !other_modifier()) {
        if (!shift_alone_) {  // ignore auto-repeat while Shift is held
            shift_alone_ = true;
            shift_down_at_ = now;
        }
    } else {
        shift_alone_ = false;
    }
    if (down(VK_CONTROL) || down(VK_MENU)) {
        // Ctrl + , . ; ' [ ] ...: their full-width marks, if that setting is on (not in 英 mode, nor
        // where nothing can be typed); every other combination is the app's shortcut (Ctrl+C, Alt+Tab...)
        return ctrl_punct_key(ctx, vk, lp) && !english_ &&
               !(state_.composition.empty() && state_.symbol_page < 0 && no_text_field(ctx));
    }
    // (a Shift tap still switches Chinese/English there)
    if (state_.composition.empty() && state_.symbol_page < 0 && no_text_field(ctx)) return false;
    if (is_width_key(vk)) return true;
    bool composing = !state_.composition.empty();
    if (english_) {
        // text typed before switching still has to go out: the next key does it;
        // in 全形 mode every character is ours to type in full width
        return vk != VK_SHIFT && (composing || (cfg_.full_width && printable(char_of(vk, lp))));
    }
    if (state_.symbol_page >= 0) return vk != VK_SHIFT;  // every key means a symbol while the keyboard is up
    if (composing) return vk != VK_SHIFT;
    if (is_numpad(vk)) return false;  // numpad always types plain digits
    wchar_t ch = char_of(vk, lp);
    if (ch == L' ') return cfg_.space == "full" || cfg_.full_width;  // a full-width space is ours to type
    return printable(ch);
}

bool TextService::ctrl_punct_key(ITfContext* ctx, WPARAM vk, LPARAM lp) {
    // the keys with a Chinese mark: the engine's PUNCT_KEYS and PUNCT (engine/composer.py)
    static const wchar_t KEYS[] = L",.;<>?!:\"'[]{}()\\|_^";
    // ...but in a terminal Ctrl + [ ] \ ^ _ are control characters (Ctrl+[ is Esc, Ctrl+] telnet's escape)
    static const wchar_t CONTROL_KEYS[] = L"[]\\^_";
    if (!cfg_.ctrl_punct || !down(VK_CONTROL) || down(VK_MENU)) return false;
    wchar_t ch = char_of(vk, lp);
    if (!ch || !wcschr(KEYS, ch)) return false;
    return !(wcschr(CONTROL_KEYS, ch) && in_terminal(ctx));
}

bool TextService::key_down(ITfContext* ctx, WPARAM vk, LPARAM lp) {
    if (!test_key_down(ctx, vk, lp)) return false;
    if (ctrl_punct_key(ctx, vk, lp)) {
        Json req = Json::object();
        req["op"] = Json("ctrl_punct");
        req["ch"] = Json(narrow(std::wstring(1, char_of(vk, lp))));
        call(ctx, req);
        return true;
    }
    if (undoing_ && vk == UNDO_MARK) {
        undoing_ = false;
        sync(ctx, L"");
        return true;
    }
    if (wants_undo(vk)) return undo(ctx) || undo_by_keys();
    if (vk == VK_BACK && down(VK_SHIFT))
        debug("no undo: can_undo=" + std::to_string(state_.can_undo) + " range=" + std::to_string(sent_range_ != nullptr) +
              " composing=" + std::to_string(!state_.composition.empty()));
    Json req = Json::object();
    if (is_width_key(vk)) {
        req["op"] = Json("full_width");
        call(ctx, req);
        show_mode(cfg_.full_width ? L"全形" : L"半形");
        return true;
    }
    wchar_t ch = char_of(vk, lp);
    if (english_) {
        // first key after switching to English with text still being typed:
        // send that text, plus the character of this key
        req["op"] = Json("flush");
        req["esc"] = Json(vk == VK_ESCAPE);
        if (vk != VK_RETURN && printable(ch)) req["ch"] = Json(narrow(std::wstring(1, ch)));
        call(ctx, req);
        return true;
    }
    bool picking = !state_.candidates.empty() && vk >= VK_NUMPAD0 && vk <= VK_NUMPAD9;
    if (is_numpad(vk) && !picking && !state_.composition.empty() && printable(ch)) {
        // numpad keys join the text being typed (as themselves, never zhuyin);
        // with the candidate list up, its digits pick like the top-row ones
        req["op"] = Json("literal");
        req["ch"] = Json(narrow(std::wstring(1, ch)));
        call(ctx, req);
        return true;
    }
    const char* special = special_of(vk);
    req["op"] = Json("key");
    if (special) {
        req["special"] = Json(std::string(vk == VK_TAB && down(VK_SHIFT) ? "STAB" : special));
    } else if (printable(ch)) {
        req["ch"] = Json(narrow(std::wstring(1, ch)));
        req["caps"] = Json((GetKeyState(VK_CAPITAL) & 1) != 0);
    } else {
        return !state_.composition.empty();  // swallow other keys mid-composition
    }
    return call(ctx, req);
}

void TextService::key_up(WPARAM vk) {
    ULONGLONG now = GetTickCount64();
    // switch right here and let the Shift key-up go on to the app
    if (vk == VK_SHIFT && shift_alone_ && now - shift_down_at_ < SHIFT_TAP_MS && !other_modifier()) {
        shift_alone_ = false;
        if (in_game() && state_.composition.empty() && state_.symbol_page < 0) {
            // in a game: typing in its chat, or keys straight through again. Nothing shown at
            // the caret: a window over a full-screen game can make it flicker or drop out of full screen
            game_typing_ = !game_typing_;
            debug(std::string("game mode: ") + (game_typing_ ? "typing" : "keys straight through"));
        } else {
            toggle_mode();
        }
    }
    shift_alone_ = false;
    if (vk == VK_CONTROL && ctrl_alone_ && cfg_.lookup) {
        if (now - ctrl_down_at_ < SHIFT_TAP_MS) {
            if (ctrl_tapped_at_ && now - ctrl_tapped_at_ < DOUBLE_CTRL_MS) {
                ctrl_tapped_at_ = 0;
                open_lookup();
            } else {
                ctrl_tapped_at_ = now;
            }
        }
    }
    ctrl_alone_ = false;
}

// ---- talking to the engine ---------------------------------------------------------------

bool TextService::ask(const Json& request, Json& reply) {
    return engine_.call(request, reply) && reply.is_object() && !reply.get("error");
}

void TextService::read_reply(const Json& r) {
    auto str = [&](const char* k) {
        auto* v = r.get(k);
        return v && v->is_string() ? v->as_string() : std::string();
    };
    auto num = [&](const char* k, int def) {
        auto* v = r.get(k);
        return v && v->is_number() ? static_cast<int>(v->as_number()) : def;
    };
    State s;
    s.composition = wide(str("composition"));
    s.cursor = utf16_offset(s.composition, num("cursor", 0));
    if (auto* c = r.get("candidates"); c && c->is_array())
        for (auto& item : c->items())
            if (item.is_string()) s.candidates.push_back(wide(item.as_string()));
    s.cand_index = num("cand_index", 0);
    s.cand_page = num("cand_page", 0);
    s.cand_pages = num("cand_pages", 1);
    if (auto* e = r.get("cand_expanded"); e && e->is_bool() && e->as_bool()) {
        s.cand_expanded = true;
        if (auto* g = r.get("cand_grid"); g && g->is_array())
            for (auto& item : g->items())
                if (item.is_string()) s.cand_grid.push_back(wide(item.as_string()));
        s.cand_grid_index = num("cand_grid_index", 0);
        s.cand_first = num("cand_first", 0);
    }
    s.hint = wide(str("hint"));
    s.zhuyin_tip = wide(str("zhuyin_tip"));
    s.zhuyin_tip_at = utf16_offset(s.composition, num("zhuyin_tip_at", 0));
    if (auto* rp = r.get("replacements"); rp && rp->is_array())
        for (auto& item : rp->items())
            if (item.is_string()) s.replacements.push_back(wide(item.as_string()));
    s.repl_index = num("repl_index", -1);
    s.symbol_page = num("symbol_page", -1);
    s.symbol_cursor = num("symbol_cursor", 0);
    auto* u = r.get("can_undo");
    s.can_undo = u && u->is_bool() && u->as_bool();
    state_ = std::move(s);
    if (auto* c = r.get("cfg"); c && c->is_object()) {
        auto flag = [&](const char* k, bool def) {
            auto* v = c->get(k);
            return v && v->is_bool() ? v->as_bool() : def;
        };
        auto text = [&](const char* k, const char* def) {
            auto* v = c->get(k);
            return v && v->is_string() ? v->as_string() : std::string(def);
        };
        auto number = [&](const char* k, int def) {
            auto* v = c->get(k);
            return v && v->is_number() ? static_cast<int>(v->as_number()) : def;
        };
        cfg_.shift_toggle = flag("shift_toggle", true);
        cfg_.start_mode = text("start_mode", "mixed");
        cfg_.lookup = flag("lookup", true);
        cfg_.space = text("space", "half");
        cfg_.full_width = flag("full_width", false);
        cfg_.ctrl_punct = flag("ctrl_punct", false);
        cfg_.cand_font = wide(text("cand_font", "Microsoft JhengHei UI"));
        cfg_.cand_font_size = number("cand_font_size", 18);
        cfg_.cand_per_row = number("cand_per_row", 1);
        cfg_.game_mode = flag("game_mode", false);
    }
}

bool TextService::call(ITfContext* ctx, Json request) {
    Json reply;
    if (!ask(request, reply)) {
        // the engine is gone: leave what is on screen and let keys through
        if (composition_ && comp_context_) {
            ComPtr<ITfContext> c = comp_context_;
            edit(c.Get(), [this](TfEditCookie ec) {
                if (composition_) composition_->EndComposition(ec);
            });
        }
        composition_.Reset();
        comp_context_.Reset();
        state_ = State{};
        hide_ui();
        return false;
    }
    read_reply(reply);
    auto* commit = reply.get("commit");
    sync(ctx, commit && commit->is_string() ? wide(commit->as_string()) : std::wstring());
    auto* handled = reply.get("handled");
    return !handled || !handled->is_bool() || handled->as_bool();
}

void TextService::sync(ITfContext* ctx, const std::wstring& commit) {
    if (!ctx) return;
    ComPtr<ITfContext> c(ctx);
    edit(ctx, [this, c, commit](TfEditCookie ec) {
        set_text(ec, c.Get(), commit);
        place_ui(ec, c.Get());
    });
}

bool TextService::undo(ITfContext* ctx) {
    // 拿回重選: the text just sent becomes the composition again, right where it is
    auto done = std::make_shared<bool>(false);
    ComPtr<ITfContext> c(ctx);
    HRESULT hr = edit(ctx, [this, c, done](TfEditCookie ec) {
        if (!sent_range_ || sent_context_.Get() != c.Get()) {
            debug("undo: no range or other context");
            return;
        }
        // still there, right before the caret?
        TF_SELECTION sel{};
        ULONG fetched = 0;
        if (FAILED(c->GetSelection(ec, TF_DEFAULT_SELECTION, 1, &sel, &fetched)) || !fetched) return;
        ComPtr<ITfRange> caret;
        caret.Attach(sel.range);
        // the text just before the caret must still be what was sent (ranges kept from
        // then may have collapsed: some apps rebuild their text)
        ComPtr<ITfRange> sent;
        if (FAILED(caret->Clone(&sent))) return;
        sent->Collapse(ec, TF_ANCHOR_START);
        LONG want = static_cast<LONG>(sent_text_.size()), moved = 0;
        sent->ShiftStart(ec, -want, &moved, nullptr);
        std::wstring buf(sent_text_.size() + 1, L'\0');
        ULONG got = 0;
        sent->GetText(ec, 0, buf.data(), static_cast<ULONG>(buf.size()), &got);
        if (moved != -want || std::wstring(buf.data(), got) != sent_text_) {
            ComPtr<ITfRange> all;
            LONG back = 0;
            std::wstring doc(200, L' ');
            ULONG n = 0;
            if (SUCCEEDED(c->GetStart(ec, &all)) && all) {
                ComPtr<ITfRange> end;
                c->GetEnd(ec, &end);
                if (end) all->ShiftEndToRange(ec, end.Get(), TF_ANCHOR_END);
                all->GetText(ec, 0, doc.data(), 200, &n);
                LONG cmp = 9;
                caret->CompareStart(ec, all.Get(), TF_ANCHOR_START, &cmp);
                back = cmp;
            }
            // (lengths only: the log never holds what was typed)
            debug("undo: text differs: " + std::to_string(got) + " vs " + std::to_string(sent_text_.size()) +
                  " characters, moved=" + std::to_string(moved) + " back=" + std::to_string(back) + " before=" +
                  std::to_string(n));
            return;
        }
        Json req = Json::object();
        req["op"] = Json("undo");
        Json reply;
        if (!ask(req, reply)) return;
        read_reply(reply);
        if (state_.composition.empty()) return;
        if (composition_) end_composition(ec);
        if (!start_composition(ec, c.Get(), sent.Get())) return;
        sent_range_.Reset();
        sent_context_.Reset();
        set_text(ec, c.Get(), L"");
        place_ui(ec, c.Get());
        *done = true;
    });
    (void)hr;
    if (!*done) {
        state_.can_undo = false;
        sent_range_.Reset();
    }
    return *done;
}

bool TextService::undo_by_keys() {
    // The app shows TSF only the text being typed (older apps, through the IMM32
    // layer): the engine takes the text back, Backspaces delete it from the app,
    // and the composition comes back once the mark key arrives after them.
    Json req = Json::object();
    req["op"] = Json("undo");
    Json reply;
    if (!ask(req, reply)) return false;
    auto* d = reply.get("deleted");
    std::string deleted = d && d->is_string() ? d->as_string() : std::string();
    read_reply(reply);
    if (deleted.empty()) return false;
    std::vector<INPUT> in;
    size_t count = from_utf8(deleted).size();
    for (size_t k = 0; k <= count; ++k) {
        WORD vk = k < count ? VK_BACK : UNDO_MARK;
        for (DWORD flags : {0UL, static_cast<DWORD>(KEYEVENTF_KEYUP)}) {
            INPUT i{};
            i.type = INPUT_KEYBOARD;
            i.ki.wVk = vk;
            i.ki.dwFlags = flags;
            in.push_back(i);
        }
    }
    undoing_ = SendInput(static_cast<UINT>(in.size()), in.data(), sizeof(INPUT)) == in.size();
    if (!undoing_) reset_engine();  // nothing was deleted: drop the attempt
    sent_range_.Reset();
    return true;
}

// ---- inside edit sessions -----------------------------------------------------------------

HRESULT TextService::edit(ITfContext* ctx, std::function<void(TfEditCookie)> fn, bool write) {
    if (!ctx) return E_INVALIDARG;
    auto* session = new EditSession(std::move(fn));
    HRESULT hr_session = E_FAIL;
    DWORD rw = write ? TF_ES_READWRITE : TF_ES_READ;
    HRESULT hr = ctx->RequestEditSession(client_id_, session, rw | TF_ES_SYNC, &hr_session);
    if (FAILED(hr) || hr_session == TF_E_SYNCHRONOUS)  // not allowed right now: as soon as it is
        hr = ctx->RequestEditSession(client_id_, session, rw | TF_ES_ASYNCDONTCARE, &hr_session);
    session->Release();
    return FAILED(hr) ? hr : hr_session;
}

bool TextService::start_composition(TfEditCookie ec, ITfContext* ctx, ITfRange* where) {
    ComPtr<ITfRange> range;
    if (where) {
        where->Clone(&range);
    } else {
        ComPtr<ITfInsertAtSelection> ins;
        if (FAILED(ctx->QueryInterface(IID_PPV_ARGS(&ins)))) return false;
        if (FAILED(ins->InsertTextAtSelection(ec, TF_IAS_QUERYONLY, nullptr, 0, &range)) || !range) return false;
    }
    ComPtr<ITfContextComposition> cc;
    if (FAILED(ctx->QueryInterface(IID_PPV_ARGS(&cc)))) return false;
    ComPtr<ITfComposition> comp;
    if (FAILED(cc->StartComposition(ec, range.Get(), static_cast<ITfCompositionSink*>(this), &comp)) || !comp)
        return false;
    composition_ = comp;
    comp_context_ = ctx;
    last_end_ok_ = last_tip_ok_ = false;
    watch_layout(ctx);
    return true;
}

void TextService::watch_layout(ITfContext* ctx) {
    if (layout_context_.Get() == ctx) return;
    if (layout_context_ && layout_cookie_ != TF_INVALID_COOKIE) {
        ComPtr<ITfSource> source;
        if (SUCCEEDED(layout_context_->QueryInterface(IID_PPV_ARGS(&source)))) source->UnadviseSink(layout_cookie_);
    }
    layout_context_.Reset();
    layout_cookie_ = TF_INVALID_COOKIE;
    if (!ctx) return;
    ComPtr<ITfSource> source;
    if (SUCCEEDED(ctx->QueryInterface(IID_PPV_ARGS(&source))) &&
        SUCCEEDED(source->AdviseSink(IID_ITfTextLayoutSink, static_cast<ITfTextLayoutSink*>(this), &layout_cookie_)))
        layout_context_ = ctx;
    else
        layout_cookie_ = TF_INVALID_COOKIE;
}

STDMETHODIMP TextService::OnLayoutChange(ITfContext* ctx, TfLayoutCode code, ITfContextView*) {
    // the text being typed was drawn (or moved): put the boxes where it is now
    try {
        if (code == TF_LC_DESTROY) {
            watch_layout(nullptr);
        } else if (composition_ && ctx && comp_context_.Get() == ctx) {
            ComPtr<ITfContext> c = ctx;
            edit(c.Get(), [this, c](TfEditCookie ec) {
                if (composition_) place_ui(ec, c.Get());
            }, false);
        }
    } catch (...) {
    }
    return S_OK;
}

void TextService::end_composition(TfEditCookie ec) {
    if (!composition_) return;
    ComPtr<ITfRange> r;
    if (SUCCEEDED(composition_->GetRange(&r)) && r) {
        if (comp_context_) set_attribute(ec, comp_context_.Get(), r.Get(), false);
        r->SetText(ec, 0, L"", 0);
    }
    composition_->EndComposition(ec);
    composition_.Reset();
    comp_context_.Reset();
}

void TextService::set_attribute(TfEditCookie ec, ITfContext* ctx, ITfRange* range, bool on) {
    ComPtr<ITfProperty> prop;
    if (FAILED(ctx->GetProperty(GUID_PROP_ATTRIBUTE, &prop))) return;
    if (on && attr_atom_ != TF_INVALID_GUIDATOM) {
        VARIANT v;
        VariantInit(&v);
        v.vt = VT_I4;
        v.lVal = static_cast<LONG>(attr_atom_);
        prop->SetValue(ec, range, &v);
    } else {
        prop->Clear(ec, range);
    }
}

void TextService::set_caret(TfEditCookie ec, ITfContext* ctx, ITfRange* range, LONG offset) {
    ComPtr<ITfRange> r;
    if (FAILED(range->Clone(&r))) return;
    r->Collapse(ec, TF_ANCHOR_START);
    LONG moved = 0;
    if (offset > 0) r->ShiftEnd(ec, offset, &moved, nullptr);
    r->Collapse(ec, TF_ANCHOR_END);
    TF_SELECTION sel{};
    sel.range = r.Get();
    sel.style.ase = TF_AE_NONE;
    sel.style.fInterimChar = FALSE;
    ctx->SetSelection(ec, 1, &sel);
}

void TextService::set_text(TfEditCookie ec, ITfContext* ctx, const std::wstring& commit) {
    if (comp_context_ && comp_context_.Get() != ctx) {  // (left behind in another field)
        composition_.Reset();
        comp_context_.Reset();
    }
    if (!commit.empty()) {
        ComPtr<ITfRange> r;
        LONG len = static_cast<LONG>(commit.size());
        if (composition_) {
            composition_->GetRange(&r);
            if (r) {
                r->SetText(ec, 0, commit.c_str(), len);
                set_attribute(ec, ctx, r.Get(), false);
                set_caret(ec, ctx, r.Get(), len);
            }
            composition_->EndComposition(ec);
            composition_.Reset();
            comp_context_.Reset();
        } else {
            ComPtr<ITfInsertAtSelection> ins;
            if (SUCCEEDED(ctx->QueryInterface(IID_PPV_ARGS(&ins))) &&
                SUCCEEDED(ins->InsertTextAtSelection(ec, 0, commit.c_str(), len, &r)) && r)
                set_caret(ec, ctx, r.Get(), len);
        }
        if (r) {  // remembered for 拿回重選
            sent_range_ = r;
            sent_context_ = ctx;
            sent_text_ = commit;
            sent_at_ = GetTickCount64();
        }
    }
    if (!state_.composition.empty()) {
        if (!composition_ && !start_composition(ec, ctx)) return;
        ComPtr<ITfRange> r;
        if (FAILED(composition_->GetRange(&r)) || !r) return;
        r->SetText(ec, TF_ST_CORRECTION, state_.composition.c_str(), static_cast<LONG>(state_.composition.size()));
        set_attribute(ec, ctx, r.Get(), true);
        set_caret(ec, ctx, r.Get(), state_.cursor);
    } else if (composition_) {
        end_composition(ec);
    }
}

bool TextService::text_rect(TfEditCookie ec, ITfContext* ctx, ITfRange* range, RECT& rc) {
    ComPtr<ITfContextView> view;
    if (FAILED(ctx->GetActiveView(&view)) || !view) return false;
    BOOL clipped = FALSE;
    rc = RECT{};
    if (FAILED(view->GetTextExt(ec, range, &rc, &clipped))) return false;
    return rc.bottom > rc.top;
}

bool TextService::caret_rect(ITfContext*, RECT& rc) {
    // apps that tell TSF nothing: the system caret, else the bottom of the window
    GUITHREADINFO gti{sizeof gti};
    if (GetGUIThreadInfo(GetCurrentThreadId(), &gti) && gti.hwndCaret) {
        rc = gti.rcCaret;
        MapWindowPoints(gti.hwndCaret, nullptr, reinterpret_cast<POINT*>(&rc), 2);
        if (rc.bottom > rc.top) return true;
    }
    HWND fg = GetForegroundWindow();
    if (fg && GetWindowRect(fg, &rc)) {
        rc.left += 40;
        rc.top = rc.bottom - 80;
        rc.bottom -= 60;
        rc.right = rc.left + 1;
        return true;
    }
    return false;
}

namespace {
// terminals draw the text being typed themselves and tell TSF only the cell it starts at
const wchar_t* const TERMINAL_CLASSES[] = {L"ConsoleWindowClass", L"CASCADIA_HOSTING_WINDOW_CLASS", L"mintty",
                                           L"VirtualConsoleClass", L"PuTTY", L"Alacritty", L"org.wezfurlong.wezterm"};

bool wide_char(wchar_t c) {  // two terminal cells: Chinese, zhuyin, full-width forms
    return (c >= 0x1100 && c <= 0x115F) || (c >= 0x2E80 && c <= 0xA4CF) || (c >= 0xAC00 && c <= 0xD7A3) ||
           (c >= 0xD800 && c <= 0xDBFF) || (c >= 0xF900 && c <= 0xFAFF) || (c >= 0xFE30 && c <= 0xFE4F) ||
           (c >= 0xFF00 && c <= 0xFF60) || (c >= 0xFFE0 && c <= 0xFFE6);
}

int cells(const std::wstring& text, int upto) {  // terminal cells taken by text[0, upto)
    int n = 0;
    for (int i = 0; i < upto && i < static_cast<int>(text.size()); ++i) {
        if (text[i] >= 0xDC00 && text[i] <= 0xDFFF) continue;  // (the second half of a pair)
        n += wide_char(text[i]) ? 2 : 1;
    }
    return n;
}
}  // namespace

bool TextService::in_terminal(ITfContext* ctx) {
    ComPtr<ITfContextView> view;
    HWND hwnd = nullptr;
    if (SUCCEEDED(ctx->GetActiveView(&view)) && view) view->GetWnd(&hwnd);
    if (!hwnd) hwnd = GetForegroundWindow();
    HWND root = hwnd ? GetAncestor(hwnd, GA_ROOT) : nullptr;
    wchar_t cls[256] = L"";
    if (!root || !GetClassNameW(root, cls, 256)) return false;
    for (auto* t : TERMINAL_CLASSES)
        if (wcscmp(cls, t) == 0) return true;
    return false;
}

bool TextService::char_rect(TfEditCookie ec, ITfContext* ctx, int at, RECT& rc) {
    // character `at` of the text being typed, as the app reports it
    if (!composition_) return false;
    ComPtr<ITfRange> r, ch;
    if (FAILED(composition_->GetRange(&r)) || !r || FAILED(r->Clone(&ch))) return false;
    ch->Collapse(ec, TF_ANCHOR_START);
    LONG moved = 0;
    if (at) {
        ch->ShiftStart(ec, at, &moved, nullptr);
        if (moved != at) return false;
        ch->Collapse(ec, TF_ANCHOR_START);
    }
    ch->ShiftEnd(ec, 1, &moved, nullptr);
    // (older apps, through the IMM32 layer, report the line from there: no character is that wide)
    return moved && text_rect(ec, ctx, ch.Get(), rc) && rc.right - rc.left <= 2 * (rc.bottom - rc.top);
}

void TextService::place_ui(TfEditCookie ec, ITfContext* ctx) {
    RECT comp_rc{}, end_rc{};
    bool have_comp = false, have_end = false;
    if (composition_) {
        ComPtr<ITfRange> r;
        if (SUCCEEDED(composition_->GetRange(&r)) && r) {
            have_comp = text_rect(ec, ctx, r.Get(), comp_rc);
            // the last character: the hint goes right after it
            ComPtr<ITfRange> last;
            if (SUCCEEDED(r->Clone(&last))) {
                last->Collapse(ec, TF_ANCHOR_END);
                LONG moved = 0;
                last->ShiftStart(ec, -1, &moved, nullptr);
                have_end = moved != 0 && text_rect(ec, ctx, last.Get(), end_rc);
            }
        }
    }
    if (!have_comp && !caret_rect(ctx, comp_rc)) comp_rc = RECT{100, 100, 101, 120};
    if (!have_end) end_rc = comp_rc;
    // Older apps (through the IMM32 layer) report from the caret to the end of the line,
    // not the character: no character is that wide, so the caret is its left edge.
    bool line_rect = end_rc.right - end_rc.left > 2 * (end_rc.bottom - end_rc.top);
    if (line_rect) end_rc.right = end_rc.left;

    // Where character `at` of the text is. A terminal tells only the cell the text starts at
    // (whatever is asked), so it is worked out from there: Chinese and zhuyin take two cells.
    // Other apps may not know yet, the text just changed (Discord, browsers): then a box
    // stays where it was last put right, not back at the start of the text; the app says when
    // it knows (OnLayoutChange) and everything is put in place again.
    int len = static_cast<int>(state_.composition.size());
    bool terminal = composition_ && len && in_terminal(ctx);
    RECT cell{};
    bool have_cell = false;
    if (terminal) {
        ComPtr<ITfRange> r;
        have_cell = SUCCEEDED(composition_->GetRange(&r)) && r && text_rect(ec, ctx, r.Get(), cell);
    }
    auto rect_of = [&](int at, RECT& out) {
        if (at < 0 || at >= len) return false;
        if (!terminal) return char_rect(ec, ctx, at, out);
        if (!have_cell) return false;
        int h = cell.bottom - cell.top, cw = cell.right - cell.left;
        if (cw < h / 4 || cw > h) cw = std::max(1, h / 2);  // (one cell: about half as wide as high)
        out = cell;
        out.left = cell.left + cells(state_.composition, at) * cw;
        out.right = out.left + (wide_char(state_.composition[at]) ? 2 : 1) * cw;
        return true;
    };
    if (!len) last_end_ok_ = last_tip_ok_ = false;
    RECT rc{};
    bool exact_end = len && rect_of(len - 1, rc);
    if (exact_end) {
        end_rc = rc;
        last_end_rc_ = rc;
        last_end_ok_ = true;
        line_rect = false;
    } else if (last_end_ok_) {
        end_rc = last_end_rc_;
        exact_end = true;
    }
    debug("place_ui: comp=" + std::to_string(len) + " chars, hint=" + std::to_string(state_.hint.size()) +
          " chars, terminal=" + std::to_string(terminal) + " have_comp=" + std::to_string(have_comp) +
          " have_end=" + std::to_string(have_end) + " exact_end=" + std::to_string(exact_end) + " end=" +
          std::to_string(end_rc.left) + "," + std::to_string(end_rc.top) + "," + std::to_string(end_rc.right) + "," +
          std::to_string(end_rc.bottom));
    if (!state_.candidates.empty()) {
        if (!cand_win_) {
            cand_win_ = std::make_unique<CandidateWindow>();
            cand_win_->on_pick = [this](int i) { pick_candidate(i); };
            cand_win_->on_pick_at = [this](int i) { pick_candidate_at(i); };
            cand_win_->on_expand = [this] { candidates_op("expand"); };
            cand_win_->on_fold = [this] { candidates_op("fold"); };
        }
        if (state_.cand_expanded)
            cand_win_->show_grid(state_.cand_grid, state_.cand_grid_index, state_.cand_first, state_.cand_pages,
                                 cfg_.cand_font, cfg_.cand_font_size, comp_rc);
        else
            cand_win_->show(state_.candidates, state_.cand_index, state_.cand_page, state_.cand_pages,
                            cfg_.cand_font, cfg_.cand_font_size, cfg_.cand_per_row > 1, comp_rc);
    } else if (cand_win_) {
        cand_win_->hide();
    }
    if (!state_.replacements.empty() && state_.candidates.empty() && state_.symbol_page < 0) {
        // above the character after the caret
        RECT at_rc = comp_rc;
        if (rect_of(state_.cursor, rc)) at_rc = rc;
        if (!bar_win_) {
            bar_win_ = std::make_unique<ReplacementBar>();
            bar_win_->on_pick = [this](int i) { pick_replacement(i); };
        }
        bar_win_->show(state_.replacements, state_.repl_index, at_rc);
    } else if (bar_win_) {
        bar_win_->hide();
    }
    if (!state_.hint.empty() && state_.candidates.empty() && state_.symbol_page < 0) {
        if (!hint_win_) hint_win_ = std::make_unique<HintWindow>();
        hint_win_->show(state_.hint, end_rc, 0, line_rect || !exact_end);
    } else if (hint_win_) {
        hint_win_->hide();
    }
    if (!state_.zhuyin_tip.empty() && state_.candidates.empty() && state_.replacements.empty() &&
        state_.symbol_page < 0) {
        // under the keys of the unfinished syllable (their first character), like the ASUS IME
        RECT at_rc = end_rc;  // (an app that tells only the caret: under that)
        if (rect_of(state_.zhuyin_tip_at, rc)) {
            at_rc = rc;
            last_tip_rc_ = rc;
            last_tip_ok_ = true;
        } else if (last_tip_ok_) {
            at_rc = last_tip_rc_;
        }
        if (!tip_win_) tip_win_ = std::make_unique<ZhuyinTip>();
        tip_win_->show(state_.zhuyin_tip, at_rc);
    } else if (tip_win_) {
        tip_win_->hide();
    }
    if (state_.symbol_page >= 0) {
        if (!keyboard_) {
            keyboard_ = std::make_unique<SymbolKeyboard>();
            keyboard_->on_key = [this](char k) { symbol_key(k); };
            keyboard_->on_page = [this](int p) { symbol_page(p); };
            keyboard_->on_close = [this] { close_symbols(); };
        }
        keyboard_->show(state_.symbol_page, state_.symbol_cursor, comp_rc);
    } else if (keyboard_) {
        keyboard_->hide();
    }
}

void TextService::hide_ui() {
    if (cand_win_) cand_win_->hide();
    if (bar_win_) bar_win_->hide();
    if (hint_win_) hint_win_->hide();
    if (tip_win_) tip_win_->hide();
    if (keyboard_) keyboard_->hide();
}

// ---- mouse and menu -----------------------------------------------------------------------

namespace {
// the context with the focus, for edits not started by a key
ComPtr<ITfContext> focused_context(ITfThreadMgr* tm) {
    ComPtr<ITfDocumentMgr> doc;
    ComPtr<ITfContext> ctx;
    if (tm && SUCCEEDED(tm->GetFocus(&doc)) && doc) doc->GetTop(&ctx);
    return ctx;
}
}  // namespace

void TextService::pick_candidate(int index) {
    ComPtr<ITfContext> ctx = comp_context_ ? comp_context_ : focused_context(thread_mgr_.Get());
    if (!ctx || index < 0 || index > 8) return;
    Json req = Json::object();
    req["op"] = Json("key");
    req["ch"] = Json(std::string(1, static_cast<char>('1' + index)));
    call(ctx.Get(), req);
}

void TextService::pick_candidate_at(int index) {
    ComPtr<ITfContext> ctx = comp_context_ ? comp_context_ : focused_context(thread_mgr_.Get());
    if (!ctx || index < 0) return;
    Json req = Json::object();
    req["op"] = Json("pick_at");
    req["index"] = Json(index);
    call(ctx.Get(), req);
}

void TextService::candidates_op(const char* op) {
    // expand: as → would (the list spreads out); fold: back to the list
    ComPtr<ITfContext> ctx = comp_context_ ? comp_context_ : focused_context(thread_mgr_.Get());
    if (!ctx) return;
    Json req = Json::object();
    if (std::string(op) == "expand") {
        req["op"] = Json("key");
        req["special"] = Json("RIGHT");
    } else {
        req["op"] = Json("fold");
    }
    call(ctx.Get(), req);
}

void TextService::pick_replacement(int index) {
    // clicked: ↑ into the bar, → to it, Enter
    ComPtr<ITfContext> ctx = comp_context_ ? comp_context_ : focused_context(thread_mgr_.Get());
    if (!ctx || index < 0) return;
    Json req = Json::object();
    req["op"] = Json("key");
    if (state_.repl_index < 0) {
        req["special"] = Json("UP");
        call(ctx.Get(), req);
    }
    for (int k = std::max(state_.repl_index, 0); k < index; ++k) {
        req["special"] = Json("RIGHT");
        call(ctx.Get(), req);
    }
    for (int k = state_.repl_index; k > index; --k) {
        req["special"] = Json("LEFT");
        call(ctx.Get(), req);
    }
    req["special"] = Json("ENTER");
    call(ctx.Get(), req);
}

void TextService::symbol_key(char key) {
    ComPtr<ITfContext> ctx = focused_context(thread_mgr_.Get());
    if (!ctx) return;
    Json req = Json::object();
    req["op"] = Json("key");
    req["ch"] = Json(std::string(1, key));
    call(ctx.Get(), req);
}

void TextService::symbol_page(int page) {
    ComPtr<ITfContext> ctx = focused_context(thread_mgr_.Get());
    if (!ctx) return;
    int pages = 3;
    int steps = ((page - state_.symbol_page) % pages + pages) % pages;
    for (int k = 0; k < steps; ++k) {
        Json req = Json::object();
        req["op"] = Json("key");
        req["special"] = Json("PGDN");
        call(ctx.Get(), req);
    }
}

void TextService::close_symbols() {
    Json req = Json::object();
    req["op"] = Json("close_symbols");
    Json reply;
    if (ask(req, reply)) read_reply(reply);
    if (keyboard_) keyboard_->hide();
}

void TextService::show_menu(POINT pt) {
    if (!hint_win_) hint_win_ = std::make_unique<HintWindow>();
    HWND owner = hint_win_->hwnd();
    if (!owner) {  // (made on first use)
        RECT r{-100, -100, -99, -99};
        hint_win_->show(L" ", r);
        hint_win_->hide();
        owner = hint_win_->hwnd();
    }
    HMENU menu = CreatePopupMenu();
    AppendMenuW(menu, MF_STRING, 1, L"兩儀輸入法設定 (&S)");
    AppendMenuW(menu, MF_STRING, 2, english_ ? L"切換到中文 (&C)" : L"切換到英文 (&E)");
    int cmd = TrackPopupMenuEx(menu, TPM_RETURNCMD | TPM_NONOTIFY | TPM_RIGHTBUTTON, pt.x, pt.y, owner, nullptr);
    DestroyMenu(menu);
    if (cmd == 1) {
        open_settings();
    } else if (cmd == 2) {
        toggle_mode();
    }
}

// ---- Chinese / English --------------------------------------------------------------------

void TextService::show_mode(const std::wstring& text) {
    // a moment's 中／英／全形 box at the caret
    RECT rc;
    ComPtr<ITfContext> ctx = focused_context(thread_mgr_.Get());
    bool found = false;
    if (ctx) {
        auto out = std::make_shared<RECT>();
        auto ok = std::make_shared<bool>(false);
        ComPtr<ITfContext> c = ctx;
        edit(ctx.Get(), [this, c, out, ok](TfEditCookie ec) {
            TF_SELECTION sel{};
            ULONG fetched = 0;
            if (SUCCEEDED(c->GetSelection(ec, TF_DEFAULT_SELECTION, 1, &sel, &fetched)) && fetched) {
                ComPtr<ITfRange> r;
                r.Attach(sel.range);
                *ok = text_rect(ec, c.Get(), r.Get(), *out);
            }
        }, false);
        if (*ok) {
            rc = *out;
            found = true;
        }
    }
    if (!found && !caret_rect(ctx.Get(), rc)) return;
    if (!mode_win_) mode_win_ = std::make_unique<HintWindow>();
    mode_win_->show(text, rc, 800);
}

void TextService::toggle_mode() {
    if (state_.symbol_page >= 0) close_symbols();
    english_ = !english_;
    update_mode_ui();
    show_mode(english_ ? L"英" : L"中");
}

void TextService::update_mode_ui() {
    DWORD conv = get_compartment(GUID_COMPARTMENT_KEYBOARD_INPUTMODE_CONVERSION);
    DWORD want = english_ ? (conv & ~static_cast<DWORD>(TF_CONVERSIONMODE_NATIVE)) : (conv | TF_CONVERSIONMODE_NATIVE);
    if (want != conv) set_compartment(GUID_COMPARTMENT_KEYBOARD_INPUTMODE_CONVERSION, want);
    if (button_) button_->update();
}

STDMETHODIMP TextService::OnChange(REFGUID guid) {
    // the taskbar's 中／英 indicator was clicked (or another program changed the mode)
    try {
        if (setting_compartment_) return S_OK;
        // Windows sets these again itself just after a program starts (to English / keyboard
        // off): right after Activate, the mode set there (開啟時的模式) wins over that
        // (Discord, Spotify do it only as their window first takes the focus, which can be much
        // later: started in the tray, opened a minute after. Until then, and for a moment after,
        // it is not the user: a Shift tap or the taskbar button is ours, never seen here)
        DWORD now = GetTickCount();
        DWORD since = now - activated_at_;
        bool fresh = since < 1500 || !first_focus_at_ || now - first_focus_at_ < 1000;
        std::string when = " " + std::to_string(since) + " ms after activate" + (fresh ? " (fresh)" : "");
        if (guid == GUID_COMPARTMENT_KEYBOARD_INPUTMODE_CONVERSION) {
            bool english = !(get_compartment(guid) & TF_CONVERSIONMODE_NATIVE);
            debug(std::string("conversion: ") + (english ? "english" : "native") + when);
            if (fresh && english != english_) {
                update_mode_ui();
            } else if (english != english_) {
                english_ = english;
                if (button_) button_->update();
            }
        } else if (guid == GUID_COMPARTMENT_KEYBOARD_OPENCLOSE) {
            bool open = get_compartment(guid) != 0;
            debug(std::string("openclose: ") + (open ? "open" : "closed") + when);
            if (fresh && !open) set_compartment(guid, TRUE);
            if (button_) button_->update();
        }
    } catch (...) {
    }
    return S_OK;
}

void TextService::set_compartment(REFGUID guid, DWORD value) {
    ComPtr<ITfCompartmentMgr> mgr;
    ComPtr<ITfCompartment> comp;
    if (!thread_mgr_ || FAILED(thread_mgr_->QueryInterface(IID_PPV_ARGS(&mgr))) ||
        FAILED(mgr->GetCompartment(guid, &comp)))
        return;
    VARIANT v;
    VariantInit(&v);
    v.vt = VT_I4;
    v.lVal = static_cast<LONG>(value);
    setting_compartment_ = true;
    comp->SetValue(client_id_, &v);
    setting_compartment_ = false;
}

DWORD TextService::get_compartment(REFGUID guid) {
    ComPtr<ITfCompartmentMgr> mgr;
    ComPtr<ITfCompartment> comp;
    if (!thread_mgr_ || FAILED(thread_mgr_->QueryInterface(IID_PPV_ARGS(&mgr))) ||
        FAILED(mgr->GetCompartment(guid, &comp)))
        return guid == GUID_COMPARTMENT_KEYBOARD_OPENCLOSE ? 1 : 0;
    VARIANT v;
    VariantInit(&v);
    if (FAILED(comp->GetValue(&v)) || v.vt != VT_I4) return guid == GUID_COMPARTMENT_KEYBOARD_OPENCLOSE ? 1 : 0;
    return static_cast<DWORD>(v.lVal);
}

void TextService::advise_compartment(REFGUID guid, DWORD& cookie, bool advise) {
    ComPtr<ITfCompartmentMgr> mgr;
    ComPtr<ITfCompartment> comp;
    ComPtr<ITfSource> source;
    if (!thread_mgr_ || FAILED(thread_mgr_->QueryInterface(IID_PPV_ARGS(&mgr))) ||
        FAILED(mgr->GetCompartment(guid, &comp)) || FAILED(comp->QueryInterface(IID_PPV_ARGS(&source))))
        return;
    if (advise) {
        source->AdviseSink(IID_ITfCompartmentEventSink, static_cast<ITfCompartmentEventSink*>(this), &cookie);
    } else if (cookie != TF_INVALID_COOKIE) {
        source->UnadviseSink(cookie);
        cookie = TF_INVALID_COOKIE;
    }
}

// ---- display attributes -------------------------------------------------------------------

STDMETHODIMP TextService::EnumDisplayAttributeInfo(IEnumTfDisplayAttributeInfo** out) {
    if (!out) return E_INVALIDARG;
    *out = new EnumAttributeInfo();
    return S_OK;
}

STDMETHODIMP TextService::GetDisplayAttributeInfo(REFGUID guid, ITfDisplayAttributeInfo** out) {
    if (!out) return E_INVALIDARG;
    *out = nullptr;
    if (guid != GUID_AttrInput) return E_INVALIDARG;
    *out = new AttributeInfo();
    return S_OK;
}

}  // namespace sz
