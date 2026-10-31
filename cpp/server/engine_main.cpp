// DualityIMEEngine.exe — the engine behind the IME, one per signed-in user.
//
// Every app the user types in loads DualityIME*.dll, which only handles keys
// and windows; each of those connects here through a named pipe and gets its
// own composition (Composer). The dictionary, the learned picks (user.json)
// and the settings are loaded once and shared, and only this process writes them.
//
// Request (JSON):  {"op": "key", "ch": "a", "special": "", "caps": false} and others, see handle()
// Reply (JSON):    the composition state, see state()
#include <windows.h>
#include <sddl.h>
#include <shellapi.h>
#include <shlobj.h>

#include <atomic>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

#include "composer.h"
#include "json.h"
#include "pipe.h"

using namespace sz;

namespace {

struct Shared {
    Resources res;
    std::unique_ptr<UserModel> user;
    std::unique_ptr<Config> config;
    std::mutex lock;             // one request at a time touches the engine
    unsigned config_gen = 0;     // bumped when config.json changed
};
Shared* g = nullptr;

std::wstring user_dir() {
    wchar_t* appdata = nullptr;
    std::wstring dir;
    if (SUCCEEDED(SHGetKnownFolderPath(FOLDERID_RoamingAppData, 0, nullptr, &appdata))) dir = appdata;
    CoTaskMemFree(appdata);
    return dir + L"\\DualityIME\\";
}

bool any_file(const std::wstring& dir, std::initializer_list<const wchar_t*> names) {
    for (const wchar_t* n : names) {
        DWORD a = GetFileAttributesW((dir + L"\\" + n).c_str());
        if (a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY)) return true;
    }
    return false;
}

// Another account's first sign-in after an upgrade from 2.x: %APPDATA%\SmartZhuyin holds data and
// %APPDATA%\DualityIME none of it yet, or a move there was cut off (the one "already migrated" rule,
// Migrate.Migrated in installer/src/Migrate.cs: one of the data files and no "migrating" marker; the
// folder alone does not count, the symbol keyboard or a failed move may have made it). Let settings.exe
// move the data (no window; given this process's own Roaming folder) and wait up to 60 s (it may itself
// wait 30 s for a move the installer is making).
// Returns false unless the move is finished: settings.exe ended with Nothing (0), Moved (1) or
// KeptExisting (2) (Migrate.MoveResult). Still running after 60 s, Failed or any other exit code, or not
// started: this process must not write user.json, keys.json or config.json (a file written now would
// count as migrated, or be kept over the 2.x one, and the 2.x data stay behind); the next start tries again.
bool migrate_user_data() {
    wchar_t* appdata = nullptr;
    std::wstring roaming;
    if (SUCCEEDED(SHGetKnownFolderPath(FOLDERID_RoamingAppData, 0, nullptr, &appdata))) roaming = appdata;
    CoTaskMemFree(appdata);
    while (!roaming.empty() && roaming.back() == L'\\') roaming.pop_back();  // (a \ before the closing quote)
    if (roaming.empty()) return true;
    std::wstring dir = roaming + L"\\DualityIME";
    if (!any_file(dir, {L"migrating"})) {  // (a move cut off half way: always finished)
        if (any_file(dir, {L"user.json", L"config.json", L"keys.json", L"dictionary.json"})) return true;
        if (!any_file(roaming + L"\\SmartZhuyin", {L"user.json", L"config.json", L"keys.json", L"dictionary.json",
                                                    L"keyboard.json", L"import-memory.json"}))
            return true;
    }
    std::wstring exe = module_dir(nullptr) + L"settings.exe";
    if (GetFileAttributesW(exe.c_str()) == INVALID_FILE_ATTRIBUTES) return true;  // (a dev build: no settings.exe)
    std::wstring cmd = L"\"" + exe + L"\" --migrate-user \"" + roaming + L"\"";
    STARTUPINFOW si{};
    si.cb = sizeof(si);
    PROCESS_INFORMATION pi{};
    if (!CreateProcessW(nullptr, &cmd[0], nullptr, nullptr, FALSE, CREATE_NO_WINDOW, nullptr, nullptr, &si, &pi))
        return false;
    DWORD code = 3;  // (Failed)
    bool ended = WaitForSingleObject(pi.hProcess, 60000) == WAIT_OBJECT_0;
    if (ended && !GetExitCodeProcess(pi.hProcess, &code)) code = 3;
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return ended && code <= 2;  // Nothing, Moved, KeptExisting
}

Json cfg_json(const Config& c) {
    // what the IME needs to know of the settings
    Json o = Json::object();
    for (const char* k : {"shift_toggle", "start_mode", "lookup", "game_mode", "space", "full_width", "ctrl_punct",
                          "cand_font", "cand_font_size", "cand_per_row", "theme"})
        if (auto* v = c.values().get(k)) o[k] = *v;
    return o;
}

