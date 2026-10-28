// The 中／英 button: the input indicator on the taskbar. Left click switches
// Chinese / English, right click opens the menu (settings, 直接輸出).
#pragma once
#include <windows.h>
#include <msctf.h>
#include <wrl/client.h>

namespace sz {

class TextService;

class LangBarButton : public ITfLangBarItemButton, public ITfSource {
public:
    explicit LangBarButton(TextService* service);
    void detach() { service_ = nullptr; }
    void update();  // the mode changed: new icon and text

    STDMETHODIMP QueryInterface(REFIID riid, void** ppv) override;
    STDMETHODIMP_(ULONG) AddRef() override;
    STDMETHODIMP_(ULONG) Release() override;
    // ITfLangBarItem
    STDMETHODIMP GetInfo(TF_LANGBARITEMINFO* info) override;
    STDMETHODIMP GetStatus(DWORD* status) override;
    STDMETHODIMP Show(BOOL) override { return S_OK; }
    STDMETHODIMP GetTooltipString(BSTR* tip) override;
    // ITfLangBarItemButton
    STDMETHODIMP OnClick(TfLBIClick click, POINT pt, const RECT* area) override;
    STDMETHODIMP InitMenu(ITfMenu*) override { return S_OK; }
    STDMETHODIMP OnMenuSelect(UINT) override { return S_OK; }
    STDMETHODIMP GetIcon(HICON* icon) override;
    STDMETHODIMP GetText(BSTR* text) override;
    // ITfSource
    STDMETHODIMP AdviseSink(REFIID riid, IUnknown* sink, DWORD* cookie) override;
    STDMETHODIMP UnadviseSink(DWORD cookie) override;

private:
    LONG refs_ = 1;
    TextService* service_;
    Microsoft::WRL::ComPtr<ITfLangBarItemSink> sink_;
};

}  // namespace sz
