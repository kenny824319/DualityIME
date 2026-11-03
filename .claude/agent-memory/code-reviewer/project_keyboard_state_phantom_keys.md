---
name: keyboard-state-phantom-keys
description: GetKeyboardState in the TIP can show non-key vk codes (0xF1, Japanese IME) held for good; "any key held" checks must whitelist real keys
metadata:
  type: project
---

A TIP check of the form "any other key held" over the whole GetKeyboardState array broke Ctrl + 標點
(3.1.4, 2026-11-04): on the user's machine (Google Japanese Input installed) vk 0xF1 (VK_DBE_KATAKANA /
VK_OEM_FINISH range 0xF0-0xFD) reads as held permanently, so every Ctrl+punct went to the app.
Fixed with a `real_key` whitelist in `TextService::ctrl_punct_key` (cpp/tip/text_service.cpp).

**Why:** IMEs set mode codes (0x15-0x1F, 0xE0+, 0xF0-0xFD) in the async/thread key state without key-ups;
the thread state is resynced from async state when it becomes foreground, so they stay.

**How to apply:** any new code scanning ks[] or GetAsyncKeyState over a range (other_modifier, shortcuts)
must use the real-key whitelist, not "everything except Ctrl/Shift". Evidence tool: the `{HELD}` token
in cpp/tools/tsf_test.cpp prints what the harness thread sees as held.
