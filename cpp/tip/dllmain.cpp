// DLL entry points: the class factory, and registering the input method with
// Windows (regsvr32, run by setup.exe as administrator).
#include <windows.h>
#include <msctf.h>
#include <olectl.h>
#include <wrl/client.h>

#include <string>

#include "globals.h"
#include "text_service.h"

namespace sz {

HINSTANCE g_module = nullptr;
LONG g_refs = 0;

// our own identity (not PIME's, not anyone else's)
const CLSID CLSID_DualityIME = {0x7F9854C9, 0xF3D8, 0x42DB, {0xBB, 0x12, 0x9F, 0x4D, 0xD0, 0x3A, 0x60, 0x90}};
const GUID GUID_Profile = {0xBFDAC825, 0xE67C, 0x4E87, {0xB6, 0x44, 0xE6, 0xB5, 0xF7, 0xB3, 0xD0, 0x8B}};
const GUID GUID_AttrInput = {0xB784454C, 0xECC8, 0x4381, {0x8D, 0x91, 0xA9, 0x51, 0x30, 0x17, 0x6C, 0xA7}};
const GUID GUID_LangBarButton = {0x603B367C, 0x53D2, 0x4F99, {0xA2, 0xBD, 0x0C, 0x1F, 0x3B, 0x13, 0x1F, 0x37}};

namespace {

using Microsoft::WRL::ComPtr;

class ClassFactory : public IClassFactory {
public:
    STDMETHODIMP QueryInterface(REFIID riid, void** ppv) override {
        if (!ppv) return E_INVALIDARG;
        *ppv = nullptr;
        if (riid == IID_IUnknown || riid == IID_IClassFactory) *ppv = static_cast<IClassFactory*>(this);
        if (!*ppv) return E_NOINTERFACE;
        AddRef();
        return S_OK;
    }
    STDMETHODIMP_(ULONG) AddRef() override { return 2; }  // a static object
    STDMETHODIMP_(ULONG) Release() override { return 1; }
    STDMETHODIMP CreateInstance(IUnknown* outer, REFIID riid, void** ppv) override {
        if (!ppv) return E_INVALIDARG;
        *ppv = nullptr;
        if (outer) return CLASS_E_NOAGGREGATION;
        auto* service = new (std::nothrow) TextService();
        if (!service) return E_OUTOFMEMORY;
        HRESULT hr = service->QueryInterface(riid, ppv);
        service->Release();
        return hr;
    }
    STDMETHODIMP LockServer(BOOL lock) override {
        if (lock) DllAddRef();
        else DllRelease();
        return S_OK;
    }
};
ClassFactory g_factory;

std::wstring guid_text(REFGUID g) {
    wchar_t buf[64];
    StringFromGUID2(g, buf, 64);
    return buf;
}

const GUID* const kCategories[] = {
    &GUID_TFCAT_TIP_KEYBOARD,
    &GUID_TFCAT_DISPLAYATTRIBUTEPROVIDER,
    &GUID_TFCAT_TIPCAP_IMMERSIVESUPPORT,     // Microsoft Store apps
    &GUID_TFCAT_TIPCAP_SYSTRAYSUPPORT,       // the 中／英 indicator on the taskbar
    &GUID_TFCAT_TIPCAP_INPUTMODECOMPARTMENT,
};

HRESULT register_com(const std::wstring& path) {
    std::wstring key = L"Software\\Classes\\CLSID\\" + guid_text(CLSID_DualityIME);
    HKEY k = nullptr, inproc = nullptr;
    if (RegCreateKeyExW(HKEY_LOCAL_MACHINE, key.c_str(), 0, nullptr, 0, KEY_WRITE, nullptr, &k, nullptr))
        return SELFREG_E_CLASS;
    RegSetValueExW(k, nullptr, 0, REG_SZ, reinterpret_cast<const BYTE*>(kName), sizeof kName);
    LONG r = RegCreateKeyExW(k, L"InprocServer32", 0, nullptr, 0, KEY_WRITE, nullptr, &inproc, nullptr);
    if (!r) {
        RegSetValueExW(inproc, nullptr, 0, REG_SZ, reinterpret_cast<const BYTE*>(path.c_str()),
                       static_cast<DWORD>((path.size() + 1) * sizeof(wchar_t)));
        const wchar_t model[] = L"Apartment";
        RegSetValueExW(inproc, L"ThreadingModel", 0, REG_SZ, reinterpret_cast<const BYTE*>(model), sizeof model);
        RegCloseKey(inproc);
    }
    RegCloseKey(k);
    return r ? SELFREG_E_CLASS : S_OK;
}

void unregister_com() {
    std::wstring key = L"Software\\Classes\\CLSID\\" + guid_text(CLSID_DualityIME);
    RegDeleteTreeW(HKEY_LOCAL_MACHINE, key.c_str());
}

HRESULT register_profile(const std::wstring& path) {
    ComPtr<ITfInputProcessorProfileMgr> mgr;
    HRESULT hr = CoCreateInstance(CLSID_TF_InputProcessorProfiles, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&mgr));
    if (FAILED(hr)) return hr;
    for (LANGID lang : {kLangId, kLangIdHK}) {
        hr = mgr->RegisterProfile(CLSID_DualityIME, lang, GUID_Profile, kName, static_cast<ULONG>(wcslen(kName)),
                                  path.c_str(), static_cast<ULONG>(path.size()), 0 /* first icon: IDI_PROFILE */, nullptr, 0,
                                  FALSE /* only where the installer adds it: enabled by default, Windows also
                                           lists it under every 中文(台灣) user's own input methods (twice) */,
                                  0);
        if (FAILED(hr)) return hr;
    }
    ComPtr<ITfCategoryMgr> cats;
    hr = CoCreateInstance(CLSID_TF_CategoryMgr, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&cats));
    if (FAILED(hr)) return hr;
    for (auto* cat : kCategories) cats->RegisterCategory(CLSID_DualityIME, *cat, CLSID_DualityIME);
    return S_OK;
}

