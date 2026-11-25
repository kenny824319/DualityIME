// Tests of the shared key record (#73, cpp/tip/key_record.h): how a key's last event is judged, that every program sees
// the same record (a child process notes a key, this one reads it), and what one note costs (printed: ns per note).
//   sz_keytest            run the tests, print "ok ..." lines, exit 1 on a failure
//   sz_keytest --child    (used by the test) note F24 down, wait for "up" on stdin, note it up
#include <windows.h>
#include <aclapi.h>
#include <sddl.h>

#include <chrono>
#include <cstdio>
#include <cstring>
#include <string>

#include "key_record.h"

using namespace sz::keys;

static int failures = 0;

static void check(const char* what, bool ok) {
    std::printf("%s  %s\n", ok ? "ok  " : "FAIL", what);
    if (!ok) ++failures;
}

static int child() {
    note(VK_F24, true);
    std::printf("down\n");
    std::fflush(stdout);
    char line[16];
    if (std::fgets(line, sizeof line, stdin)) note(VK_F24, false);
    return 0;
}

int main(int argc, char** argv) {
    if (argc > 1 && std::strcmp(argv[1], "--child") == 0) return child();

    // how a key's record is judged
    check("never seen: None", judge(0, 50000) == Seen::None);
    check("last event up: Released", judge(entry(false, 1000), 50000) == Seen::Released);
    check("down 10 s ago exactly: Held", judge(entry(true, 1000), 1000 + STUCK_MS) == Seen::Held);
    check("down more than 10 s ago: Stuck", judge(entry(true, 1000), 1000 + STUCK_MS + 1) == Seen::Stuck);
    check("down just now: Held", judge(entry(true, 1000), 1001) == Seen::Held);
    check("a tick before the record (not expected): Held", judge(entry(true, 5000), 4000) == Seen::Held);
    check("tick 0 down is still a record", judge(entry(true, 0), 1) == Seen::Held);

    // only the keys Ctrl + a mark asks about go in (findings #118: letters, digits, the number pad never - a program
    // reading the record must not be able to tell what is typed)
    note('A', true);
    check("a letter is never recorded", seen('A') == Seen::None);
    note('7', true);
    check("a digit is never recorded", seen('7') == Seen::None);
    note(VK_NUMPAD7, true);
    check("a number pad key is never recorded", seen(VK_NUMPAD7) == Seen::None);
    note(VK_SPACE, true);
    note(VK_OEM_COMMA, true);
    check("space and a mark are never recorded", seen(VK_SPACE) == Seen::None && seen(VK_OEM_COMMA) == Seen::None);
    note(VK_INSERT, true);
    check("a shortcut key is recorded", seen(VK_INSERT) == Seen::Held);
    note(VK_INSERT, false);
    // ...and readable by this user's programs only (findings #118: Everyone and every app container could)
    {
        HANDLE h = OpenFileMappingW(READ_CONTROL, FALSE, SECTION);
        PSECURITY_DESCRIPTOR sd = nullptr;
        PACL dacl = nullptr;
        LPWSTR text = nullptr;
        bool got = h && GetSecurityInfo(h, SE_KERNEL_OBJECT, DACL_SECURITY_INFORMATION, nullptr, nullptr, &dacl, nullptr,
                                        &sd) == ERROR_SUCCESS &&
                   ConvertSecurityDescriptorToStringSecurityDescriptorW(sd, SDDL_REVISION_1, DACL_SECURITY_INFORMATION,
                                                                        &text, nullptr);
        std::wstring dacl_text = got ? text : L"";
        check("the record's access: not Everyone, not app containers",
              got && dacl_text.find(L";;;WD)") == std::wstring::npos && dacl_text.find(L";;;AC)") == std::wstring::npos);
        if (text) LocalFree(text);
        if (sd) LocalFree(sd);
        if (h) CloseHandle(h);
    }

    // this program's notes
    note(VK_F23, true);
    check("noted down: Held", seen(VK_F23) == Seen::Held);
    note(VK_F23, false);
    check("noted up: Released", seen(VK_F23) == Seen::Released);
    check("out of range: None", seen(300) == Seen::None);
    check("the record is the shared one", shared());

    // another program's notes, through the shared record
    SECURITY_ATTRIBUTES sa{sizeof(sa), nullptr, TRUE};
    HANDLE out_r, out_w, in_r, in_w;
    CreatePipe(&out_r, &out_w, &sa, 0);
    CreatePipe(&in_r, &in_w, &sa, 0);
    SetHandleInformation(out_r, HANDLE_FLAG_INHERIT, 0);
    SetHandleInformation(in_w, HANDLE_FLAG_INHERIT, 0);
    wchar_t self[MAX_PATH];
    GetModuleFileNameW(nullptr, self, MAX_PATH);
    std::wstring cmd = L"\"" + std::wstring(self) + L"\" --child";
    STARTUPINFOW si{sizeof(si)};
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdOutput = out_w;
    si.hStdInput = in_r;
    si.hStdError = GetStdHandle(STD_ERROR_HANDLE);
    PROCESS_INFORMATION pi{};
    note(VK_F24, false);
    bool started = CreateProcessW(nullptr, &cmd[0], nullptr, nullptr, TRUE, 0, nullptr, nullptr, &si, &pi);
    CloseHandle(out_w);
    CloseHandle(in_r);
    char buf[16] = {};
    DWORD got = 0;
    if (started) ReadFile(out_r, buf, sizeof buf - 1, &got, nullptr);
    check("another program's key-down is seen here", started && seen(VK_F24) == Seen::Held);
    DWORD wrote = 0;
    WriteFile(in_w, "up\n", 3, &wrote, nullptr);
    CloseHandle(in_w);
    if (started) WaitForSingleObject(pi.hProcess, 5000);
    check("another program's key-up is seen here", started && seen(VK_F24) == Seen::Released);
    if (started) {
        CloseHandle(pi.hProcess);
        CloseHandle(pi.hThread);
    }
    CloseHandle(out_r);

    // what one note costs (the work added to every key)
    const int n = 10000000;
    auto t0 = std::chrono::steady_clock::now();
    for (int i = 0; i < n; ++i) note(VK_F22, (i & 1) != 0);
    auto t1 = std::chrono::steady_clock::now();
    double ns = std::chrono::duration<double, std::nano>(t1 - t0).count() / n;
    std::printf("      (one note: %.1f ns, %d notes)\n", ns, n);
    check("one note under 1 microsecond", ns < 1000);

    std::printf(failures ? "%d failed\n" : "all passed\n", failures);
    return failures ? 1 : 0;
}
