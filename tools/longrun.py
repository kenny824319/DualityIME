"""Long runs (an evaluation, tools/compare.py, a log of many sentences) started through one door, so that a later code
change can never leave one silently measuring other code (2026-11-21, three times in one day, hours each: a copy's
older docs/status.md gave another natural sentence set; a 13-hour compare ran max's model under plus's parameters; a
parity run kept running on a copy after the copy was moved back).

    py -3 tools/longrun.py start --name <name> [--code <folder>] [--fixed-code "<why>"] [--log <file>] -- <command ...>
                                             returns once a background process of its own runs it (see start)
    py -3 tools/longrun.py wait <id>         follows it (its log as it grows) to its end, exit = its exit code; start
                                             it in the background: ending it ends the watching, not the run
    py -3 tools/longrun.py status            every run: running or finished, and whether it still stands for the code
    py -3 tools/longrun.py status --hook     (PostToolUse) exit 2 listing the runs a change left behind, none: exit 0
    py -3 tools/longrun.py ack <id> --reason "<what was done about it>"     (after telling the user)
    py -3 tools/longrun.py retire <id> --reason "<why>"   a run whose results are no longer used (a proof, numbers a
                                             rerun replaced): listed, never flagged again
    py -3 tools/longrun.py history <name>    how long the earlier runs of that name took (time estimates come from it)

start records, before running the command: the folder whose code it runs (--code, default the current folder), that
code's fingerprint (engine/, cpp/engine/, cpp/tools/, tools/, data/, the docs and settings page the natural sentences
come from, the built sz_trace.exe, and the folders of the .py scripts the command names - docs/superpowers/items/'s
evaluations), the command and the start time; then a hidden process Windows starts for it (WMI: not a child of the
shell, so a session or terminal ending never takes it down - 2026-11-16 a 6-hour run died that way; the user
2026-11-25: every long run so) runs it with the starter's environment, its output to --log (default the record's
<id>.log), and records the end, the exit code and how long it took. A GPU job goes the same way, through
tools/lm/gpu_run.py: start ... -- py -3 tools/lm/gpu_run.py --log <file> -- <command>. start prints how long earlier
runs of the same name took.
status compares each run with this folder's code now: a run of another folder's code, or of code changed since, no
longer stands for this folder - its numbers do not count until it is run again. --fixed-code marks a run meant to
measure other code (the engine before a change): listed, never flagged. A flagged run stays flagged (the hook says so
after every tool call) until it is acknowledged with ack.
The records are this computer's, whichever folder started them (a copy's runs are seen here too):
%LOCALAPPDATA%\\DualityIME-dev\\longrun\\<id>.json (DUALITYIME_LONGRUN_DIR moves them).
"""
import argparse
import base64
import ctypes
import datetime
import hashlib
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS = os.environ.get("DUALITYIME_LONGRUN_DIR") or os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "DualityIME-dev", "longrun")
CACHE = os.path.join(RUNS, "hash-cache.json")
# what a run's results depend on, by group (the natural sentences: README.md, docs/*.md, settings/ui - tools/
# autocorrect_eval.py natural_sentences)
GROUPS = {
    "engine": ["engine"],
    "cpp engine": ["cpp/engine", "cpp/tools"],
    "built engine": ["cpp/build/x64-Release/sz_trace.exe"],
    "tools": ["tools"],
    "data": ["data"],
    "sentences": ["README.md", "docs", "settings/ui"],
}
SKIP_DIRS = {"__pycache__", "superpowers", "architecture", ".git", "build"}  # (docs/superpowers: internal, not read)
SKIP_FILES = {"status.md", "review-backlog.md", "longrun.py"}  # (internal docs, as autocorrect_eval INTERNAL_DOCS)
sys.stdout.reconfigure(encoding="utf-8")


def _retry(fn):
    # (Windows: os.replace onto a file another process is reading fails for a moment)
    for k in range(40):
        try:
            return fn()
        except PermissionError:
            if k == 39:
                raise
            time.sleep(0.05)


