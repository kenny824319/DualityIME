#include "engine_client.h"

#include "globals.h"
#include "pipe.h"

namespace sz {

namespace {
constexpr DWORD kCallTimeoutMs = 800;     // one key: the engine answers in a millisecond or two
constexpr DWORD kStartWaitMs = 2500;      // the engine loads its dictionary in about half a second
constexpr DWORD kStartRetryMs = 10000;    // don't keep starting it over and over
}

EngineClient::~EngineClient() {
    close();
    if (event_) CloseHandle(event_);
}

void EngineClient::close() {
    if (pipe_ != INVALID_HANDLE_VALUE) CloseHandle(pipe_);
    pipe_ = INVALID_HANDLE_VALUE;
}

void EngineClient::start_engine() {
    // started at sign-in normally; this covers the first run and crashes.
    // (Microsoft Store apps may not start programs: they just wait for it.)
    std::wstring exe = module_dir(g_module) + L"DualityIMEEngine.exe";
    STARTUPINFOW si{sizeof si};
    PROCESS_INFORMATION pi{};
    std::wstring cmd = L"\"" + exe + L"\"";
    if (CreateProcessW(exe.c_str(), cmd.data(), nullptr, nullptr, FALSE, DETACHED_PROCESS | CREATE_NO_WINDOW,
                       nullptr, nullptr, &si, &pi)) {
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
    }
}

bool EngineClient::connect() {
    std::wstring name = engine_pipe_name();
    DWORD deadline = 0;
    for (;;) {
        pipe_ = CreateFileW(name.c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr, OPEN_EXISTING,
                            FILE_FLAG_OVERLAPPED, nullptr);
        if (pipe_ != INVALID_HANDLE_VALUE) break;
        DWORD err = GetLastError();
        if (err == ERROR_PIPE_BUSY) {
            if (!WaitNamedPipeW(name.c_str(), 200)) return false;
            continue;
        }
        if (err != ERROR_FILE_NOT_FOUND) return false;
        DWORD now = GetTickCount();
        if (!deadline) {
            if (last_start_ && now - last_start_ < kStartRetryMs) return false;
            last_start_ = now;
            start_engine();
            deadline = now + kStartWaitMs;
        }
        if (static_cast<LONG>(now - deadline) >= 0) return false;
        Sleep(50);
    }
    DWORD mode = PIPE_READMODE_MESSAGE;
    SetNamedPipeHandleState(pipe_, &mode, nullptr, nullptr);
    return true;
}

bool EngineClient::call(const Json& request, Json& reply) {
    if (pipe_ == INVALID_HANDLE_VALUE && !connect()) return false;
    if (!event_) event_ = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    std::string out = request.dump();
    std::string in(65536, '\0');
    for (int attempt = 0; attempt < 2; ++attempt) {
        OVERLAPPED ov{};
        ov.hEvent = event_;
        ResetEvent(event_);
        DWORD n = 0;
        BOOL ok = TransactNamedPipe(pipe_, out.data(), static_cast<DWORD>(out.size()), in.data(),
                                    static_cast<DWORD>(in.size()), &n, &ov);
        DWORD err = ok ? 0 : GetLastError();
        if (!ok && err == ERROR_IO_PENDING) {
            if (WaitForSingleObject(event_, kCallTimeoutMs) != WAIT_OBJECT_0) {
                CancelIo(pipe_);
                GetOverlappedResult(pipe_, &ov, &n, TRUE);
                close();  // stuck: start over with the next key
                return false;
            }
            ok = GetOverlappedResult(pipe_, &ov, &n, FALSE);
            err = ok ? 0 : GetLastError();
        }
        if (ok || err == ERROR_MORE_DATA) {
            std::string msg(in.data(), n);
            while (!ok && err == ERROR_MORE_DATA) {  // a long reply: read the rest
                DWORD more = 0;
                OVERLAPPED ov2{};
                ov2.hEvent = event_;
                ResetEvent(event_);
                ok = ReadFile(pipe_, in.data(), static_cast<DWORD>(in.size()), &more, &ov2);
                err = ok ? 0 : GetLastError();
                if (!ok && err == ERROR_IO_PENDING) {
                    if (WaitForSingleObject(event_, kCallTimeoutMs) != WAIT_OBJECT_0) {
                        CancelIo(pipe_);
                        close();
                        return false;
                    }
                    ok = GetOverlappedResult(pipe_, &ov2, &more, FALSE);
                    err = ok ? 0 : GetLastError();
                }
                msg.append(in.data(), more);
            }
            bool parsed = false;
            reply = Json::parse(msg, &parsed);
            return parsed;
        }
        // the engine went away (restarted?): reconnect once
        close();
        if (attempt == 0 && !connect()) return false;
    }
    return false;
}

}  // namespace sz
