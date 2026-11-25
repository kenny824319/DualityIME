#include "key_record.h"

#include <windows.h>
#include <sddl.h>

#include <string>
#include <vector>

namespace sz::keys {

// bit 0: down; bit 1: always set, so a record is never 0 (not even at tick 0); above: the tick
uint64_t entry(bool down, uint64_t tick) { return (tick << 2) | 2u | (down ? 1u : 0u); }

Seen judge(uint64_t e, uint64_t now, uint64_t stuck_ms) {
    if (e == 0) return Seen::None;
    if (!(e & 1)) return Seen::Released;
    uint64_t at = e >> 2;
    return now > at && now - at > stuck_ms ? Seen::Stuck : Seen::Held;
}

namespace {

volatile LONG64 own[KEYS];  // this program's own record, when the shared one can't be opened

std::wstring user_sid() {  // this program's user, "S-1-5-21-..."
    HANDLE token = nullptr;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) return L"";
    DWORD size = 0;
    GetTokenInformation(token, TokenUser, nullptr, 0, &size);
    std::vector<BYTE> buf(size);
    std::wstring out;
    LPWSTR text = nullptr;
    if (size && GetTokenInformation(token, TokenUser, buf.data(), size, &size) &&
        ConvertSidToStringSidW(reinterpret_cast<TOKEN_USER*>(buf.data())->User.Sid, &text)) {
        out = text;
        LocalFree(text);
    }
    CloseHandle(token);
    return out;
}

volatile LONG64* table() {
    static volatile LONG64* const t = []() -> volatile LONG64* {
        // one per Windows session, read and written by this user's programs only (findings #118: not Everyone, not
        // app containers): low-integrity ones too (the label), and whatever made it first - an elevated program would
        // label it high, and the others could not write it
        const DWORD access = FILE_MAP_READ | FILE_MAP_WRITE;
        HANDLE h = OpenFileMappingW(access, FALSE, SECTION);
        if (!h) {
            std::wstring sid = user_sid();
            PSECURITY_DESCRIPTOR sd = nullptr;
            SECURITY_ATTRIBUTES sa{sizeof(sa), nullptr, FALSE};
            if (!sid.empty() &&
                ConvertStringSecurityDescriptorToSecurityDescriptorW((L"D:(A;;GRGW;;;" + sid + L")S:(ML;;NW;;;LW)").c_str(),
                                                                      SDDL_REVISION_1, &sd, nullptr))
                sa.lpSecurityDescriptor = sd;
            if (sd)  // (without its own security it is not made at all: this program keeps its own record)
                h = CreateFileMappingW(INVALID_HANDLE_VALUE, &sa, PAGE_READWRITE, 0, KEYS * sizeof(LONG64), SECTION);
            if (sd) LocalFree(sd);
        }
        if (!h) return own;
        void* view = MapViewOfFile(h, access, 0, 0, KEYS * sizeof(LONG64));
        if (!view) {
            CloseHandle(h);
            return own;
        }
        return static_cast<volatile LONG64*>(view);  // (mapped for the program's life: the handle stays open)
    }();
    return t;
}

}  // namespace

void note(unsigned vk, bool down) {
    if (vk >= KEYS || !recorded(vk)) return;
    InterlockedExchange64(&table()[vk], static_cast<LONG64>(entry(down, GetTickCount64())));
}

Seen seen(unsigned vk) {
    if (vk >= KEYS) return Seen::None;
    // (an interlocked read: the 32-bit DLL can't read 64 bits in one go otherwise)
    LONG64 e = InterlockedCompareExchange64(&table()[vk], 0, 0);
    return judge(static_cast<uint64_t>(e), GetTickCount64());
}

bool shared() { return table() != own; }

}  // namespace sz::keys
