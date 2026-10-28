# 兩儀輸入法 (DualityIME)

A Traditional Chinese Zhuyin input method for Windows 10/11 (64-bit) that mixes Chinese and English
without Shift. Two engines: the Python one (`engine/`, tests in `tests/`) is the reference, the C++ one
ships (`cpp/engine/`, the TSF text service `cpp/tip/`, the engine process `cpp/server/`). The installer
and settings tool are C# (`installer/src/`); the settings window is a web page (`settings/ui/`) in
WebView2. `README.md` is the user manual. Users install from GitHub Releases
(`DualityIME-X.Y.Z-setup.exe`, repo `kenny824319/DualityIME`); built files (`bin/`, `out/`) are not committed.

## Invariants
- An engine change goes into both `engine/` and `cpp/engine/`, the same way: `tools/compare.py` types
  the same scripts into both and must print "same" (output, `user.json`, `keys.json`).
- A setting's default lives in four places that must agree: `engine/config.py`,
  `cpp/engine/config.cpp`, `installer/src/Settings.cs` (defaults and the backup), `settings/ui/bridge.js`.
- The user's data is `%APPDATA%\DualityIME` (`user.json`, `keys.json`, `dictionary.json`,
  `config.json`). Tests and tools work in temporary folders, never there.
- An older engine rewriting `user.json` drops fields it does not know: new kinds of memory get a file
  of their own (`keys.json`: 校正記憶 and 代碼記憶).
- With 注音自動校正 off the engine behaves as before the feature, except 代碼記憶 ("en" only).
- The installer `DualityIME-X.Y.Z-setup.exe` comes only from `installer\build.ps1 -Release` (after
  `cpp\publish.cmd`); a Release carries only one built by `tools\release.ps1`. It is never committed.
- 2.x data is converted in two places only: `installer/src/Migrate.cs` (the installer, importing a 2.x
  backup) and the engine's one-time move for another account (it runs `settings.exe --migrate-user`,
  i.e. Migrate.cs again). Both engines and the settings page read the current formats only.
- The GUIDs (CLSID, profile, AttrInput) never change: Windows keeps the keyboard lists and the default.
- User-facing text (UI, errors, README, user docs) is Traditional Chinese; code, comments and
  identifiers are English. `README.md` and `docs/settings-ui-kit-text.md` follow the features and UI text.

## Commands (Git Bash, repo root)
- Full check, required before reporting a code change as done:
  `py -3 .claude/scripts/check.py`
  (steps unittest, cpp build (`cpp\build.cmd`), compare (`tools/compare.py`, 5 settings), settings
  (`tools\test_settings.ps1 -Source`), setup (`tools\test_setup.ps1`), release (`tools\test_release.ps1`);
  about 5 minutes, most of it the Python side of compare.py: 5 settings × ~40 s)
- One test while iterating: `py -3 -m unittest tests.test_engine.AutocorrectTest`
- Scoring changes (中英判斷、注音自動校正): `py tools/tune_english.py`, `py tools/autocorrect_eval.py`
  (English changed must stay 0, Chinese changed ≤ 1%), `py tools/code_memory_eval.py` (240/240).
- Settings tool: `powershell -ExecutionPolicy Bypass -File tools\test_settings.ps1 -Source` compiles
  `installer\src` (Settings.cs, Migrate.cs, Updater.cs) with `tools/settings_test.cs` into a temporary folder
  (migration, backups, the updater on fake release lists); without `-Source` it tests `bin\settings.exe`
  (`installer\build.ps1` first) and also renders every page in headless Edge.
- Installer and release script logic: `tools\test_setup.ps1`, `tools\test_release.ps1` (install nothing,
  release nothing).
