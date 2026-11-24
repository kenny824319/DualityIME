// 兩儀輸入法 TSF input method (DualityIME64.dll / DualityIME32.dll): shared definitions.
#pragma once
#include <windows.h>
#include <msctf.h>

#include <string>

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
constexpr int IDI_ZH = 101;        // the mode button, white: for a dark taskbar
constexpr int IDI_EN = 102;
constexpr int IDI_ZH_LIGHT = 103;  // ...and black: for a light taskbar
constexpr int IDI_EN_LIGHT = 104;

// Where Windows reads the input method's own taskbar icon from (register_profile). A file, not a
// resource of the DLL: the engine puts the white or the black one there as the taskbar's theme
// changes (Windows reads the file again each time it shows the icon, but asks no code of ours).
// %ProgramData%\DualityIME\tray.ico; "" if that folder cannot be found.
std::wstring tray_icon_path();

}  // namespace sz
