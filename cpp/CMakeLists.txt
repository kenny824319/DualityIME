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
    engine/config.cpp engine/symbols.cpp engine/composer.cpp engine/lm.cpp engine/lm_next.cpp engine/lm_pool.cpp
    engine/candlayout.cpp
    engine/script.cpp
    engine/ja_romaji.cpp engine/ja_dict.cpp engine/ja_convert.cpp engine/ja_composer.cpp engine/ja_session.cpp
    engine/ja_mixed.cpp engine/ja_bigram.cpp engine/ja_predict.cpp)
target_include_directories(sz_engine PUBLIC engine)

# #36: the Japanese conversion on romaji lines (tools/ja_compare.py runs it beside tools/ja_convert.py)
add_executable(sz_ja tools/ja_tool.cpp)
target_link_libraries(sz_ja sz_engine)
# ...and the engine process's Japanese requests without the pipe (tests/test_ja_session.py)
add_executable(sz_jasession tools/ja_session_test.cpp)
target_link_libraries(sz_jasession sz_engine)

add_executable(sz_trace tools/trace.cpp common/anim_image.cpp)
target_link_libraries(sz_trace sz_engine ole32 windowscodecs bcrypt shell32)

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
    tip/dllmain.cpp tip/text_service.cpp tip/langbar.cpp tip/ui.cpp tip/appearance.cpp tip/engine_client.cpp tip/key_record.cpp
    common/pipe.cpp common/anim_image.cpp tip/tip.def tip/tip.rc)
target_include_directories(${TIP_NAME} PRIVATE common tip)
target_link_libraries(${TIP_NAME} sz_engine ole32 oleaut32 uuid user32 gdi32 shell32 dwmapi advapi32 imm32 msimg32 d2d1 dwrite
                      windowscodecs)

# the shared key record the input method keeps (#73), on its own
add_executable(sz_keytest tools/key_record_test.cpp tip/key_record.cpp)
target_include_directories(sz_keytest PRIVATE tip)
target_link_libraries(sz_keytest advapi32)

# the input method's windows drawn in a process of their own and saved as pictures (look at a window change without
# installing the DLL: #69, #73)
add_executable(sz_uishot tools/ui_shot.cpp tip/ui.cpp tip/appearance.cpp common/anim_image.cpp)
target_include_directories(sz_uishot PRIVATE common tip)
target_link_libraries(sz_uishot sz_engine ole32 user32 gdi32 shell32 dwmapi advapi32 msimg32 windowscodecs d2d1 dwrite psapi)

# end-to-end test against the registered input method
add_executable(sz_tsftest tools/tsf_test.cpp)
target_include_directories(sz_tsftest PRIVATE tip)
target_link_libraries(sz_tsftest sz_engine ole32 user32 imm32)