- Builds: `cpp\publish.cmd` (x64 + x86 DLLs, engine → `bin\`); `installer\build.ps1` (`bin\settings.exe`,
  `bin\lookup.exe` for development); `installer\build.ps1 -Release` (after publish.cmd: the installer
  `out\DualityIME-<VERSION>-setup.exe`, carrying `out\payload.zip`).
- End-to-end through TSF (runs without asking, the user's call 2026-10-27: it stops the engine the user
  types with and types keys into a window — say before starting so the user keeps off the keyboard):
  `powershell -ExecutionPolicy Bypass -File cpp\tools\e2e.ps1 [cases.txt] [-Config file.json]`;
  `cpp\tools\tsf_test.txt` (defaults), `cpp\tools\autocorrect_cases.txt` with
  `-Config cpp\tools\autocorrect_config.json`.

## Workflow
1. For multi-file changes, show a plan before editing.
   - A rule whose boundary the request leaves open (inclusive or exclusive limit,
     time zone, existing data, an unusual state): list those cases with the result
     you would assume and ask before writing their tests — tests built on your own
     guess pass and still miss what was meant. Only cases you would be guessing.
2. Run the full check. Report the commands run and their results (pass counts,
   failures), and state what is unverified (e.g. "not tried in the running app").
   Never say done or fixed without that evidence.
   - Every claim carries its evidence or its label in the same sentence: measured
     (you ran it), read (`file:line`), inferred, or a guess. Never hand the user a
     check you could run yourself.
   - A user-visible change: verify it on the real surface. No live-check skill yet
     (`/collab:live-check` creates one): `cpp/tools/e2e.ps1` types into a real text box through
     TSF; say what was not tried in the running app.
   - Reporting a number you measured (speed, throughput, memory, accuracy):
     `/collab:benchmark` first.
   - Debugging: two fixes that share one premise failed the same check → write the
     premise down and test it directly before a third fix.
3. Review every code change before reporting — don't judge the size yourself, run
   `py -3 .claude/scripts/review_level.py` (thresholds in its docstring).
   Implementing a task from a plan counts as large regardless.
   - Always first: the project's `code-reviewer` agent; fix what it confirms.
   - large: then the built-in `/code-review high`, **once per change**. List its
     findings for the user, each marked "fix" (wrong behaviour a user or a test
     would hit) or "skip" (uncertain, unlikely or cosmetic), in the user's
     language, and let them choose. Before marking: trace each finding to a real
     caller or input (a "what if X is null" that no caller can reach is skip);
     "I would have written it differently" without a concrete failure is skip; all
     findings minor means the change is fine, say so. Findings on correctness or
     security deserve the closest look even from one reviewer. Fix the chosen ones; add the others to
     `docs/review-backlog.md`, one line each. Re-check fixes with `code-reviewer`
     only: never re-run `/code-review` on the same change. Reviews serve feature
     work, not the other way round.
   - `/code-review max` only when the user says they are preparing a release (or
     asks for it); never offer it otherwise. `/code-review ultra` bills usage
     credits: only the user starts it.
   - Report which reviews ran and what each found.
4. Finish with `/collab:commit-prep`. **In this repo Claude commits and pushes** (the user's decision
   for this side project, 2026-10-27; it overrides "git read-only / the user commits" in
   `~/.claude/CLAUDE.md` here only): once the full check passed with everything committed, commit in the
   user's message format and push. Branches: work on a local feature branch, merge it into `develop` when
   verified, push `develop`; `main` holds the releases. guard_bash runs `.claude/scripts/push_audit.py` before every push and
   blocks it if the check record does not match what is pushed, or the pushed code adds debug logging,
   key recording, the network or a new kind of file, or the IME's debug log is on (`%TEMP%\dualityime_debug.log`).
   Fix what it finds; an exception is the user's to add (`.claude/push-audit-allow.txt`).
5. Releasing (`tools\release.ps1 -Channel develop|main -Phase prepare|publish [-Repo] [-Version] [-WhatIf]`):
   `-Phase prepare` bumps `VERSION` (develop X.Y.(Z+1), main X.(Y+1).0), commits it, runs the full check,
   `cpp\publish.cmd` and `installer\build.ps1 -Release` (develop with nothing packaged changed — `cpp/`, `installer/`, `settings/`, `data/`,
   `VERSION` — does nothing); then Claude runs `git push` itself (so guard_bash and push_audit check it);
   then `-Phase publish` creates the Release `vX.Y.Z` with the installer (develop = Pre-release; the tag is
   made by GitHub, never locally). Pushing `develop` and creating its Pre-release need no asking (the
   user's decision, 2026-10-27). `-Channel develop -Phase prepare` stops while `origin/main` has commits
   `develop` lacks (merge main back first); `-Channel main -Phase publish` prints the commands that bring
   `develop` up to `main` (it runs none of them). Releasing to `main` (merging `develop` into it, `-Channel main`) is the
   user's call: only when they say so. The script never pushes and never deletes a tag or a Release; if a
   step fails it stops and says where, and the user decides what to do with what was created.

## Ask the user first
- Installing, removing or updating the input method: `DualityIME-X.Y.Z-setup.exe`, `uninstall.exe`,
  `installer\*.ps1` (administrator rights, the language list, the default input method). guard_bash asks.
- Releasing to `main` (see Workflow 5).
- `cpp\register.cmd`, `cpp\deploy.cmd`: register the IME / swap its files and stop the engine. guard_bash asks.
- Building the bigram from the corpus (`tools/build_bigram.py`): long; not on the company computer.
- Anything that deletes or rewrites what is on GitHub (`gh repo delete`, `gh api -X DELETE`,
  `git push --force` / `--delete` / `:branch` / `+branch` / `--mirror`): never run it; write it out for
  the user. guard_bash blocks it.
- Changing the push rules (`.claude/hooks/`, `.claude/scripts/push_audit.py`,
  `.claude/push-audit-allow.txt`): propose it; the user makes the change. guard_paths blocks edits.

## Gotchas
- Git Bash heredocs eat backslashes in scripts (`\t`, `\a` became a tab and a bell): write a
  script with the Write tool and run the file.
- C++ `Json` keeps object members in a vector: adding a member moves the others, so a `Json&` to a
  sibling dies. Fill a member completely before adding the next (`CorrectionMemory::save`).
- PowerShell 5.1 strips the double quotes of arguments to `powershell.exe`: pass JSON as a file
  (`e2e.ps1 -Config`).
- On the company computer headless Edge prints nothing (company policy, even for a one-line page): the
  page check of `test_settings.ps1` can't run there.
- The IME's debug log (`debug()` in `cpp/tip/text_service.cpp`) writes only while `%TEMP%\dualityime_debug.log`
  exists, and never what is typed (sizes, positions, kinds). Create the file to debug, delete it after.
- claude-kit's `tests/test_guard_bash.py` fails 3 tests on this project's guard_bash by design (they
  expect `git commit` to ask; `PROJECT_GIT_POLICY = "push"` lets it run). Set it to `""` on a copy to run them.
- A tokenizer change can shift syllable numbers: picks (`overrides`) are by syllable index, so a
  pick that turns English or a number into a syllable re-numbers them (`_picks_by_key` / `_picks_back`).

## Enforced rules
Each rule paired with what makes breaking it fail. A row enforced by "nothing" is
kept only by memory: when it is broken again, propose moving it up a level (hook,
check step, test) with `/collab:correct`. A row whose mistake can no longer happen
is dropped.

| Rule | Enforced by |
|---|---|
| Don't edit `third_party/`, `bin/`, `out/`, `data/` by hand | guard_paths hook |
| Installing / removing the IME asks first (`*setup.exe`, `uninstall.exe`, `installer\*.ps1`) | guard_bash ASK `(setup\|uninstall).exe` (auto-ask), INSTALL_SCRIPTS |
| `register.cmd` / `deploy.cmd` ask first | guard_bash ASK |
| Deleting files, stopping programs, installing software ask first | guard_bash ASK |
| Everything else runs without a prompt (side project, the user's call 2026-10-27) | settings.json allows Bash / PowerShell / Edit / Write; a hook's "ask" still prompts |
| Claude commits and pushes; other git writes (reset, amend, checkout --, tag, merge, rebase) ask | guard_bash git whitelist (`PROJECT_GIT_POLICY = "push"`) |
| Nothing on GitHub is deleted or rewritten by Claude | guard_bash BLOCK (`gh repo delete`, `gh api -X DELETE`, force / delete / mirror pushes) |
| A Release is of exactly what was checked and pushed | release.ps1 (prepare runs check.py after the version commit; publish needs HEAD = `origin/<branch>`) |
| Releasing to `main` is the user's call | guard_bash asks for `git merge`; otherwise nothing |
| A push is of exactly what passed the full check | push_audit.py (check.py records the tree; guard_bash runs the audit before every push) |
| Pushed code adds no debug logging, key recording, network or new kind of file | push_audit.py (exceptions: `.claude/push-audit-allow.txt`, the user's) |
| The IME's debug log is off when pushing | push_audit.py (`%TEMP%\dualityime_debug.log` must not exist) |
| The push rules are the user's to change | guard_paths (`.claude/hooks/`, `push_audit.py`, `push-audit-allow.txt`, `last-check.json`) |
| The Python engine's tests pass | check.py step "unittest" |
| The C++ engine builds | check.py step "cpp build" |
| Both engines behave the same | check.py step "compare" |
| The settings tool, 2.x migration and updater work | check.py step "settings" |
| The installer's logic and the release script work | check.py steps "setup", "release" |
| A setting's default agrees in four places | nothing (review_level.py flags the change as large) |
| Memory file formats stay readable by older versions and exports | nothing (review_level.py flags the change as large) |
| English never turns Chinese; Chinese typed right changes ≤ 1% | nothing (`tools/autocorrect_eval.py`, run on scoring changes) |
| Tests never touch `%APPDATA%\DualityIME` | nothing |
| README and `docs/settings-ui-kit-text.md` follow the features | nothing |

When the user corrects a mistake that already happened before, fix it and suggest
`/collab:correct` so it is enforced instead of remembered.

## Knowledge map
- Why things are built this way → `docs/architecture/decisions.md`.
- What is done and what is left → `docs/status.md` (move a line to 已完成 when
  done; finished handoffs and plans are deleted after asking).
- Design work → superpowers brainstorming → `docs/superpowers/specs/`; plans in
  `docs/superpowers/plans/`; session handoff → `/collab:handoff` writes
  `docs/superpowers/progress/`, `/collab:resume` continues from it.
  `/collab:handoff`, `resume`, `correct` and `live-check` are user-only
  (`disable-model-invocation`): when one is needed, ask the user to type it;
  never say it does not exist, and never imitate it by hand.
- Rules that keep being broken → `/collab:correct`; the table in "Enforced rules".
- The settings window's text and layout → `docs/settings-ui-kit-text.md`; review findings not fixed →
  `docs/review-backlog.md` (create it when the first one is added).

## Compaction
When compacting, preserve: modified files, active spec/plan path and task number,
failing test names, open design decisions, pending user confirmations.
