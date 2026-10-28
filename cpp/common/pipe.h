// The named pipe between the IME (DualityIME*.dll, loaded into every app)
// and the engine (DualityIMEEngine.exe, one per signed-in user). One request
// message, one reply message, both JSON in UTF-8.
#pragma once
#include <windows.h>

#include <string>

namespace sz {

// \\.\pipe\DualityIME.<user SID>: the same name in an elevated app of the same user
std::wstring engine_pipe_name();
// the folder holding this module (DLL or exe), with a trailing backslash
std::wstring module_dir(HMODULE module);

}  // namespace sz
