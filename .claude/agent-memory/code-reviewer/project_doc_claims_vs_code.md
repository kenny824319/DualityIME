---
name: doc-claims-vs-code
description: README/UI text claims that have been found not to match the code (left/right Shift setting, 遊戲模式 as 純英文) - check these when docs are edited
metadata:
  type: project
---

Doc claims found wrong on review (2026-10-28, README rewrite on docs/readme):
- "可以設定只用左邊或只用右邊的 Shift": no such setting exists (only `shift_toggle` on/off; no VK_LSHIFT/VK_RSHIFT in cpp/tip). Carried over from the old README.
- 遊戲模式 "自動切換成純英文/全英文模式" (README and settings/ui/app.js 284/493/503): the code (cpp/tip/text_service.cpp `direct()`, `test_key_down`) passes keys straight through, does not toggle the mode, icon does not become "A"; a Shift tap toggles `game_typing_`, not the mode.
- "Shift+Backspace 復原" what a commit taught (decisions.md / plans, 2026-10-29): `TextService::wants_undo` returns false (cpp/tip/text_service.cpp ~464), so the app just deletes a character; `undo_commit` exists in the engine and server (`op == "undo"`) but no key reaches it. Unit tests calling `undo_commit()` do not prove a user can undo.

**Why:** user-facing docs drift from the TIP code; grep for the setting key in the four default locations proves a setting exists.
**How to apply:** when README or settings text changes, verify each "可以設定…" against engine/config.py keys, and game-mode wording against text_service.cpp.
