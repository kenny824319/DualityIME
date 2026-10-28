#include "langbar.h"

#include <olectl.h>

#include "globals.h"
#include "text_service.h"

namespace sz {

namespace {
// the input mode button Windows shows on the taskbar
const GUID GUID_LBI_INPUTMODE_ = {0x2C77A81E, 0x41CC, 0x4178, {0xA3, 0xA7, 0x5F, 0x8A, 0x98, 0x75, 0x68, 0xE6}};
constexpr DWORD kSinkCookie = 0x5A;
}

LangBarButton::LangBarButton(TextService* service) : service_(service) { DllAddRef(); }

STDMETHODIMP LangBarButton::QueryInterface(REFIID riid, void** ppv) {
    if (!ppv) return E_INVALIDARG;
    *ppv = nullptr;
    if (riid == IID_IUnknown || riid == IID_ITfLangBarItem || riid == IID_ITfLangBarItemButton)
        *ppv = static_cast<ITfLangBarItemButton*>(this);
    else if (riid == IID_ITfSource)
        *ppv = static_cast<ITfSource*>(this);
    if (!*ppv) return E_NOINTERFACE;
    AddRef();
    return S_OK;
}

STDMETHODIMP_(ULONG) LangBarButton::AddRef() { return InterlockedIncrement(&refs_); }

STDMETHODIMP_(ULONG) LangBarButton::Release() {
    LONG r = InterlockedDecrement(&refs_);
    if (!r) {
        delete this;
        DllRelease();
    }
    return r;
}

STDMETHODIMP LangBarButton::GetInfo(TF_LANGBARITEMINFO* info) {
    if (!info) return E_INVALIDARG;
    *info = TF_LANGBARITEMINFO{};
    info->clsidService = CLSID_DualityIME;
    info->guidItem = GUID_LBI_INPUTMODE_;
    info->dwStyle = TF_LBI_STYLE_BTN_BUTTON | TF_LBI_STYLE_SHOWNINTRAY;
    info->ulSort = 0;
    wcsncpy_s(info->szDescription, kName, _TRUNCATE);
    return S_OK;
}

STDMETHODIMP LangBarButton::GetStatus(DWORD* status) {
    if (!status) return E_INVALIDARG;
    *status = 0;
    return S_OK;
}

STDMETHODIMP LangBarButton::GetTooltipString(BSTR* tip) {
    if (!tip) return E_INVALIDARG;
    *tip = SysAllocString(L"兩儀輸入法：左鍵切換中／英，右鍵設定");
    return *tip ? S_OK : E_OUTOFMEMORY;
}

STDMETHODIMP LangBarButton::OnClick(TfLBIClick click, POINT pt, const RECT*) {
    try {
        if (!service_) return S_OK;
        if (click == TF_LBI_CLK_LEFT) service_->toggle_mode();
        else if (click == TF_LBI_CLK_RIGHT) service_->show_menu(pt);
    } catch (...) {
    }
    return S_OK;
}

STDMETHODIMP LangBarButton::GetIcon(HICON* icon) {
    if (!icon) return E_INVALIDARG;
    bool english = service_ && service_->english_mode();
    // a fresh copy each time: the caller destroys it
    *icon = static_cast<HICON>(LoadImageW(g_module, MAKEINTRESOURCEW(english ? IDI_EN : IDI_ZH), IMAGE_ICON,
                                          GetSystemMetrics(SM_CXSMICON), GetSystemMetrics(SM_CYSMICON), 0));
    return *icon ? S_OK : E_FAIL;
}

STDMETHODIMP LangBarButton::GetText(BSTR* text) {
    if (!text) return E_INVALIDARG;
    *text = SysAllocString(service_ && service_->english_mode() ? L"英" : L"中");
    return *text ? S_OK : E_OUTOFMEMORY;
}

STDMETHODIMP LangBarButton::AdviseSink(REFIID riid, IUnknown* sink, DWORD* cookie) {
    if (riid != IID_ITfLangBarItemSink) return CONNECT_E_CANNOTCONNECT;
    if (sink_) return CONNECT_E_ADVISELIMIT;
    if (FAILED(sink->QueryInterface(IID_PPV_ARGS(&sink_)))) return E_NOINTERFACE;
    *cookie = kSinkCookie;
    return S_OK;
}

STDMETHODIMP LangBarButton::UnadviseSink(DWORD cookie) {
    if (cookie != kSinkCookie || !sink_) return CONNECT_E_NOCONNECTION;
    sink_.Reset();
    return S_OK;
}

void LangBarButton::update() {
    if (sink_) sink_->OnUpdate(TF_LBI_ICON | TF_LBI_TEXT | TF_LBI_TOOLTIP);
}

}  // namespace sz