struct Session {
    std::unique_ptr<Composer> c;
    unsigned config_gen = 0;
};

Json state(Session& s, bool handled) {
    Composer& c = *s.c;
    Json o = Json::object();
    o["handled"] = Json(handled);
    o["commit"] = Json(to_utf8(c.commit));
    o["composition"] = Json(to_utf8(c.composition));
    o["cursor"] = Json(c.cursor);
    auto [page, index] = c.page_candidates();
    Json cands = Json::array();
    for (auto& p : page) cands.push(Json(to_utf8(p)));
    o["candidates"] = cands;
    o["cand_index"] = Json(index);
    o["cand_page"] = Json(c.cand_index / Composer::PAGE);
    o["cand_pages"] = Json(static_cast<int>((c.candidates.size() + Composer::PAGE - 1) / Composer::PAGE));
    // → spread the list out: the columns shown (from column cand_first), the selection among them
    o["cand_expanded"] = Json(c.cand_expanded);
    if (c.cand_expanded) {
        auto [grid, at] = c.grid_candidates();
        Json shown = Json::array();
        for (auto& p : grid) shown.push(Json(to_utf8(p)));
        o["cand_grid"] = shown;
        o["cand_grid_index"] = Json(at);
        o["cand_first"] = Json(c.cand_first);
    }
    // Tab hint: just the next character (or the rest of the word) right after the text
    bool hint = !c.suggestions.empty() && !c.composition.empty() && c.candidates.empty();
    o["hint"] = Json(hint ? to_utf8(c.suggestions[0]) : std::string());
    // 英文字母＋注音提示: the unfinished syllable as zhuyin, in a box under its keys (from character tip_at)
    auto [tip, tip_at] = c.zhuyin_tip();
    o["zhuyin_tip"] = Json(to_utf8(tip));
    o["zhuyin_tip_at"] = Json(tip_at);
    Json repl = Json::array();  // the replacement bar above the text
    for (auto& r : c.replacements) repl.push(Json(to_utf8(r)));
    o["replacements"] = repl;
    o["repl_index"] = Json(c.repl_index);
    o["symbol_page"] = Json(c.symbol_page ? *c.symbol_page : -1);
    o["symbol_cursor"] = Json(c.symbol_cursor);
    o["can_undo"] = Json(c.can_undo());
    o["cfg"] = cfg_json(c.config);
    return o;
}

std::optional<char32_t> one_char(const Json* v) {
    if (!v || !v->is_string()) return std::nullopt;
    ustr u = from_utf8(v->as_string());
    if (u.size() != 1) return std::nullopt;
    return u[0];
}

Json handle(Session& s, const Json& req) {
    std::lock_guard<std::mutex> guard(g->lock);
    if (g->config->reload_if_changed()) ++g->config_gen;
    Composer& c = *s.c;
    if (s.config_gen != g->config_gen) {
        s.config_gen = g->config_gen;
        c.settings_changed();
    }
    const Json* opv = req.get("op");
    std::string op = opv && opv->is_string() ? opv->as_string() : "";
    auto text = [&](const char* k) -> std::string {
        auto* v = req.get(k);
        return v && v->is_string() ? v->as_string() : std::string();
    };
    auto flag = [&](const char* k) {
        auto* v = req.get(k);
        return v && v->is_bool() && v->as_bool();
    };
    bool handled = true;
    if (op == "key") {
        handled = c.key(one_char(req.get("ch")), text("special"), flag("caps"));
    } else if (op == "ctrl_punct") {
        // Ctrl + a punctuation key (the setting ctrl_punct): its full-width mark
        if (auto ch = one_char(req.get("ch"))) handled = c.type_full_punct(*ch);
    } else if (op == "pick_at") {
        // clicked in the spread-out list: that one, as Enter would take it
        auto* v = req.get("index");
        int i = v && v->is_number() ? static_cast<int>(v->as_number()) : -1;
        if (i >= 0 && i < static_cast<int>(c.candidates.size())) {
            c.cand_index = i;
            handled = c.key(std::nullopt, "ENTER");
        }
    } else if (op == "fold") {
        // the ← at the top of the spread-out list: back to the list
        c.cand_expanded = false;
        c.cand_first = 0;
    } else if (op == "literal") {
        if (auto ch = one_char(req.get("ch"))) c.type_literal(*ch);
    } else if (op == "undo") {
        ustr deleted = c.undo_commit();
        Json o = state(s, !deleted.empty());
        o["deleted"] = Json(to_utf8(deleted));
        return o;
    } else if (op == "reset") {
        c.reset();
    } else if (op == "forget_undo") {
        c.forget_undo();
    } else if (op == "close_symbols") {
        c.close_symbols();
    } else if (op == "full_width") {
        c.toggle_full_width();
    } else if (op == "flush") {
        // first key after switching to English with text still being typed:
        // send that text, plus the character of this key
        if (!c.candidates.empty()) c.key(std::nullopt, "ESC");
        ustr out;
        if (!flag("esc")) {
            c.key(std::nullopt, "ENTER");
            out = c.commit;
            if (auto ch = one_char(req.get("ch")))
                out += c.config.flag("full_width") ? to_full_width(ustr(1, *ch)) : ustr(1, *ch);
        }
        c.reset();
        c.commit = out;
    }
    Json o = state(s, handled);
    c.commit.clear();  // sent once
    return o;
}

