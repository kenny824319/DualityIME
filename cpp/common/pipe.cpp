#include "pipe.h"

#include <sddl.h>

namespace sz {

std::wstring user_sid(HANDLE process) {
    std::wstring sid;
    HANDLE token = nullptr;
    // the process token, not a thread's impersonation token
    if (OpenProcessToken(process, TOKEN_QUERY, &token)) {
        DWORD size = 0;
        GetTokenInformation(token, TokenUser, nullptr, 0, &size);
        if (size) {
            std::string buf(size, '\0');
            if (GetTokenInformation(token, TokenUser, buf.data(), size, &size)) {
                LPWSTR text = nullptr;
                if (ConvertSidToStringSidW(reinterpret_cast<TOKEN_USER*>(buf.data())->User.Sid, &text)) {
                    sid = text;
                    LocalFree(text);
                }
            }
        }
        CloseHandle(token);
    }
    return sid;
}

std::wstring engine_pipe_name() {
    std::wstring sid = user_sid();
    return L"\\\\.\\pipe\\DualityIME." + (sid.empty() ? std::wstring(L"unknown") : sid);
}

std::wstring module_dir(HMODULE module) {
    wchar_t path[MAX_PATH * 2];
    DWORD n = GetModuleFileNameW(module, path, static_cast<DWORD>(std::size(path)));
    std::wstring p(path, n);
    size_t slash = p.find_last_of(L"\\/");
    return slash == std::wstring::npos ? L"" : p.substr(0, slash + 1);
}

}  // namespace sz
