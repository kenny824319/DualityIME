---
name: imm-forms-async-stale
description: Reading IMM32 candidate/composition forms in caret_rect (Java/CUAS): Java answers IMN_OPENCANDIDATE asynchronously via its EDT, forms persist on the thread's IMC, so the first place_ui of a composition reads the previous one
metadata:
  type: project
---

3.2.x added `imm_rect` (text_service.cpp caret_rect fallback) and `ask_position` (SendMessage WM_IME_NOTIFY
IMN_OPENCANDIDATE to SunAwt* windows at start_composition). Java's WInputMethod.inquireCandidatePosition posts
to the AWT event thread and sets ImmSetCandidateWindow later (JDK source, recalled; not in repo).

**Why:** forms stay on the HIMC (default IMC is shared by every window of the thread) until Java sets new ones,
so right after start_composition the forms are those of the previous composition / previous field, or
CFS_DEFAULT on the first composition (window-corner fallback). Correct only once something re-runs place_ui.

**How to apply:** when reviewing caret placement changes for IMM32-layer apps, ask whether a placement made in
the same edit session can see the app's answer; suggest a live check typing ONE key in a second field.