def _files(folder, rel, deep=True):
    path = os.path.join(folder, rel)
    if os.path.isfile(path):
        yield rel
        return
    for d, dirs, names in os.walk(path):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS) if deep else []
        for n in sorted(names):
            if not n.endswith((".pyc", ".log")) and n not in SKIP_FILES:
                yield os.path.relpath(os.path.join(d, n), folder).replace("\\", "/")


def _digest(paths, folder, cache):
    h = hashlib.sha256()
    for f in paths:
        p = os.path.join(folder, f)
        try:
            st = os.stat(p)
        except OSError:
            continue
        key = f"{os.path.abspath(p)}|{st.st_size}|{st.st_mtime_ns}"
        digest = cache.get(key) if cache is not None else None
        if digest is None:
            try:
                with open(p, "rb") as fh:
                    digest = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                continue
            if cache is not None:
                cache[key] = digest
        h.update(f.encode() + b"\0" + digest.encode() + b"\n")
    return h.hexdigest()[:16]


def fingerprint(folder, cache=None, scripts=()):
    """{group: sha256 of its files' names and contents} of folder, and of the scripts' folders (their files, not
    below) as "scripts" (a file's hash kept by size and time in cache)."""
    out = {g: _digest([f for rel in rels for f in _files(folder, rel)], folder, cache) for g, rels in GROUPS.items()}
    if scripts:
        out["scripts"] = _digest([os.path.join(d, os.path.basename(f)) for d in sorted(scripts)
                                  for f in _files(d, ".", deep=False)], "", cache)
    return out


def _script_dirs(command, cwd):
    """The folders of the .py files the command names (its evaluation scripts), absolute."""
    out = set()
    for a in command:
        if a.endswith(".py"):
            p = a if os.path.isabs(a) else os.path.join(cwd, a)
            if os.path.isfile(p):
                out.add(os.path.normcase(os.path.dirname(os.path.abspath(p))))
    return sorted(out)


def _load_cache():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_cache(cache):
    os.makedirs(RUNS, exist_ok=True)
    if len(cache) > 50000:
        cache.clear()
    tmp = CACHE + f".{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    try:
        _retry(lambda: os.replace(tmp, CACHE))
    except OSError:  # (only a cache: the next one writes it)
        pass


def _created(pid):
    """When the process started (Windows FILETIME), None when it is gone - with the pid, who it is."""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return 0
        except OSError:
            return None
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)  # (PROCESS_QUERY_LIMITED_INFORMATION)
    if not h:
        return None
    try:
        code = ctypes.c_ulong()
        if not k.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:  # (STILL_ACTIVE)
            return None
        t = [ctypes.c_ulonglong() for _ in range(4)]
        if not k.GetProcessTimes(h, *(ctypes.byref(x) for x in t)):
            return None
        return t[0].value
    finally:
        k.CloseHandle(h)


def _alive(run):
    c = _created(run["pid"])
    return c is not None and (run.get("created") is None or c == run["created"])


def _runs():
    out = []
    if not os.path.isdir(RUNS):
        return out
    for n in sorted(os.listdir(RUNS)):
        if n.endswith(".json") and n != os.path.basename(CACHE):
            try:
                with open(os.path.join(RUNS, n), encoding="utf-8") as f:
                    out.append(json.load(f))
            except (OSError, ValueError):
                pass
    return out