bool read_message(HANDLE pipe, std::string& out) {
    out.clear();
    char buf[16384];
    for (;;) {
        DWORD n = 0;
        BOOL ok = ReadFile(pipe, buf, sizeof buf, &n, nullptr);
        out.append(buf, n);
        if (ok) return true;
        if (GetLastError() != ERROR_MORE_DATA) return false;
    }
}

void serve(HANDLE pipe) {
    Session s;
    {
        std::lock_guard<std::mutex> guard(g->lock);
        s.c = std::make_unique<Composer>(*g->res.table, *g->res.lexicon, g->res.english, *g->user,
                                         g->res.bigram.get(), *g->config, g->res.lm.get());
        s.config_gen = g->config_gen;
    }
    std::string msg;
    while (read_message(pipe, msg)) {
        bool ok = false;
        Json req = Json::parse(msg, &ok);
        Json reply;
        try {
            reply = ok ? handle(s, req) : Json::object();
        } catch (...) {  // never take the engine down for one bad request
            reply = Json::object();
            reply["error"] = Json("internal");
        }
        std::string out = reply.dump();
        DWORD written = 0;
        if (!WriteFile(pipe, out.data(), static_cast<DWORD>(out.size()), &written, nullptr)) break;
    }
    {
        std::lock_guard<std::mutex> guard(g->lock);
        s.c.reset();
    }
    DisconnectNamedPipe(pipe);
    CloseHandle(pipe);
}

HANDLE make_pipe(const std::wstring& name, bool first, const std::wstring& sid) {
    // The user, SYSTEM and administrators, plus sandboxed (Microsoft Store) apps
    // and low-integrity processes (browsers' sandboxes host no text fields, but
    // Store apps run at low integrity): all of them type through the IME.
    // The user by SID, not only as the owner: an engine left elevated is owned by
    // Administrators, which the user's ordinary apps are denied.
    SECURITY_ATTRIBUTES sa{sizeof sa, nullptr, FALSE};
    PSECURITY_DESCRIPTOR sd = nullptr;
    std::wstring sddl = L"D:(A;;GA;;;OW)" + (sid.empty() ? L"" : L"(A;;GA;;;" + sid + L")") +
                        L"(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;AC)(A;;GRGW;;;S-1-15-2-2)S:(ML;;NW;;;LW)";
    if (ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl.c_str(), SDDL_REVISION_1, &sd, nullptr))
        sa.lpSecurityDescriptor = sd;
    DWORD mode = PIPE_ACCESS_DUPLEX | (first ? FILE_FLAG_FIRST_PIPE_INSTANCE : 0);
    HANDLE pipe = CreateNamedPipeW(name.c_str(), mode, PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT |
                                   PIPE_REJECT_REMOTE_CLIENTS, PIPE_UNLIMITED_INSTANCES, 65536, 65536, 0, &sa);
    if (sd) LocalFree(sd);
    return pipe;
}

bool is_elevated(HANDLE process) {
    HANDLE token = nullptr;
    if (!OpenProcessToken(process, TOKEN_QUERY, &token)) return false;
    TOKEN_ELEVATION e{};
    DWORD n = 0;
    bool yes = GetTokenInformation(token, TokenElevation, &e, sizeof e, &n) && e.TokenIsElevated;
    CloseHandle(token);
    return yes;
}

