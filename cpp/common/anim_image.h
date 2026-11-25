// 自訂外觀的動圖 (#56, docs/superpowers/specs/2026-11-24-animated-photos-design.md): an animated GIF or APNG read
// into whole frames, composited once at load as browsers show them, and the frame to show at a time.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace sz {

struct AnimImage {
    int w = 0, h = 0;
    std::vector<std::vector<uint8_t>> frames;  // 32-bit premultiplied BGRA, w * h * 4 each, the whole picture
    std::vector<int> delays_ms;                // as played: 10 ms or less is 100 ms (as browsers)
    int loops = 0;                             // plays in all, 0 = forever
};

// An animated GIF (2 frames or more: WIC's frames, their disposal) or APNG (acTL with 2 frames or more: its own chunk
// reader, each frame decoded by WIC as a PNG of its own, dispose and blend) into `out`. false: no animation (a still,
// a one-frame file), unreadable, or its frames over max_bytes together - the caller loads the file as a still.
// COM must be initialized on the calling thread (WIC).
bool load_anim(const std::wstring& path, AnimImage& out, size_t max_bytes);

// The frame shown `elapsed_ms` after the animation started; *next_ms (if given) the time from the start of the next
// change, -1 when there is none (the last frame of the last play, and after it: the last frame stays).
int frame_at(const std::vector<int>& delays_ms, int loops, long long elapsed_ms, long long* next_ms);

}  // namespace sz