def _read(rid):
    try:
        with open(os.path.join(RUNS, f"{rid}.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write(run):
    os.makedirs(RUNS, exist_ok=True)
    path = os.path.join(RUNS, f"{run['id']}.json")
    tmp = path + f".{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(run, f, ensure_ascii=False, indent=1)
    _retry(lambda: os.replace(tmp, path))


def _fmt(sec):
    sec = int(sec)
    return f"{sec // 3600}h{sec % 3600 // 60:02d}m" if sec >= 3600 else f"{sec // 60}m{sec % 60:02d}s"


def history(name):
    return [r for r in _runs() if r["name"] == name and r.get("end") and r.get("exit") == 0]


def start(args, command, detach=True):
    """Records the run, then runs it - detached (the default): in a hidden process Windows starts for it (WMI), not a
    child of this one, so the session or terminal that started it can end and it goes on (2026-11-16 a 6-hour run died
    with the session; the user 2026-11-25: every long run so); start returns once that process has it, and
    `wait <id>` follows it. detach=False runs it here, its output to the console too."""
    code = os.path.abspath(args.code or os.getcwd())
    if not os.path.isdir(os.path.join(code, "engine")):
        sys.exit(f"--code {code}: no engine/ there (the folder whose code the run runs)")
    scripts = _script_dirs(command, os.getcwd())
    cache = _load_cache()
    fp = fingerprint(code, cache, scripts)
    _save_cache(cache)
    rid = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}"
    log = args.log or (os.path.join(RUNS, f"{rid}.log") if detach else None)
    run = {"id": rid, "name": args.name, "command": command, "cwd": os.getcwd(), "code": code, "scripts": scripts,
           "fingerprint": fp, "fixed_code": args.fixed_code, "log": os.path.abspath(log) if log else None,
           "pid": os.getpid(), "created": _created(os.getpid()), "start": time.time()}
    if detach:
        run["env"] = dict(os.environ)  # (the started process has the user's default environment, not this one's)
    _write(run)
    other = os.path.normcase(code) != os.path.normcase(ROOT)
    past = history(args.name)
    print(f"[longrun {rid}] {args.name}: code of {code}" + ("  (ANOTHER FOLDER than this one)" if other else "")
          + (f"  fixed code: {args.fixed_code}" if args.fixed_code else ""), flush=True)
    print(f"[longrun {rid}] earlier runs of {args.name}: "
          + (", ".join(_fmt(r["end"] - r["start"]) for r in past[-5:]) if past else "none (no measured time yet)"),
          flush=True)
    if not detach:
        return _execute(rid, echo=True)
    worker = f'"{sys.executable}" "{os.path.abspath(__file__)}" _run {rid} --runs "{RUNS}"'
    if not _launch_hidden(worker, os.getcwd()):
        failed = _read(rid) or run
        failed.update(end=time.time(), exit=-1, fixed_code="never started: Windows did not start the process")
        _write(failed)
        sys.exit(f"[longrun {rid}] Windows did not start the background process (WMI Win32_Process.Create)")
    for _ in range(200):  # (the background process takes the record over: its pid in it)
        r = _read(rid)
        if r and r.get("detached"):
            print(f"[longrun {rid}] running in a background process of its own (pid {r['pid']}), its output in "
                  f"{r['log']}; follow it: py -3 tools/longrun.py wait {rid}", flush=True)
            return 0
        time.sleep(0.1)
    sys.exit(f"[longrun {rid}] the background process did not take the run over in 20 s (see {log})")


def _launch_hidden(command_line, cwd):
    """Starts command_line through WMI (Win32_Process.Create, its window hidden): its parent is WMI's provider, so
    neither this process nor the session above it takes it down when they end."""
    q = lambda s: "'" + s.replace("'", "''") + "'"  # noqa: E731 (a PowerShell string)
    ps = ("$si = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow = [uint16]0}; "
          f"$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{{CommandLine = {q(command_line)}; "
          f"CurrentDirectory = {q(cwd)}; ProcessStartupInformation = $si}}; exit $r.ReturnValue")
    enc = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc], capture_output=True)
    return r.returncode == 0


