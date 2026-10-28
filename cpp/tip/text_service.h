// The TSF text service: takes the keys, keeps the composition (the underlined
// text being typed) in the app, and shows the candidate list and hints.
// What the keys mean is decided by the engine (engine_client.h).
#pragma once
#include <windows.h>
#include <msctf.h>
#include <wrl/client.h>

#include <functional>
#include <memory>
#include <string>
#include <vector>

#include "engine_client.h"
#include "ui.h"

namespace sz {

using Microsoft::WRL::ComPtr;

class LangBarButton;

class TextService : public ITfTextInputProcessorEx,
                    public ITfThreadMgrEventSink,
                    public ITfKeyEventSink,
                    public ITfCompositionSink,
                    public ITfDisplayAttributeProvider,
                    public ITfCompartmentEventSink,
                    public ITfThreadFocusSink,
                    public ITfTextLayoutSink {
public:
    TextService();
    virtual ~TextService();

    // IUnknown
    STDMETHODIMP QueryInterface(REFIID riid, void** ppv) override;
    STDMETHODIMP_(ULONG) AddRef() override;
    STDMETHODIMP_(ULONG) Release() override;
    // ITfTextInputProcessor(Ex)
    STDMETHODIMP Activate(ITfThreadMgr* tm, TfClientId id) override;
    STDMETHODIMP ActivateEx(ITfThreadMgr* tm, TfClientId id, DWORD flags) override;
    STDMETHODIMP Deactivate() override;
    // ITfThreadMgrEventSink
    STDMETHODIMP OnInitDocumentMgr(ITfDocumentMgr*) override { return S_OK; }
    STDMETHODIMP OnUninitDocumentMgr(ITfDocumentMgr*) override { return S_OK; }
    STDMETHODIMP OnSetFocus(ITfDocumentMgr* focus, ITfDocumentMgr* prev) override;
    STDMETHODIMP OnPushContext(ITfContext*) override { return S_OK; }
    STDMETHODIMP OnPopContext(ITfContext*) override { return S_OK; }
    // ITfKeyEventSink
    STDMETHODIMP OnSetFocus(BOOL) override { return S_OK; }
    STDMETHODIMP OnTestKeyDown(ITfContext* ctx, WPARAM wp, LPARAM lp, BOOL* eaten) override;
    STDMETHODIMP OnTestKeyUp(ITfContext* ctx, WPARAM wp, LPARAM lp, BOOL* eaten) override;
    STDMETHODIMP OnKeyDown(ITfContext* ctx, WPARAM wp, LPARAM lp, BOOL* eaten) override;
    STDMETHODIMP OnKeyUp(ITfContext* ctx, WPARAM wp, LPARAM lp, BOOL* eaten) override;
    STDMETHODIMP OnPreservedKey(ITfContext*, REFGUID, BOOL* eaten) override {
        *eaten = FALSE;
        return S_OK;
    }
    // ITfCompositionSink
    STDMETHODIMP OnCompositionTerminated(TfEditCookie ec, ITfComposition* composition) override;
    // ITfDisplayAttributeProvider
    STDMETHODIMP EnumDisplayAttributeInfo(IEnumTfDisplayAttributeInfo** out) override;
    STDMETHODIMP GetDisplayAttributeInfo(REFGUID guid, ITfDisplayAttributeInfo** out) override;
    // ITfCompartmentEventSink
    STDMETHODIMP OnChange(REFGUID guid) override;
    // ITfThreadFocusSink
    STDMETHODIMP OnSetThreadFocus() override { return S_OK; }
    STDMETHODIMP OnKillThreadFocus() override;
    // ITfTextLayoutSink: the app knows where the text is now (after it changed)
    STDMETHODIMP OnLayoutChange(ITfContext* ctx, TfLayoutCode code, ITfContextView* view) override;

    // for the language bar button and the windows
    void toggle_mode();
    bool english_mode() const { return english_; }
    void show_menu(POINT pt);
    void pick_candidate(int index);        // clicked in the candidate list
    void pick_candidate_at(int index);     // clicked in the spread-out list (index in all of them)
    void candidates_op(const char* op);    // the → / ← at the top of the list: spread out / back
    void pick_replacement(int index);      // clicked in the replacement bar
    void symbol_key(char key);             // clicked on the symbol keyboard
    void symbol_page(int page);
    void close_symbols();

private:
    // engine state after the last reply
    struct State {
        std::wstring composition;
        int cursor = 0;  // in UTF-16 units
        std::vector<std::wstring> candidates;
        int cand_index = 0, cand_page = 0, cand_pages = 1;
        bool cand_expanded = false;            // → spread the list out to the right
        std::vector<std::wstring> cand_grid;   // the columns shown then, from column cand_first
        int cand_grid_index = 0, cand_first = 0;
        std::wstring hint;
        std::wstring zhuyin_tip;  // 英文字母＋注音提示: the unfinished syllable as zhuyin, under its keys
        int zhuyin_tip_at = 0;    // where its keys start in the composition (UTF-16 units)
        std::vector<std::wstring> replacements;  // the bar above the text
        int repl_index = -1;
        int symbol_page = -1, symbol_cursor = 0;
        bool can_undo = false;
    };
    struct Settings {
        bool shift_toggle = true;
        std::string start_mode = "mixed";  // switching to the IME starts in "mixed" or "english"
        bool lookup = true;
        bool game_mode = false;  // 遊戲模式: in games, keys go straight through
        std::string space = "half";
        bool full_width = false;
        bool ctrl_punct = false;  // Ctrl + a punctuation key types its full-width mark
        std::wstring cand_font = L"Microsoft JhengHei UI";
        int cand_font_size = 18;
        int cand_per_row = 1;
    };

    bool test_key_down(ITfContext* ctx, WPARAM vk, LPARAM lp);
    bool no_text_field(ITfContext* ctx);
    bool password_field(ITfContext* ctx);
    bool ctrl_punct_key(ITfContext* ctx, WPARAM vk, LPARAM lp);  // Ctrl + a key the setting ctrl_punct makes full width
    bool key_down(ITfContext* ctx, WPARAM vk, LPARAM lp);
    void key_up(WPARAM vk);
    bool wants_undo(WPARAM vk) const;
    bool is_width_key(WPARAM vk) const;
    bool other_modifier() const;
    bool direct();   // 遊戲模式 and in a game (and not typing in its chat)
    bool in_game();  // this program is a game: one Windows knows (Game Bar), or exclusive full screen
    bool just_started() const;  // this program started under 2 minutes ago
    wchar_t char_of(WPARAM vk, LPARAM lp) const;

    bool call(ITfContext* ctx, Json request);        // ask the engine, then show what it says
    bool ask(const Json& request, Json& reply);      // ask the engine only
    void read_reply(const Json& reply);
    void sync(ITfContext* ctx, const std::wstring& commit);
    bool undo(ITfContext* ctx);
    bool undo_by_keys();  // apps that show TSF only the composition: delete with Backspace

    // inside an edit session
    HRESULT edit(ITfContext* ctx, std::function<void(TfEditCookie)> fn, bool write = true);
    void set_text(TfEditCookie ec, ITfContext* ctx, const std::wstring& commit);
    bool start_composition(TfEditCookie ec, ITfContext* ctx, ITfRange* where = nullptr);
    void end_composition(TfEditCookie ec);
    void set_attribute(TfEditCookie ec, ITfContext* ctx, ITfRange* range, bool on);
    void set_caret(TfEditCookie ec, ITfContext* ctx, ITfRange* range, LONG offset);
    bool text_rect(TfEditCookie ec, ITfContext* ctx, ITfRange* range, RECT& rc);
    void place_ui(TfEditCookie ec, ITfContext* ctx);
    bool char_rect(TfEditCookie ec, ITfContext* ctx, int at, RECT& rc);
    bool in_terminal(ITfContext* ctx);
    void watch_layout(ITfContext* ctx);  // OnLayoutChange for this context (nullptr: none)
    void hide_ui();
    void reset_engine();
    bool caret_rect(ITfContext* ctx, RECT& rc);

    void set_compartment(REFGUID guid, DWORD value);
    DWORD get_compartment(REFGUID guid);
    void advise_compartment(REFGUID guid, DWORD& cookie, bool advise);
    void update_mode_ui();
    void show_mode(const std::wstring& text);  // 中／英／全形 for a moment at the caret

    LONG refs_ = 1;
    ComPtr<ITfThreadMgr> thread_mgr_;
    TfClientId client_id_ = TF_CLIENTID_NULL;
    DWORD tm_cookie_ = TF_INVALID_COOKIE, focus_cookie_ = TF_INVALID_COOKIE;
    DWORD openclose_cookie_ = TF_INVALID_COOKIE, conversion_cookie_ = TF_INVALID_COOKIE;
    ComPtr<ITfContext> layout_context_;
    DWORD layout_cookie_ = TF_INVALID_COOKIE;
    RECT last_end_rc_{}, last_tip_rc_{};  // the boxes' last right places, while the text is being typed
    bool last_end_ok_ = false, last_tip_ok_ = false;
    TfGuidAtom attr_atom_ = TF_INVALID_GUIDATOM;
    ComPtr<ITfComposition> composition_;
    ComPtr<ITfContext> comp_context_;
    ComPtr<LangBarButton> button_;
    bool button_added_ = false;
    bool setting_compartment_ = false;

    EngineClient engine_;
    State state_;
    Settings cfg_;
    bool english_ = false;
    DWORD activated_at_ = 0;  // GetTickCount() at Activate: see OnChange
    DWORD first_focus_at_ = 0;  // GetTickCount() when a document first had the focus after Activate (0: not yet)
    std::string app_;  // this program's exe name, lower case

    // Shift / Ctrl taps
    bool shift_alone_ = false, ctrl_alone_ = false;
    // 遊戲模式
    std::wstring exe_path_;           // this program, lower case
    bool known_game_ = false;         // Windows lists it as a game
    ULONGLONG games_read_at_ = 0;     // ...when that list was last read
    bool full_screen_ = false;        // a Direct3D game in exclusive full screen
    ULONGLONG full_screen_at_ = 0;
    bool game_typing_ = false;        // Shift tapped in a game: composing again (its chat)
    ULONGLONG shift_down_at_ = 0, ctrl_down_at_ = 0, ctrl_tapped_at_ = 0;

    // 拿回重選: what was sent last, where
    ComPtr<ITfRange> sent_range_;
    ComPtr<ITfContext> sent_context_;
    std::wstring sent_text_;
    ULONGLONG sent_at_ = 0;
    bool undoing_ = false;  // our Backspaces deleting the sent text are on their way

    std::unique_ptr<CandidateWindow> cand_win_;
    std::unique_ptr<ReplacementBar> bar_win_;
    std::unique_ptr<HintWindow> hint_win_;
    std::unique_ptr<ZhuyinTip> tip_win_;
    std::unique_ptr<HintWindow> mode_win_;
    std::unique_ptr<SymbolKeyboard> keyboard_;
};

}  // namespace sz
