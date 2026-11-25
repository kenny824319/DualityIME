#pragma once
// 自訂外觀 (#35): the frame each window's photo is scaled to and centred on (DIP), and where the photo goes - the same
// sums as settings/ui/frame.js (both checked against tests/photo_frames.json and tests/photo_places.json): the
// candidate list and the quick fill list a whole page with these settings, the symbol keyboard its size, the others
// (their size follows what they show) a fixed one. No Windows here: tools/trace.cpp prints them for tools/compare.py.
#include <algorithm>
#include <cmath>
#include <string>

namespace sz {

struct PhotoFrame {
    double w, h;
};

inline PhotoFrame photo_frame(const std::string& window, int keys, int per_line, int font_size) {
    int f = std::clamp(font_size, 10, 48);
    int line_h = static_cast<int>(std::floor(f * 1.28 + 0.5));  // (a character's height, as cpp/tip/ui.cpp measures it)
    if (window == "keyboard") return {918, 292};
    if (window == "tip") return {200, 44};
    if (window == "mode") return {56, 48};
    if (window == "notice") return {140, 32};  // (訊息提示, #73: "Insert 沒有放開" at 16 px)
    if (window == "bar") return {420, 44};
    if (window == "fill") return {332, static_cast<double>(12 + 30 + 9 * (line_h + 10))};
    int n = std::max(1, keys), p = std::max(1, per_line);
    int label = static_cast<int>(std::floor(std::max(11, f * 2 / 3) * 0.55 + 0.5));
    int cell = label + 6 + f + 20, row = line_h + 6;  // (one character to a cell)
    int w, h;
    if (p > 1 && p >= n) {  // one row: each cell as wide as it is
        w = 12 + n * cell + (n - 1) * 2;
        h = 36 + row + 6;
    } else {  // a column (1 to a row) or a grid: cells 64 wide at least
        int rows = (n + p - 1) / p;
        w = 12 + p * std::max(64, cell) + (p - 1) * 4;
        h = 36 + rows * (row + 2) - 2 + 6;
    }
    return {static_cast<double>(std::max(w, 110)), static_cast<double>(h)};
}

struct PhotoPlace {
    double left, top, w, h;
};

// where the photo (iw x ih) goes in a window w x h with this frame (all in one unit: DIP, or device pixels with the
// frame scaled). Scaled to cover the frame (times zoom %), and the window if it is bigger; its point (x, y) at the
// frame's centre, clamped against the larger of window and frame (a window shorter than its frame, the last page,
// shows the same picture's top; review 2026-11-14). (min of max, not std::clamp: the low end may round to a hair
// above the high one, and clamp's low above its high is undefined)
inline PhotoPlace photo_place(PhotoFrame frame, double zoom, double x, double y, double w, double h, double iw,
                              double ih) {
    double k = std::max(frame.w / iw, frame.h / ih) * zoom / 100;
    k = std::max({k, w / iw, h / ih});
    double dw = iw * k, dh = ih * k;
    double left = std::min(0.0, std::max(std::max(w, frame.w) - dw, frame.w / 2 - x * dw));
    double top = std::min(0.0, std::max(std::max(h, frame.h) - dh, frame.h / 2 - y * dh));
    return {left, top, dw, dh};
}

}  // namespace sz