void unregister_profile() {
    ComPtr<ITfInputProcessorProfileMgr> mgr;
    if (SUCCEEDED(CoCreateInstance(CLSID_TF_InputProcessorProfiles, nullptr, CLSCTX_INPROC_SERVER,
                                   IID_PPV_ARGS(&mgr))))
        for (LANGID lang : {kLangId, kLangIdHK}) mgr->UnregisterProfile(CLSID_DualityIME, lang, GUID_Profile, 0);
    ComPtr<ITfCategoryMgr> cats;
    if (SUCCEEDED(CoCreateInstance(CLSID_TF_CategoryMgr, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&cats))))
        for (auto* cat : kCategories) cats->UnregisterCategory(CLSID_DualityIME, *cat, CLSID_DualityIME);
}

}  // namespace
}  // namespace sz

using namespace sz;

BOOL WINAPI DllMain(HINSTANCE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_module = module;
        DisableThreadLibraryCalls(module);
    }
    return TRUE;
}

STDAPI DllGetClassObject(REFCLSID clsid, REFIID riid, void** ppv) {
    if (!ppv) return E_INVALIDARG;
    *ppv = nullptr;
    if (clsid != CLSID_DualityIME) return CLASS_E_CLASSNOTAVAILABLE;
    return g_factory.QueryInterface(riid, ppv);
}

STDAPI DllCanUnloadNow() { return g_refs > 0 ? S_FALSE : S_OK; }

STDAPI DllRegisterServer() {
    wchar_t path[MAX_PATH * 2];
    DWORD n = GetModuleFileNameW(g_module, path, static_cast<DWORD>(std::size(path)));
    std::wstring p(path, n);
    HRESULT hr = register_com(p);
    if (SUCCEEDED(hr)) hr = register_profile(p);
    if (FAILED(hr)) {
        unregister_profile();
        unregister_com();
    }
    return hr;
}

STDAPI DllUnregisterServer() {
    unregister_profile();
    unregister_com();
    return S_OK;
}
