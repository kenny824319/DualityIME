---
name: tip-popups-outside-place-ui
description: TIP popups are also shown outside place_ui (show_mode for Shift / Shift+Space, show_menu's hint as menu owner); per-window setup done only in place_ui misses them
metadata:
  type: project
---

In `cpp/tip/text_service.cpp` most popups are shown from `place_ui`, but `show_mode` (中／英／全形 box on Shift and Shift+Space, `mode_win_`) and `show_menu` (`hint_win_` made as the menu's owner) are not. `SymbolKeyboard::show` reads `visible_` before `create()` and returns early when it was visible, so a `create()` that remakes the window leaves the new one hidden.

**Why:** 2026-11-06 the search-box fix set the popups' owner only in place_ui (`own_popups`): the 中／英 box made before any typing is unowned (hidden in the search box), and a keyboard remade on an owner change stays hidden until the next key.

**How to apply:** for any per-process or per-window popup setup in the TIP, check show_mode, show_menu and SymbolKeyboard's was_visible path too. Related: [[entry-points-bypass-key]].