def _execute(rid, echo):
    """Runs the recorded command (its environment as recorded, else this one), its output to the log (and the
    console with echo), and records the end and the exit code."""
    run = _read(rid)
    env = dict(run.get("env") or os.environ, PYTHONIOENCODING="utf-8")
    log = open(run["log"], "w", encoding="utf-8", newline="") if run.get("log") else None
    p = subprocess.Popen(run["command"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=run["cwd"])
    for raw in p.stdout:
        line = raw.decode("utf-8", "replace").replace("\r\n", "\n")
        if echo:
            sys.stdout.write(line)
            sys.stdout.flush()
        if log:
            log.write(line)
            log.flush()
    p.stdout.close()
    p.wait()
    if log:
        log.close()
    done = _read(rid) or run  # (as written now: an ack made while it ran stays)
    done.update(end=time.time(), exit=p.returncode)
    _write(done)
    if echo:
        print(f"[longrun {rid}] {run['name']}: exit {p.returncode} after {_fmt(done['end'] - done['start'])}", flush=True)
    return p.returncode


def _run_detached(rid):
    """The background process start launched: takes the record over (its own pid: status sees it alive) and runs it."""
    run = _read(rid)
    if run is None:
        return 1
    run.update(pid=os.getpid(), created=_created(os.getpid()), detached=True)
    _write(run)
    return _execute(rid, echo=False)


def wait(rid, poll=5.0):
    """Follows a run to its end: its log's new lines as they come, then its exit code. Ending this ends only the
    watching - the run goes on (status, wait again)."""
    seen = 0
    while True:
        run = _read(rid)
        if run is None:
            sys.exit(f"no run {rid} (py -3 tools/longrun.py status)")
        if run.get("log") and os.path.exists(run["log"]):
            with open(run["log"], "rb") as f:
                f.seek(seen)
                chunk = f.read()
            seen += len(chunk)
            if chunk:
                sys.stdout.write(chunk.decode("utf-8", "replace"))
                sys.stdout.flush()
        if run.get("end"):
            print(f"[longrun {rid}] {run['name']}: exit {run.get('exit')} after {_fmt(run['end'] - run['start'])}",
                  flush=True)
            return run.get("exit") if isinstance(run.get("exit"), int) and run.get("exit") >= 0 else 1
        if not _alive(run):
            print(f"[longrun {rid}] {run['name']}: its process is gone before it ended", flush=True)
            return 1
        time.sleep(poll)


def _parent(pid):
    """The pid of the process that started pid (Windows), None when unknown."""
    if os.name != "nt":
        return None

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong), ("th32ProcessID", ctypes.c_ulong),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", ctypes.c_ulong),
                    ("cntThreads", ctypes.c_ulong), ("th32ParentProcessID", ctypes.c_ulong),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", ctypes.c_ulong), ("szExeFile", ctypes.c_wchar * 260)]

    k = ctypes.windll.kernel32
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    snap = k.CreateToolhelp32Snapshot(0x2, 0)  # (TH32CS_SNAPPROCESS)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return None
    try:
        e = Entry()
        e.dwSize = ctypes.sizeof(Entry)
        ok = k.Process32FirstW(ctypes.c_void_p(snap), ctypes.byref(e))
        while ok:
            if e.th32ProcessID == pid:
                return e.th32ParentProcessID
            ok = k.Process32NextW(ctypes.c_void_p(snap), ctypes.byref(e))
        return None
    finally:
        k.CloseHandle(ctypes.c_void_p(snap))


def judge(run, cache=None):
    """What no longer holds for the run: [] while it stands for this folder's code now."""
    why = []
    if os.path.normcase(run["code"]) != os.path.normcase(ROOT):
        why.append(f"runs the code of another folder ({run['code']})")
    now = fingerprint(ROOT, cache, run.get("scripts") or ())
    changed = [g for g, h in run["fingerprint"].items() if now.get(g) != h]
    if changed:
        why.append("changed here since it started: " + ", ".join(changed))
    return why


