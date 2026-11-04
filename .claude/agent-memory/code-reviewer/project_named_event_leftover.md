---
name: named-event-leftover
description: Cross-process "please close" through a named AutoReset event: a requester that gives up leaves the signal set, and the owner registering its wait later consumes it and closes (lookup.exe)
metadata:
  type: project
---

lookup.exe (fix/lookup-uia, 2026-11-04) replaces an open window via `Local\DualityIME.Lookup.Close`:
the second instance Sets it and waits 1 s on the mutex. The owner Resets the event right after taking
the mutex but registers its wait only in `OnShown`, after reading the selection (up to 2 s). A Set in
between (from an instance that then timed out and quit) stays signaled; the wait fires at once and
the new window closes as it appears: no window at all. Proven by a script: start `lookup.exe --text A`,
Set the event 50 ms after the mutex exists, the owner exits within 3 s.

Also: named mutex/event created by an elevated lookup.exe (started from an admin app's TIP) get an
admin-only DACL + High label, so a normal lookup.exe throws on open while that one lives (inferred,
same mechanism as the elevated engine pipe).

**Why:** the timeouts of requester and owner differ, so "the request" can outlive the requester.

**How to apply:** for any single-instance handoff in installer/src: the requester withdraws its
request when it gives up (Reset), or the owner Resets immediately before it starts waiting, not
earlier. Probe with a leftover Set between the owner's start and its window. See [[winforms-mainwindowhandle]].
