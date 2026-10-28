// Talks to DualityIMEEngine.exe through its named pipe. Every call has a
// short time limit: if the engine is missing or stuck, keys go straight to the
// app instead of freezing it.
#pragma once
#include <windows.h>

#include <string>

#include "json.h"

namespace sz {

class EngineClient {
public:
    ~EngineClient();
    // false if the engine could not be reached in time
    bool call(const Json& request, Json& reply);
    bool connected() const { return pipe_ != INVALID_HANDLE_VALUE; }

private:
    bool connect();
    void close();
    void start_engine();
    HANDLE pipe_ = INVALID_HANDLE_VALUE;
    HANDLE event_ = nullptr;
    DWORD last_start_ = 0;  // GetTickCount when we last tried to start the engine
};

}  // namespace sz
