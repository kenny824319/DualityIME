// 兩儀輸入法 TSF input method (DualityIME64.dll / DualityIME32.dll): shared definitions.
#pragma once
#include <windows.h>
#include <msctf.h>

namespace sz {

extern HINSTANCE g_module;
extern LONG g_refs;  // live objects + server locks, for DllCanUnloadNow

extern const CLSID CLSID_DualityIME;
extern const GUID GUID_Profile;
extern const GUID GUID_AttrInput;
extern const GUID GUID_LangBarButton;

constexpr LANGID kLangId = MAKELANGID(LANG_CHINESE, SUBLANG_CHINESE_TRADITIONAL);  // zh-TW
// also registered for 中文(香港): set up there (a setting), Windows takes it for another
// language, so Ctrl+Shift (between languages) reaches it from another 中文(台灣) input method
constexpr LANGID kLangIdHK = MAKELANGID(LANG_CHINESE, SUBLANG_CHINESE_HONGKONG);  // zh-HK
constexpr wchar_t kName[] = L"兩儀輸入法";

inline void DllAddRef() { InterlockedIncrement(&g_refs); }
inline void DllRelease() { InterlockedDecrement(&g_refs); }

// resources (tip.rc)
constexpr int IDI_PROFILE = 100;  // first icon: the input method's own
constexpr int IDI_ZH = 101;
constexpr int IDI_EN = 102;

}  // namespace sz
