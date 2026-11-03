---
name: tip-setting-places
description: A setting the TIP reads lives in 7 places, not the 4 CLAUDE.md lists: + app.js toggle, engine_main cfg_json list, TIP read_reply flag default and the cfg_ struct default
metadata:
  type: project
---

A setting the TIP (cpp/tip) acts on (shift_toggle, ctrl_full_punct, space, lookup, game_mode...) is
named in more places than the four defaults CLAUDE.md lists: also `settings/ui/app.js` (cfgToggle),
`cpp/server/engine_main.cpp` `cfg_json` (the keys forwarded to the TIP), and in `cpp/tip/text_service.cpp`
`read_reply` (`flag("key", default)`) plus the struct default in `text_service.h` (used before the first reply).
Python Config and C++ Config drop unknown keys (`k in DEFAULTS`), Settings.cs RestoreBackup filters by
`Defaults`, but `ReadConfig` keeps stale keys in config.json (harmless).

**Why:** the 2026-11-03 rename ctrl_punct -> ctrl_full_punct (to flip everyone's default on) had to touch
all seven; missing cfg_json or the TIP fallback silently leaves the TIP on its own default.

**How to apply:** on any setting add/rename/default flip, grep the key across cpp/tip, cpp/server,
settings/ui, installer/src, engine, tests; check old DLLs still loaded in running apps (old TIP + new engine)
only fall back to their own default.
