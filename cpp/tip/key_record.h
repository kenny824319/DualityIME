// The last event of each key, as the input method sees it in every program (#73). Windows can keep a key "held" whose
// key-up went missing (2026-11-25: Insert, and Ctrl + a punctuation key was every program's shortcut until it was tapped
// again). Each program's input method writes here as keys pass it, so another program can tell a key let go or stuck
// (pressed more than STUCK_MS ago, never let go: a real chord is never held that long - Windows repeats only the last
// key pressed, so a key held under another stops repeating) from one really held. No timer, no polling: a note is one
// write, read only when Ctrl + a punctuation key finds a key held.
#pragma once
#include <cstdint>

namespace sz::keys {

enum class Seen { None, Released, Held, Stuck };
constexpr uint64_t STUCK_MS = 10000;
constexpr unsigned KEYS = 256;                                    // one record (64 bits) per virtual key
constexpr const wchar_t* SECTION = L"Local\\DualityIME.KeyRecord.1";  // the shared memory holding them

uint64_t entry(bool down, uint64_t tick);  // one key's record: (tick << 2) | 2 | down; 0 is "never seen"
Seen judge(uint64_t entry, uint64_t now, uint64_t stuck_ms = STUCK_MS);

void note(unsigned vk, bool down);  // a key event passing the input method
Seen seen(unsigned vk);             // what the record says of the key now
bool shared();                      // false: the shared record could not be opened (an AppContainer app): this program's own

}  // namespace sz::keys
