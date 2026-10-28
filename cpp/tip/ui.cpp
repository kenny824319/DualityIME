#include "ui.h"

#include <dwmapi.h>
#include <windowsx.h>
#include <shlobj.h>

#include <algorithm>
#include <cmath>

#include "globals.h"
#include "json.h"
#include "symbols.h"
#include "util.h"

namespace sz {

namespace {

constexpr UINT_PTR TIMER_HIDE = 1;
constexpr UINT_PTR TIMER_CARET = 2;

const Theme kDark = {RGB(12, 12, 12), RGB(30, 30, 30), RGB(58, 58, 58), RGB(17, 17, 17), RGB(240, 240, 240),
                     RGB(130, 130, 130), RGB(0, 103, 192), RGB(255, 255, 255), RGB(70, 70, 70), RGB(64, 64, 64),
                     RGB(64, 64, 64), RGB(245, 245, 245)};
const Theme kLight = {RGB(243, 243, 243), RGB(255, 255, 255), RGB(210, 210, 210), RGB(232, 232, 232),
                      RGB(26, 26, 26), RGB(125, 125, 125), RGB(0, 103, 192), RGB(255, 255, 255),
                      RGB(190, 190, 190), RGB(255, 255, 255), RGB(200, 200, 200), RGB(30, 30, 30)};

std::wstring wide(const ustr& u) {
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

RECT work_area(const RECT& near_rect) {
    POINT pt{near_rect.left, near_rect.bottom};
    MONITORINFO mi{sizeof mi};
    GetMonitorInfoW(MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST), &mi);
    return mi.rcWork;
}

}  // namespace

const Theme& current_theme() {
    DWORD light = 1, size = sizeof light;
    RegGetValueW(HKEY_CURRENT_USER, L"Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize",
                 L"AppsUseLightTheme", RRF_RT_REG_DWORD, nullptr, &light, &size);
    return light ? kLight : kDark;
}

// ---- Canvas -----------------------------------------------------------------------------

Canvas::Canvas(HDC dc, double scale) : dc_(dc), s_(scale) { SetBkMode(dc, TRANSPARENT); }

Canvas::~Canvas() {
    for (auto& [_, f] : fonts_) DeleteObject(f);
    for (auto o : objs_) DeleteObject(o);
}

HFONT Canvas::font(int size, const wchar_t* face, int weight) {
    std::wstring key = std::to_wstring(size) + L"/" + std::to_wstring(weight) + L"/" + face;
    for (auto& [k, f] : fonts_)
        if (k == key) return f;
    HFONT f = CreateFontW(-static_cast<int>(size * s_), 0, 0, 0, weight, 0, 0, 0, DEFAULT_CHARSET, 0, 0,
                          CLEARTYPE_QUALITY, 0, face);
    fonts_.emplace_back(key, f);
    return f;
}

void Canvas::box(double x, double y, double w, double h, COLORREF fill, COLORREF edge, int radius) {
    HBRUSH b = CreateSolidBrush(fill);
    HPEN p = CreatePen(PS_SOLID, std::max(1, static_cast<int>(s_)), edge);
    objs_.push_back(b);
    objs_.push_back(p);
    SelectObject(dc_, b);
    SelectObject(dc_, p);
    RoundRect(dc_, static_cast<int>(x * s_), static_cast<int>(y * s_), static_cast<int>((x + w) * s_),
              static_cast<int>((y + h) * s_), static_cast<int>(radius * s_), static_cast<int>(radius * s_));
}

void Canvas::text(const std::wstring& t, double x, double y, double w, double h, COLORREF color, HFONT font,
                  UINT flags) {
    SelectObject(dc_, font);
    SetTextColor(dc_, color);
    RECT r{static_cast<int>(x * s_), static_cast<int>(y * s_), static_cast<int>((x + w) * s_),
           static_cast<int>((y + h) * s_)};
    DrawTextW(dc_, t.c_str(), static_cast<int>(t.size()), &r, flags | DT_NOPREFIX);
}

SIZE Canvas::measure(const std::wstring& t, HFONT font) {
    SelectObject(dc_, font);
    RECT r{0, 0, 0, 0};
    DrawTextW(dc_, t.c_str(), static_cast<int>(t.size()), &r, DT_CALCRECT | DT_SINGLELINE | DT_NOPREFIX);
    return {static_cast<LONG>(std::ceil(r.right / s_)), static_cast<LONG>(std::ceil(r.bottom / s_))};
}

bool Canvas::has_glyphs(const std::wstring& t, HFONT font) {
    SelectObject(dc_, font);
    std::vector<WORD> idx(t.size());
    GetGlyphIndicesW(dc_, t.c_str(), static_cast<int>(t.size()), idx.data(), GGI_MARK_NONEXISTING_GLYPHS);
    return std::none_of(idx.begin(), idx.end(), [](WORD i) { return i == 0xFFFF; });
}

// ---- Popup --------------------------------------------------------------------------------

Popup::~Popup() {
    if (hwnd_) DestroyWindow(hwnd_);
}

bool Popup::create(const wchar_t* class_name) {
    if (hwnd_) return true;
    WNDCLASSEXW wc{sizeof wc};
    if (!GetClassInfoExW(g_module, class_name, &wc)) {
        wc = WNDCLASSEXW{sizeof wc};
        wc.style = CS_DBLCLKS | CS_IME;
        wc.lpfnWndProc = proc;
        wc.hInstance = g_module;
        wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
        wc.lpszClassName = class_name;
        RegisterClassExW(&wc);
    }
    hwnd_ = CreateWindowExW(WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE, class_name, kName, WS_POPUP, 0, 0,
                            10, 10, nullptr, nullptr, g_module, this);
    if (!hwnd_) return false;
    DWORD corners = 2;  // rounded corners on Windows 11
    DwmSetWindowAttribute(hwnd_, 33 /* DWMWA_WINDOW_CORNER_PREFERENCE */, &corners, sizeof corners);
    return true;
}

double Popup::scale() const {
    UINT dpi = hwnd_ ? GetDpiForWindow(hwnd_) : 96;
    return (dpi ? dpi : 96) / 96.0;
}

void Popup::place(int x, int y, int w, int h) {
    SetWindowPos(hwnd_, HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE | SWP_SHOWWINDOW);
    visible_ = true;
    InvalidateRect(hwnd_, nullptr, FALSE);
}

void Popup::hide() {
    if (hwnd_ && visible_) ShowWindow(hwnd_, SW_HIDE);
    visible_ = false;
}

void Popup::paint() {
    PAINTSTRUCT ps;
    HDC dc = BeginPaint(hwnd_, &ps);
    RECT rc;
    GetClientRect(hwnd_, &rc);
    int w = std::max<int>(rc.right, 1), h = std::max<int>(rc.bottom, 1);
    HDC mem = CreateCompatibleDC(dc);
    HBITMAP bmp = CreateCompatibleBitmap(dc, w, h);
    HGDIOBJ old = SelectObject(mem, bmp);
    draw(mem, w, h);
    BitBlt(dc, 0, 0, w, h, mem, 0, 0, SRCCOPY);
    SelectObject(mem, old);
    DeleteObject(bmp);
    DeleteDC(mem);
    EndPaint(hwnd_, &ps);
}

LRESULT Popup::message(UINT, WPARAM, LPARAM, bool& done) {
    done = false;
    return 0;
}

LRESULT CALLBACK Popup::proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    if (msg == WM_NCCREATE) {
        auto* cs = reinterpret_cast<CREATESTRUCTW*>(lp);
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(cs->lpCreateParams));
    }
    auto* self = reinterpret_cast<Popup*>(GetWindowLongPtrW(hwnd, GWLP_USERDATA));
    if (msg == WM_MOUSEACTIVATE) return MA_NOACTIVATE;  // clicking must not take the focus from the app
    if (msg == WM_ERASEBKGND) return 1;
    if (!self) return DefWindowProcW(hwnd, msg, wp, lp);
    if (msg == WM_NCDESTROY) {
        self->hwnd_ = nullptr;
        return DefWindowProcW(hwnd, msg, wp, lp);
    }
    try {
        if (msg == WM_PAINT) {
            self->paint();
            return 0;
        }
        bool done = false;
        LRESULT r = self->message(msg, wp, lp, done);
        if (done) return r;
    } catch (...) {  // never let an error escape into the app's message loop
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

// ---- candidate list ---------------------------------------------------------------------

namespace {
constexpr int CAND_PAD = 6, ROW_GAP = 2, ITEM_PAD_X = 10, ITEM_PAD_Y = 3, TITLE_H = 30;
constexpr int GRID_ROWS = 9, GRID_SHOWN = 4, COL_GAP = 4, SCROLL_H = 6, ARROW_W = 30;
}

void CandidateWindow::layout(HDC dc) {
    Canvas cv(dc, 1.0);  // sizes in 96-dpi units
    HFONT f = cv.font(font_size_, font_.c_str());
    HFONT num = cv.font(std::max(11, font_size_ * 2 / 3), L"Segoe UI");
    hit_.clear();
    int x = CAND_PAD, y = CAND_PAD + TITLE_H, max_w = 0;
    int row_h = cv.measure(L"中", f).cy + 2 * ITEM_PAD_Y;
    for (size_t i = 0; i < items_.size(); ++i) {
        int w = cv.measure(std::to_wstring(i + 1), num).cx + 6 + cv.measure(items_[i], f).cx + 2 * ITEM_PAD_X;
        if (one_row_) {
            hit_.push_back(RECT{x, y, x + w, y + row_h});
            x += w + ROW_GAP;
        } else {
            max_w = std::max(max_w, w);
            hit_.push_back(RECT{x, y, x + w, y + row_h});
            y += row_h + ROW_GAP;
        }
    }
    if (!one_row_) {
        max_w = std::max(max_w, 64);
        for (auto& r : hit_) r.right = r.left + max_w;
        size_.cx = CAND_PAD * 2 + max_w;
        size_.cy = y - ROW_GAP + CAND_PAD;
    } else {
        size_.cx = x - ROW_GAP + CAND_PAD;
        size_.cy = y + row_h + CAND_PAD;
    }
    size_.cx = std::max<LONG>(size_.cx, 110);  // room for the title
    arrow_ = RECT{size_.cx - CAND_PAD - ARROW_W, CAND_PAD, size_.cx - CAND_PAD, CAND_PAD + TITLE_H};
    track_ = RECT{};
}

void CandidateWindow::layout_grid(HDC dc) {
    Canvas cv(dc, 1.0);  // sizes in 96-dpi units
    HFONT f = cv.font(font_size_, font_.c_str());
    HFONT num = cv.font(std::max(11, font_size_ * 2 / 3), L"Segoe UI");
    hit_.clear();
    // every column as wide as the widest candidate shown, with room for its number
    int text_w = 0;
    for (auto& item : items_) text_w = std::max<int>(text_w, cv.measure(item, f).cx);
    int col_w = std::max<int>(64, cv.measure(L"9", num).cx + 6 + text_w + 2 * ITEM_PAD_X);
    int row_h = cv.measure(L"中", f).cy + 2 * ITEM_PAD_Y;
    int top = CAND_PAD + TITLE_H;
    for (size_t i = 0; i < items_.size(); ++i) {
        int col = static_cast<int>(i) / GRID_ROWS, row = static_cast<int>(i) % GRID_ROWS;
        int x = CAND_PAD + col * (col_w + COL_GAP), y = top + row * (row_h + ROW_GAP);
        hit_.push_back(RECT{x, y, x + col_w, y + row_h});
    }
    int shown = std::max(1, std::min(GRID_SHOWN, columns_));
    size_.cx = CAND_PAD * 2 + shown * col_w + (shown - 1) * COL_GAP;
    int bottom = top + GRID_ROWS * (row_h + ROW_GAP) - ROW_GAP;
    track_ = RECT{};
    if (columns_ > GRID_SHOWN) {  // more columns than shown: a scroll bar under them
        track_ = RECT{CAND_PAD, bottom + CAND_PAD, size_.cx - CAND_PAD, bottom + CAND_PAD + SCROLL_H};
        bottom = track_.bottom;
    }
    size_.cy = bottom + CAND_PAD;
    arrow_ = RECT{size_.cx - CAND_PAD - ARROW_W, CAND_PAD, size_.cx - CAND_PAD, CAND_PAD + TITLE_H};
}

void CandidateWindow::show(const std::vector<std::wstring>& items, int index, int page, int pages,
                           const std::wstring& font, int font_size, bool one_row, const RECT& text_rect) {
    if (!create(L"DualityIMECandidates")) return;
    items_ = items;
    index_ = index;
    page_ = page;
    pages_ = std::max(pages, 1);
    font_ = font.empty() ? L"Microsoft JhengHei UI" : font;
    font_size_ = std::clamp(font_size, 10, 48);
    one_row_ = one_row;
    grid_ = false;
    HDC dc = GetDC(hwnd_);
    layout(dc);
    ReleaseDC(hwnd_, dc);
    place_near(text_rect);
}

void CandidateWindow::show_grid(const std::vector<std::wstring>& items, int index, int first, int columns,
                                const std::wstring& font, int font_size, const RECT& text_rect) {
    if (!create(L"DualityIMECandidates")) return;
    items_ = items;
    index_ = index;
    first_ = first;
    columns_ = std::max(columns, 1);
    font_ = font.empty() ? L"Microsoft JhengHei UI" : font;
    font_size_ = std::clamp(font_size, 10, 48);
    one_row_ = false;
    grid_ = true;
    HDC dc = GetDC(hwnd_);
    layout_grid(dc);
    ReleaseDC(hwnd_, dc);
    place_near(text_rect);
}

void CandidateWindow::place_near(const RECT& text_rect) {
    double s = scale();
    int w = static_cast<int>(size_.cx * s), h = static_cast<int>(size_.cy * s);
    RECT work = work_area(text_rect);
    int x = std::min<int>(text_rect.left, work.right - w);
    x = std::max<int>(x, work.left);
    int y = text_rect.bottom + static_cast<int>(4 * s);
    if (y + h > work.bottom) y = text_rect.top - h - static_cast<int>(4 * s);  // no room below: go above
    place(x, y, w, h);
}

void CandidateWindow::draw(HDC dc, int w, int h) {
    const Theme& th = current_theme();
    double s = scale();
    Canvas cv(dc, s);
    cv.box(0, 0, w / s, h / s, th.key, th.border, 10);
    HFONT f = cv.font(font_size_, font_.c_str());
    HFONT num = cv.font(std::max(11, font_size_ * 2 / 3), L"Segoe UI");
    int nine = cv.measure(L"9", num).cx;
    int selected_col = index_ / GRID_ROWS;
    for (size_t i = 0; i < items_.size() && i < hit_.size(); ++i) {
        const RECT& r = hit_[i];
        bool on = static_cast<int>(i) == index_;
        if (on) cv.box(r.left, r.top, r.right - r.left, r.bottom - r.top, th.accent, th.accent, 6);
        // the spread-out list numbers only the column with the selection (1-9 pick in it)
        bool numbered = !grid_ || static_cast<int>(i) / GRID_ROWS == selected_col;
        std::wstring n = std::to_wstring(grid_ ? static_cast<int>(i) % GRID_ROWS + 1 : static_cast<int>(i) + 1);
        int nw = grid_ ? nine : cv.measure(n, num).cx;
        if (numbered)
            cv.text(n, r.left + ITEM_PAD_X, r.top, nw + 2, r.bottom - r.top, on ? th.accent_text : th.label, num,
                    DT_LEFT | DT_VCENTER | DT_SINGLELINE);
        cv.text(items_[i], r.left + ITEM_PAD_X + nw + 6, r.top, r.right - r.left, r.bottom - r.top,
                on ? th.accent_text : th.text, f, DT_LEFT | DT_VCENTER | DT_SINGLELINE);
    }
    HFONT title = cv.font(std::max(12, font_size_ * 3 / 4), L"Microsoft JhengHei UI");
    cv.text(L"候選字", CAND_PAD + ITEM_PAD_X, CAND_PAD, w / s, TITLE_H, th.label, title,
            DT_LEFT | DT_VCENTER | DT_SINGLELINE);
    // → more: click or press → to spread the list out; ←: back to the list
    if (grid_ || page_ + 1 < pages_)
        cv.text(grid_ ? L"←" : L"→", arrow_.left, arrow_.top, arrow_.right - arrow_.left - ITEM_PAD_X / 2,
                arrow_.bottom - arrow_.top, th.label, title, DT_RIGHT | DT_VCENTER | DT_SINGLELINE);
    if (grid_ && track_.right > track_.left) {  // which columns of all are shown
        double tw = track_.right - track_.left;
        double thumb = std::max(24.0, tw * GRID_SHOWN / columns_);
        double x = track_.left + (tw - thumb) * first_ / std::max(1, columns_ - GRID_SHOWN);
        cv.box(track_.left, track_.top, tw, SCROLL_H, th.key, th.border, SCROLL_H / 2);
        cv.box(x, track_.top, thumb, SCROLL_H, th.dim, th.dim, SCROLL_H / 2);
    }
}

LRESULT CandidateWindow::message(UINT msg, WPARAM, LPARAM lp, bool& done) {
    done = false;
    if (msg == WM_LBUTTONUP) {
        double s = scale();
        POINT pt{static_cast<LONG>(GET_X_LPARAM(lp) / s), static_cast<LONG>(GET_Y_LPARAM(lp) / s)};
        if (PtInRect(&arrow_, pt)) {
            if (grid_ && on_fold) on_fold();
            else if (!grid_ && !one_row_ && page_ + 1 < pages_ && on_expand) on_expand();  // (one row: → is the next one)
            done = true;
            return 0;
        }
        for (size_t i = 0; i < hit_.size(); ++i)
            if (PtInRect(&hit_[i], pt)) {
                if (grid_ && on_pick_at) on_pick_at(first_ * GRID_ROWS + static_cast<int>(i));
                else if (!grid_ && on_pick) on_pick(static_cast<int>(i));
                break;
            }
        done = true;
    }
    return 0;
}

// ---- the replacement bar ------------------------------------------------------------------

namespace {
constexpr int BAR_PAD = 8, BAR_ITEM_X = 14, BAR_FONT = 18, BAR_ICON_W = 36;
}

void ReplacementBar::show(const std::vector<std::wstring>& items, int index, const RECT& r) {
    if (!create(L"DualityIMEReplacements")) return;
    items_ = items;
    index_ = index;
    double s = scale();
    HDC dc = GetDC(hwnd_);
    {
        Canvas cv(dc, 1.0);
        HFONT f = cv.font(BAR_FONT, L"Microsoft JhengHei UI");
        hit_.clear();
        int x = BAR_PAD, h = cv.measure(L"中", f).cy + 12;
        for (auto& item : items_) {
            int w = cv.measure(item, f).cx + 2 * BAR_ITEM_X;
            hit_.push_back(RECT{x, BAR_PAD, x + w, BAR_PAD + h});
            x += w + 4;
        }
        size_.cx = x + BAR_ICON_W;
        size_.cy = BAR_PAD * 2 + h;
    }
    ReleaseDC(hwnd_, dc);
    int w = static_cast<int>(size_.cx * s), h = static_cast<int>(size_.cy * s);
    RECT work = work_area(r);
    int x = std::max<int>(std::min<int>(r.left - static_cast<int>(BAR_PAD * s), work.right - w), work.left);
    int y = r.top - h - static_cast<int>(4 * s);
    if (y < work.top) y = r.bottom + static_cast<int>(4 * s);  // no room above: below
    place(x, y, w, h);
}

void ReplacementBar::draw(HDC dc, int w, int h) {
    const Theme& th = current_theme();
    double s = scale();
    Canvas cv(dc, s);
    cv.box(0, 0, w / s, h / s, th.key, th.border, 10);
    HFONT f = cv.font(BAR_FONT, L"Microsoft JhengHei UI");
    for (size_t i = 0; i < items_.size() && i < hit_.size(); ++i) {
        const RECT& r = hit_[i];
        bool on = static_cast<int>(i) == index_;
        if (on) cv.box(r.left, r.top, r.right - r.left, r.bottom - r.top, th.accent, th.accent, 6);
        cv.text(items_[i], r.left, r.top, r.right - r.left, r.bottom - r.top, on ? th.accent_text : th.text, f,
                DT_CENTER | DT_VCENTER | DT_SINGLELINE);
    }
    // ↑ to go in; once in, ↵ takes the highlighted one
    cv.text(index_ < 0 ? L"↑" : L"↵", w / s - BAR_ICON_W, 0, BAR_ICON_W - 4, h / s, th.label,
            cv.font(BAR_FONT, L"Segoe UI Symbol"), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
}

LRESULT ReplacementBar::message(UINT msg, WPARAM, LPARAM lp, bool& done) {
    done = false;
    if (msg == WM_LBUTTONUP) {
        double s = scale();
        POINT pt{static_cast<LONG>(GET_X_LPARAM(lp) / s), static_cast<LONG>(GET_Y_LPARAM(lp) / s)};
        for (size_t i = 0; i < hit_.size(); ++i)
            if (PtInRect(&hit_[i], pt) && on_pick) {
                on_pick(static_cast<int>(i));
                break;
            }
        done = true;
    }
    return 0;
}

// ---- the Tab hint -------------------------------------------------------------------------

namespace {
constexpr int HINT_FONT = 16, HINT_PAD_X = 7, HINT_PAD_Y = 3;
}

void HintWindow::show(const std::wstring& text, const RECT& r, UINT ms, bool follow_caret) {
    if (!create(L"DualityIMEHint")) return;
    text_ = text;
    double s = scale();
    HDC dc = GetDC(hwnd_);
    SIZE sz;
    {
        Canvas cv(dc, s);
        sz = cv.measure(text, cv.font(HINT_FONT, L"Microsoft JhengHei UI"));
    }
    ReleaseDC(hwnd_, dc);
    w_ = static_cast<int>((sz.cx + 2 * HINT_PAD_X) * s);
    h_ = static_cast<int>((sz.cy + 2 * HINT_PAD_Y) * s);
    move_to(r);
    KillTimer(hwnd_, TIMER_HIDE);
    if (ms) SetTimer(hwnd_, TIMER_HIDE, ms, nullptr);
    KillTimer(hwnd_, TIMER_CARET);
    if (follow_caret) SetTimer(hwnd_, TIMER_CARET, 40, nullptr);
}

void HintWindow::move_to(const RECT& r) {
    double s = scale();
    int x = r.right + static_cast<int>(3 * s);
    int line = r.bottom - r.top;
    int y = line >= h_ ? r.top + (line - h_) / 2 : r.bottom - h_;  // a thin caret line: line up with its bottom
    RECT work = work_area(r);
    x = std::min<int>(x, work.right - w_);
    place(x, y, w_, h_);
}

void HintWindow::draw(HDC dc, int w, int h) {
    const Theme& th = current_theme();
    double s = scale();
    Canvas cv(dc, s);
    cv.box(0, 0, w / s, h / s, th.hint, th.hint_border, 6);
    cv.text(text_, 0, 0, w / s, h / s, th.hint_text, cv.font(HINT_FONT, L"Microsoft JhengHei UI"),
            DT_CENTER | DT_VCENTER | DT_SINGLELINE);
}

LRESULT HintWindow::message(UINT msg, WPARAM wp, LPARAM, bool& done) {
    done = false;
    if (msg == WM_TIMER && wp == TIMER_HIDE) {
        KillTimer(hwnd_, TIMER_HIDE);
        hide();
        done = true;
    } else if (msg == WM_TIMER && wp == TIMER_CARET) {
        KillTimer(hwnd_, TIMER_CARET);
        GUITHREADINFO gti{sizeof gti};
        if (visible_ && GetGUIThreadInfo(GetCurrentThreadId(), &gti) && gti.hwndCaret) {
            RECT c = gti.rcCaret;
            MapWindowPoints(gti.hwndCaret, nullptr, reinterpret_cast<POINT*>(&c), 2);
            if (c.bottom > c.top) move_to(c);
        }
        done = true;
    }
    return 0;
}

// ---- the zhuyin under the keys (英文字母＋注音提示) ------------------------------------------

void ZhuyinTip::show(const std::wstring& text, const RECT& r) {
    if (!create(L"DualityIMETip")) return;
    text_ = text;
    double s = scale();
    HDC dc = GetDC(hwnd_);
    SIZE sz;
    {
        Canvas cv(dc, s);
        sz = cv.measure(text, cv.font(HINT_FONT, L"Microsoft JhengHei UI"));
    }
    ReleaseDC(hwnd_, dc);
    int w = static_cast<int>((sz.cx + 2 * HINT_PAD_X) * s);
    int h = static_cast<int>((sz.cy + 2 * HINT_PAD_Y) * s);
    int gap = static_cast<int>(2 * s);
    RECT work = work_area(r);
    int x = std::max<int>(work.left, std::min<int>(r.left, work.right - w));
    int y = r.bottom + gap;
    if (y + h > work.bottom) y = r.top - gap - h;
    place(x, y, w, h);
}

void ZhuyinTip::draw(HDC dc, int w, int h) {
    const Theme& th = current_theme();
    double s = scale();
    Canvas cv(dc, s);
    cv.box(0, 0, w / s, h / s, th.accent, th.accent, 4);
    cv.text(text_, 0, 0, w / s, h / s, th.accent_text, cv.font(HINT_FONT, L"Microsoft JhengHei UI"),
            DT_CENTER | DT_VCENTER | DT_SINGLELINE);
}

// ---- symbol keyboard ----------------------------------------------------------------------

namespace {
constexpr int UNIT = 56, GAP = 6, KEY_H = 50, PAD = 14, TAB_H = 46;

std::wstring keyboard_state_path() {
    wchar_t* appdata = nullptr;
    std::wstring p;
    if (SUCCEEDED(SHGetKnownFolderPath(FOLDERID_RoamingAppData, 0, nullptr, &appdata)))
        p = std::wstring(appdata) + L"\\DualityIME\\keyboard.json";
    CoTaskMemFree(appdata);
    return p;
}
}  // namespace

void SymbolKeyboard::build() {
    items_.clear();
    int width = 13 * UNIT + 13 * GAP + static_cast<int>(UNIT * 1.5);  // row 0: 13 keys + backspace
    int y0 = PAD + TAB_H;
    int index = 0;
    static const wchar_t* left_labels[4] = {nullptr, L"Tab", L"Caps", L"Shift"};
    static const wchar_t* right_labels[4] = {L"⬅", nullptr, L"Enter", L"Shift"};
    for (int r = 0; r < 4; ++r) {
        int y = y0 + r * (KEY_H + GAP);
        int x = PAD;
        int n = static_cast<int>(std::char_traits<char>::length(symbols::ROWS[r]));
        int left, right;
        if (r == 0) {
            left = 0;
            right = width - 13 * UNIT - 13 * GAP;
        } else if (r == 1) {
            left = static_cast<int>(UNIT * 1.5);
            right = 0;
        } else {
            left = right = (width - n * UNIT - (n + 1) * GAP) / 2;
        }
        if (left) {
            items_.push_back(Item{x, y, left, KEY_H, Item::Deco, 0, left_labels[r]});
            x += left + GAP;
        }
        for (int k = 0; k < n; ++k) {
            items_.push_back(Item{x, y, UNIT, KEY_H, Item::Key, index++, nullptr});
            x += UNIT + GAP;
        }
        if (right) items_.push_back(Item{x, y, PAD + width - x, KEY_H, Item::Deco, 0, right_labels[r]});
    }
    for (int p = 0; p < static_cast<int>(symbols::page_count()); ++p)
        items_.push_back(Item{PAD + p * 112, PAD, 100, TAB_H - 12, Item::Tab, p, nullptr});
    items_.push_back(Item{PAD + width - 34, PAD + 2, 30, 30, Item::Close, 0, nullptr});
    width_ = PAD * 2 + width;
    height_ = y0 + 4 * (KEY_H + GAP) - GAP + PAD;
}

bool SymbolKeyboard::load_position(POINT& pt) const {
    std::string text;
    if (!read_file(keyboard_state_path(), text)) return false;
    bool ok = false;
    Json j = Json::parse(text, &ok);
    auto* x = j.get("x");
    auto* y = j.get("y");
    if (!ok || !x || !y || !x->is_number() || !y->is_number()) return false;
    pt = {static_cast<LONG>(x->as_number()), static_cast<LONG>(y->as_number())};
    return true;
}

void SymbolKeyboard::save_position(bool keep) {
    Json j = Json::object();
    if (keep) {
        RECT rc;
        GetWindowRect(hwnd_, &rc);
        j["x"] = Json(static_cast<int>(rc.left));
        j["y"] = Json(static_cast<int>(rc.top));
    }
    write_file_atomic(keyboard_state_path(), j.dump());  // (fails quietly in sandboxed apps)
}

void SymbolKeyboard::show(int page, int cursor, const RECT& caret) {
    bool was_visible = visible_;
    if (!create(L"DualityIMESymbols")) return;
    if (items_.empty()) build();
    page_ = page;
    cursor_ = cursor;
    if (was_visible) {
        InvalidateRect(hwnd_, nullptr, FALSE);
        return;
    }
    double s = scale();
    int w = static_cast<int>(width_ * s), h = static_cast<int>(height_ * s);
    // where the user dragged it last, else under the caret
    POINT saved;
    if (load_position(saved)) {
        POINT centre{saved.x + w / 2, saved.y + h / 2};
        if (MonitorFromPoint(centre, MONITOR_DEFAULTTONULL)) {
            place(saved.x, saved.y, w, h);
            return;
        }
    }
    RECT work = work_area(caret);
    int x = (work.left + work.right - w) / 2;
    int y = work.bottom - h - static_cast<int>(16 * s);
    if (caret.bottom > caret.top && work.top <= caret.bottom && caret.bottom <= work.bottom) {
        x = std::min<int>(std::max<int>(caret.left - w / 3, work.left), work.right - w);
        int below = caret.bottom + static_cast<int>(8 * s);
        int above = caret.top - h - static_cast<int>(8 * s);
        y = below + h <= work.bottom ? below : std::max<int>(above, work.top);
    }
    place(x, y, w, h);
}

const SymbolKeyboard::Item* SymbolKeyboard::hit(LPARAM lp) const {
    double s = scale();
    double x = GET_X_LPARAM(lp) / s, y = GET_Y_LPARAM(lp) / s;
    for (auto& it : items_)
        if (it.kind != Item::Deco && it.x <= x && x < it.x + it.w && it.y <= y && y < it.y + it.h) return &it;
    return nullptr;  // empty spot: grab to drag
}

LRESULT SymbolKeyboard::message(UINT msg, WPARAM, LPARAM lp, bool& done) {
    done = true;
    switch (msg) {
        case WM_SETCURSOR: {
            POINT pt;
            GetCursorPos(&pt);
            ScreenToClient(hwnd_, &pt);
            bool empty = !hit(MAKELPARAM(pt.x, pt.y));
            SetCursor(LoadCursorW(nullptr, empty ? IDC_SIZEALL : IDC_ARROW));
            return TRUE;
        }
        case WM_MOUSEMOVE:
            if (dragging_) {
                POINT pt;
                GetCursorPos(&pt);
                SetWindowPos(hwnd_, HWND_TOPMOST, drag_window_.x + pt.x - drag_cursor_.x,
                             drag_window_.y + pt.y - drag_cursor_.y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE);
            }
            return 0;
        case WM_LBUTTONUP:
            if (dragging_) {
                dragging_ = false;
                ReleaseCapture();
                save_position(true);
            }
            return 0;
        case WM_CAPTURECHANGED:
            dragging_ = false;
            return 0;
        case WM_LBUTTONDOWN:
        case WM_LBUTTONDBLCLK: {
            const Item* it = hit(lp);
            if (!it) {
                if (msg == WM_LBUTTONDBLCLK) {  // back to following the caret
                    save_position(false);
                } else {
                    GetCursorPos(&drag_cursor_);
                    RECT rc;
                    GetWindowRect(hwnd_, &rc);
                    drag_window_ = {rc.left, rc.top};
                    dragging_ = true;
                    SetCapture(hwnd_);
                }
                return 0;
            }
            if (it->kind == Item::Key && on_key) on_key(symbols::KEYS[it->value]);
            else if (it->kind == Item::Tab && on_page) on_page(it->value);
            else if (it->kind == Item::Close && on_close) on_close();
            return 0;
        }
    }
    done = false;
    return 0;
}

void SymbolKeyboard::draw(HDC dc, int w, int h) {
    const Theme& th = current_theme();
    double s = scale();
    Canvas cv(dc, s);
    HFONT sym_font = cv.font(19, L"Microsoft JhengHei UI"), sym_fallback = cv.font(19, L"Segoe UI Symbol");
    HFONT label_font = cv.font(11, L"Segoe UI");
    cv.box(0, 0, w / s, h / s, th.bg, th.border, 14);
    for (auto& it : items_) {
        if (it.kind == Item::Key) {
            bool on = it.value == cursor_;
            cv.box(it.x, it.y, it.w, it.h, on ? th.accent : th.key, on ? th.accent : th.key_border);
            wchar_t label[2] = {static_cast<wchar_t>(std::toupper(static_cast<unsigned char>(symbols::KEYS[it.value]))), 0};
            cv.text(label, it.x + 6, it.y + 2, it.w, 16, on ? th.accent_text : th.label, label_font,
                    DT_LEFT | DT_TOP | DT_SINGLELINE);
            std::wstring sym = wide(symbols::symbol(page_, it.value));
            HFONT f = cv.has_glyphs(sym, sym_font) ? sym_font : sym_fallback;
            cv.text(sym, it.x, it.y + 12, it.w, it.h - 12, on ? th.accent_text : th.text, f,
                    DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        } else if (it.kind == Item::Deco) {
            cv.box(it.x, it.y, it.w, it.h, th.dim, th.key_border);
            cv.text(it.label, it.x + 6, it.y + 2, it.w, 20, th.label, label_font, DT_LEFT | DT_TOP | DT_SINGLELINE);
        } else if (it.kind == Item::Tab) {
            bool on = it.value == page_;
            if (on) cv.box(it.x, it.y, it.w, it.h, th.accent, th.accent, 8);
            std::wstring name = wide(from_utf8(symbols::page_names()[it.value]));
            cv.text(name, it.x, it.y, it.w, it.h, on ? th.accent_text : th.text, cv.font(16, L"Microsoft JhengHei UI"),
                    DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        } else {
            cv.text(L"✕", it.x, it.y, it.w, it.h, th.text, cv.font(20, L"Segoe UI"), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        }
    }
}

}  // namespace sz
