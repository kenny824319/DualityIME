# 兩儀輸入法 C++ version. Build with cpp\build.cmd (Visual Studio Build Tools + CMake + Ninja).
cmake_minimum_required(VERSION 3.20)
project(DualityIME CXX RC)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")  # no VC++ runtime to install
add_compile_options(/utf-8 /W4 /permissive- /EHsc)
add_compile_definitions(UNICODE _UNICODE NOMINMAX WIN32_LEAN_AND_MEAN _CRT_SECURE_NO_WARNINGS)

add_library(sz_engine STATIC
    engine/util.cpp engine/json.cpp engine/zhuyin.cpp engine/lexicon.cpp engine/bigram.cpp
    engine/config.cpp engine/symbols.cpp engine/composer.cpp engine/lm.cpp)
target_include_directories(sz_engine PUBLIC engine)

add_executable(sz_trace tools/trace.cpp)
target_link_libraries(sz_trace sz_engine)

# the engine program behind the input method (x64 / ARM64 only)
add_executable(DualityIMEEngine WIN32 server/engine_main.cpp common/pipe.cpp)
target_include_directories(DualityIMEEngine PRIVATE common)
target_link_libraries(DualityIMEEngine sz_engine shell32 ole32 advapi32)

# the input method itself, loaded into every app (x64, x86 for 32-bit apps, ARM64)
if(CMAKE_SIZEOF_VOID_P EQUAL 8)
    set(TIP_NAME DualityIME64)
else()
    set(TIP_NAME DualityIME32)
endif()
add_library(${TIP_NAME} SHARED
    tip/dllmain.cpp tip/text_service.cpp tip/langbar.cpp tip/ui.cpp tip/engine_client.cpp
    common/pipe.cpp tip/tip.def tip/tip.rc)
target_include_directories(${TIP_NAME} PRIVATE common tip)
target_link_libraries(${TIP_NAME} sz_engine ole32 oleaut32 uuid user32 gdi32 shell32 dwmapi advapi32)

# end-to-end test against the registered input method
add_executable(sz_tsftest tools/tsf_test.cpp)
target_link_libraries(sz_tsftest sz_engine ole32 user32 imm32)
