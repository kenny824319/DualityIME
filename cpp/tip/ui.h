// The IME's own windows, drawn with GDI: the candidate list, the Tab hint
// next to the text, and the symbol keyboard. They never take the focus.
// They live on the app's UI thread, like the rest of the IME.
#pragma once
#include <windows.h>

#include <functional>
#include <string>
#include <vector>

namespace sz {

struct Theme {
    COLORREF bg, key, key_border, dim, text, label, accent, accent_text, border, hint, hint_border, hint_text;
};
const Theme& current_theme();  // follows Windows' light / dark app setting

class Popup {
public:
    virtual ~Popup();
    void hide();
    bool visible() const { return visible_; }
    HWND hwnd() const { return hwnd_; }

protected:
    bool create(const wchar_t* class_name);
    void place(int x, int y, int w, int h);
    double scale() const;
    virtual void draw(HDC dc, int w, int h) = 0;
    virtual LRESULT message(UINT msg, WPARAM wp, LPARAM lp, bool& done);
    HWND hwnd_ = nullptr;
    bool visible_ = false;

private:
    static LRESULT CALLBACK proc(HWND, UINT, WPARAM, LPARAM);
    void paint();
};

// GDI helper in 96-dpi units
class Canvas {
public:
    Canvas(HDC dc, double scale);
    ~Canvas();
    HFONT font(int size, const wchar_t* face, int weight = FW_NORMAL);
    void box(double x, double y, double w, double h, COLORREF fill, COLORREF edge, int radius = 8);
    void text(const std::wstring& t, double x, double y, double w, double h, COLORREF color, HFONT font, UINT flags);
    SIZE measure(const std::wstring& t, HFONT font);
    bool has_glyphs(const std::wstring& t, HFONT font);

private:
    HDC dc_;
    double s_;
    std::vector<HGDIOBJ> objs_;
    std::vector<std::pair<std::wstring, HFONT>> fonts_;
};

class CandidateWindow : public Popup {
public:
    std::function<void(int)> on_pick;     // index on the page, clicked with the mouse
    std::function<void(int)> on_pick_at;  // index in all the candidates, clicked in the spread-out list
    std::function<void()> on_expand;      // the → at the top of the list
    std::function<void()> on_fold;        // the ← at the top of the spread-out list
    void show(const std::vector<std::wstring>& items, int index, int page, int pages, const std::wstring& font,
              int font_size, bool one_row, const RECT& text_rect);
    // spread out to the right (like the ASUS IME): columns of 9 from column `first`, four shown,
    // `columns` in all; numbers only in the column with the selection
    void show_grid(const std::vector<std::wstring>& items, int index, int first, int columns,
                   const std::wstring& font, int font_size, const RECT& text_rect);

protected:
    void draw(HDC dc, int w, int h) override;
    LRESULT message(UINT msg, WPARAM wp, LPARAM lp, bool& done) override;

private:
    void layout(HDC dc);
    void layout_grid(HDC dc);
    void place_near(const RECT& text_rect);
    std::vector<std::wstring> items_;
    std::vector<RECT> hit_;  // item rectangles in 96-dpi units
    RECT arrow_{};           // the → / ← at the top right, in 96-dpi units
    RECT track_{};           // the scroll bar of the spread-out list (empty: none)
    int index_ = 0, page_ = 0, pages_ = 1, font_size_ = 18;
    int first_ = 0, columns_ = 1;  // the spread-out list: first column shown, columns in all
    std::wstring font_ = L"Microsoft JhengHei UI";
    bool one_row_ = false, grid_ = false;
    SIZE size_{};
};

class ReplacementBar : public Popup {
public:
    std::function<void(int)> on_pick;  // clicked with the mouse
    // a row above `text_rect` (the character after the caret); index -1: not entered yet (↑)
    void show(const std::vector<std::wstring>& items, int index, const RECT& text_rect);

protected:
    void draw(HDC dc, int w, int h) override;
    LRESULT message(UINT msg, WPARAM wp, LPARAM lp, bool& done) override;

private:
    std::vector<std::wstring> items_;
    std::vector<RECT> hit_;
    int index_ = -1;
    SIZE size_{};
};

class HintWindow : public Popup {
public:
    // text right after `text_rect` (the text being typed); hides itself after `ms` if not 0.
    // follow_caret: the app gave no exact place, so move next to the system caret once
    // the app has moved it (older apps going through the IMM32 layer)
    void show(const std::wstring& text, const RECT& text_rect, UINT ms = 0, bool follow_caret = false);

protected:
    void draw(HDC dc, int w, int h) override;
    LRESULT message(UINT msg, WPARAM wp, LPARAM lp, bool& done) override;

private:
    void move_to(const RECT& r);
    std::wstring text_;
    int w_ = 0, h_ = 0;
};

class ZhuyinTip : public Popup {
public:
    // 英文字母＋注音提示: the zhuyin of the keys being typed, in a box under `at` (the first of
    // those keys in the text), like the ASUS IME; above it if there is no room below
    void show(const std::wstring& text, const RECT& at);

protected:
    void draw(HDC dc, int w, int h) override;

private:
    std::wstring text_;
};

class SymbolKeyboard : public Popup {
public:
    std::function<void(char)> on_key;   // a key clicked: the key it stands for (symbols::KEYS)
    std::function<void(int)> on_page;   // a page tab clicked
    std::function<void()> on_close;
    void show(int page, int cursor, const RECT& caret);

protected:
    void draw(HDC dc, int w, int h) override;
    LRESULT message(UINT msg, WPARAM wp, LPARAM lp, bool& done) override;

private:
    struct Item {
        int x, y, w, h;
        enum Kind { Key, Deco, Tab, Close } kind;
        int value;
        const wchar_t* label;
    };
    void build();
    const Item* hit(LPARAM lp) const;
    void save_position(bool keep);
    bool load_position(POINT& pt) const;
    std::vector<Item> items_;
    int width_ = 0, height_ = 0;
    int page_ = 0, cursor_ = 0;
    bool dragging_ = false;
    POINT drag_cursor_{}, drag_window_{};
};

}  // namespace sz