// Started elevated (the IME in an app run as administrator starts the engine when none is running;
// or a script in an elevated shell): the user's ordinary apps would talk to an administrator's
// process. Start the same command line again as a child of the desktop's explorer, which gives the
// copy explorer's token, and let it serve. Nothing when explorer is elevated too (UAC off), not
// found, or another account's (an app run as SYSTEM or as another administrator waits on its own
// account's pipe): then this process serves (make_pipe lets the user in by SID). True when the
// copy started.
bool restart_unelevated() {
    if (!is_elevated(GetCurrentProcess())) return false;
    DWORD pid = 0;
    HWND shell = GetShellWindow();
    if (!shell || !GetWindowThreadProcessId(shell, &pid) || !pid) return false;
    HANDLE parent = OpenProcess(PROCESS_CREATE_PROCESS | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (!parent) return false;
    bool started = false;
    SIZE_T size = 0;
    InitializeProcThreadAttributeList(nullptr, 1, 0, &size);
    std::string buf(size, '\0');
    auto* list = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(buf.data());
    std::wstring me = user_sid();
    if (!is_elevated(parent) && !me.empty() && user_sid(parent) == me && size &&
        InitializeProcThreadAttributeList(list, 1, 0, &size)) {
        if (UpdateProcThreadAttribute(list, 0, PROC_THREAD_ATTRIBUTE_PARENT_PROCESS, &parent, sizeof parent,
                                      nullptr, nullptr)) {
            STARTUPINFOEXW si{};
            si.StartupInfo.cb = sizeof si;
            si.lpAttributeList = list;
            PROCESS_INFORMATION pi{};
            wchar_t exe[MAX_PATH * 2];
            DWORD n = GetModuleFileNameW(nullptr, exe, static_cast<DWORD>(std::size(exe)));
            std::wstring cmd = GetCommandLineW();
            if (n && n < std::size(exe) &&
                CreateProcessW(exe, cmd.data(), nullptr, nullptr, FALSE,
                               EXTENDED_STARTUPINFO_PRESENT | DETACHED_PROCESS | CREATE_NO_WINDOW |
                                   CREATE_SUSPENDED, nullptr, nullptr, &si.StartupInfo, &pi)) {
                // (checked before it runs: it never restarts itself)
                started = !is_elevated(pi.hProcess) && ResumeThread(pi.hThread) != static_cast<DWORD>(-1);
                if (!started) TerminateProcess(pi.hProcess, 0);  // (not what was meant: serve from here)
                CloseHandle(pi.hThread);
                CloseHandle(pi.hProcess);
            }
        }
        DeleteProcThreadAttributeList(list);
    }
    CloseHandle(parent);
    return started;
}

}  // namespace

// DualityIMEEngine.exe [--user-dir <folder>]  (another folder for user.json etc.: tests)
//                      [--keep-elevated]      (stay elevated: the tests' elevated window, by PID)
int WINAPI wWinMain(HINSTANCE, HINSTANCE, LPWSTR, int) {
    int argc = 0;
    LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    bool keep_elevated = false;
    for (int i = 1; argv && i < argc; ++i)
        if (std::wstring(argv[i]) == L"--keep-elevated") keep_elevated = true;
    if (!keep_elevated && restart_unelevated()) {
        if (argv) LocalFree(argv);
        return 0;
    }

    std::wstring sid = user_sid();
    std::wstring name = engine_pipe_name();
    HANDLE pipe = make_pipe(name, true, sid);
    if (pipe == INVALID_HANDLE_VALUE) {  // already running
        if (argv) LocalFree(argv);
        return 0;
    }
    SetPriorityClass(GetCurrentProcess(), ABOVE_NORMAL_PRIORITY_CLASS);  // typing must never wait

    std::wstring data = module_dir(nullptr) + L"data";
    bool own_dir = false;  // --user-dir (tests): no migration
    for (int i = 1; argv && i + 1 < argc; ++i)
        if (std::wstring(argv[i]) == L"--user-dir") own_dir = true;
    bool may_save = own_dir || migrate_user_data();
    std::wstring dir = user_dir();
    for (int i = 1; argv && i + 1 < argc; ++i) {
        if (std::wstring(argv[i]) == L"--user-dir") dir = std::wstring(argv[i + 1]) + L"\\";
        if (std::wstring(argv[i]) == L"--data-dir") data = argv[i + 1];  // (a build without data\ next to it)
    }
    if (argv) LocalFree(argv);
    g = new Shared{Resources::load(data)};
    g->user = std::make_unique<UserModel>(dir + L"user.json");
    g->config = std::make_unique<Config>(dir + L"config.json");
    if (!may_save) {  // (the move not finished: what is learned or switched now lives in memory only)
        g->user->keep_files();
        g->config->keep_file();
    }
    if (!g->res.lexicon->ok()) return 1;
    if (g->config->flag("long_sentence")) g->res.lm->get();  // (now, not on the first key typed)

    for (;;) {
        BOOL connected = ConnectNamedPipe(pipe, nullptr) ? TRUE : (GetLastError() == ERROR_PIPE_CONNECTED);
        if (connected) {
            std::thread(serve, pipe).detach();
        } else {
            CloseHandle(pipe);
        }
        pipe = make_pipe(name, false, sid);
        if (pipe == INVALID_HANDLE_VALUE) {
            Sleep(100);
            pipe = make_pipe(name, false, sid);
            if (pipe == INVALID_HANDLE_VALUE) return 1;
        }
    }
}
