---
name: winforms-mainwindowhandle
description: A WinForms form with ShowInTaskbar=false is owned by a hidden TaskbarOwner window, so Process.MainWindowHandle is 0 and CloseMainWindow() does nothing (lookup.exe's window)
metadata:
  type: project
---

`Process.MainWindowHandle` only finds visible, unowned top-level windows. WinForms gives a top-level
form with `ShowInTaskbar = false` a hidden owner (TaskbarOwner), so for lookup.exe's LookupWindow
(borderless, TopMost, ShowInTaskbar=false) `MainWindowHandle` is 0 and `CloseMainWindow()` returns
false without closing anything. Proven 2026-11-04 with a scratch winexe of the same form flags.
(A console exe probe is misleading: the conhost window is reported as its main window.)

**Why:** the fix/lookup-uia change relied on `p.CloseMainWindow()` to replace a lookup window left open
behind another program; it silently never closed it.

**How to apply:** any "close the other instance" code in installer/src (lookup.exe, settings.exe) must
find the window another way (EnumWindows by PID / title, a named event the first instance waits on),
and any `GetProcessesByName("lookup")` should filter by path like `installer/uninstall.ps1` does.
