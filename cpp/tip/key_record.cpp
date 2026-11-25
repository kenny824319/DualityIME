#include "key_record.h"

#include <windows.h>
#include <sddl.h>

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

volatile LONG64* table() {
    static volatile LONG64* const t = []() -> volatile LONG64* {
        // one per Windows session, readable and writable by every program of it: low-integrity ones too (the label)
        // and whatever made it first - an elevated program would label it high, and the others could not write it
        PSECURITY_DESCRIPTOR sd = nullptr;
        SECURITY_ATTRIBUTES sa{sizeof(sa), nullptr, FALSE};
        if (ConvertStringSecurityDescriptorToSecurityDescriptorW(L"D:(A;;GA;;;WD)(A;;GA;;;AC)S:(ML;;NW;;;LW)",
                                                                  SDDL_REVISION_1, &sd, nullptr))
            sa.lpSecurityDescriptor = sd;
        HANDLE h = CreateFileMappingW(INVALID_HANDLE_VALUE, sd ? &sa : nullptr, PAGE_READWRITE, 0, KEYS * sizeof(LONG64),
                                      SECTION);
        if (sd) LocalFree(sd);
        if (!h) return own;
        void* view = MapViewOfFile(h, FILE_MAP_ALL_ACCESS, 0, 0, KEYS * sizeof(LONG64));
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
    if (vk >= KEYS) return;
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