def status(hook=False):
    runs = _runs()
    if not runs:
        if not hook:
            print("no runs recorded")
        return 0
    cache = _load_cache()
    flagged = []
    for r in runs:
        state = "finished" if r.get("end") else ("running" if _alive(r) else "gone (stopped before it ended)")
        took = _fmt((r.get("end") or time.time()) - r["start"])
        # (an ack is this folder's, for the code as it was then: a copy's ack, or a change after it, flags again -
        # code-reviewer 2026-11-21)
        given = (r.get("acks") or {}).get(os.path.normcase(ROOT))
        if r.get("fixed_code"):
            verdict = f"fixed code ({r['fixed_code']})"
        elif given and given.get("fingerprint") == fingerprint(ROOT, cache, r.get("scripts") or ()):
            verdict = f"acknowledged: {given['reason']}"
        else:
            why = judge(r, cache)
            if given:
                why.append(f"changed again after the acknowledgement ({given['reason']})")
            if why:
                verdict = "NO LONGER STANDS FOR THIS CODE: " + "; ".join(why)
                if state != "gone (stopped before it ended)":
                    flagged.append((r, state, took, why))
            else:
                verdict = "stands for this code"
        if not hook:
            print(f"{r['id']}  {r['name']}  {state} {took}  exit {r.get('exit', '-')}  {verdict}")
    _save_cache(cache)
    if hook and flagged:
        lines = ["longrun: these runs no longer stand for the code in this folder (CLAUDE.md Workflow 2). Before going "
                 "on, tell the user each one, what its numbers no longer show and what rerunning costs (earlier runs: "
                 "`tools/longrun.py history <name>`); stop or rerun it, then `py -3 tools/longrun.py ack <id> --reason "
                 "\"...\"`:"]
        for r, state, took, why in flagged:
            lines.append(f"  {r['id']}  {r['name']}  ({state}, {took})  {'; '.join(why)}")
        print("\n".join(lines), file=sys.stderr)
        return 2
    return 0


def retire(rid, reason):
    """A run whose results are no longer used (a proof, numbers replaced by a rerun): listed, never flagged again."""
    r = _read(rid)
    if r is None:
        sys.exit(f"no run {rid} (py -3 tools/longrun.py status)")
    r["fixed_code"] = f"retired: {reason}"
    _write(r)
    print(f"{rid} retired: {reason}")
    return 0


def ack(rid, reason):
    r = _read(rid)
    if r is None:
        sys.exit(f"no run {rid} (py -3 tools/longrun.py status)")
    cache = _load_cache()
    r.setdefault("acks", {})[os.path.normcase(ROOT)] = {
        "reason": reason, "fingerprint": fingerprint(ROOT, cache, r.get("scripts") or ()), "time": time.time()}
    _save_cache(cache)
    _write(r)
    print(f"{rid} acknowledged: {reason}")
    return 0


def main(argv):
    command = []
    if "--" in argv:
        command = argv[argv.index("--") + 1:]
        argv = argv[:argv.index("--")]
    ap = argparse.ArgumentParser(description="Long runs through one door (see the module docstring).")
    sub = ap.add_subparsers(dest="what", required=True)
    s = sub.add_parser("start")
    s.add_argument("--name", required=True)
    s.add_argument("--code")
    s.add_argument("--fixed-code")
    s.add_argument("--log")
    st = sub.add_parser("status")
    st.add_argument("--hook", action="store_true")
    a = sub.add_parser("ack")
    a.add_argument("id")
    a.add_argument("--reason", required=True)
    rt = sub.add_parser("retire")
    rt.add_argument("id")
    rt.add_argument("--reason", required=True)
    h = sub.add_parser("history")
    h.add_argument("name")
    w = sub.add_parser("wait")
    w.add_argument("id")
    w.add_argument("--poll", type=float, default=5.0)
    r = sub.add_parser("_run")  # (the background process start launches)
    r.add_argument("id")
    r.add_argument("--runs")
    args = ap.parse_args(argv)
    if args.what == "start":
        if not command:
            sys.exit("start ... -- <command>")
        return start(args, command)
    if args.what == "wait":
        return wait(args.id, args.poll)
    if args.what == "_run":
        global RUNS, CACHE
        if args.runs:
            RUNS = args.runs
            CACHE = os.path.join(RUNS, "hash-cache.json")
        return _run_detached(args.id)
    if args.what == "status":
        if args.hook:
            try:
                return status(hook=True)
            except Exception as e:  # (a hook never breaks the session: say it and go on)
                print(f"longrun status failed: {e}", file=sys.stderr)
                return 0
        return status()
    if args.what == "ack":
        return ack(args.id, args.reason)
    if args.what == "retire":
        return retire(args.id, args.reason)
    past = history(args.name)
    if not past:
        print(f"no finished run of {args.name}")
    for r in past:
        print(f"{r['id']}  {_fmt(r['end'] - r['start'])}  {' '.join(r['command'])[:120]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
