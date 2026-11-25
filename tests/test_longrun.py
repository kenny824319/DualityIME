"""tools/longrun.py: a run of other code, or of code changed since it started, is flagged until acknowledged."""
import argparse
import contextlib
import io
import os
import shutil
import sys
import tempfile
import time
import unittest

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, TOOLS)
import longrun  # noqa: E402


class LongrunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "repo")
        self.copy = os.path.join(self.tmp, "copy")
        for folder in (self.root, self.copy):
            os.makedirs(os.path.join(folder, "engine"))
            os.makedirs(os.path.join(folder, "items"))
            with open(os.path.join(folder, "engine", "composer.py"), "w") as f:
                f.write("x = 1\n")
            with open(os.path.join(folder, "items", "eval.py"), "w") as f:
                f.write("print('eval')\n")
        self.saved = longrun.ROOT, longrun.RUNS, longrun.CACHE, os.environ.get("DUALITYIME_LONGRUN_DIR")
        longrun.ROOT = self.root
        longrun.RUNS = os.path.join(self.tmp, "runs")
        longrun.CACHE = os.path.join(longrun.RUNS, "hash-cache.json")
        os.environ["DUALITYIME_LONGRUN_DIR"] = longrun.RUNS  # (a child's longrun: the same records)

    def tearDown(self):
        longrun.ROOT, longrun.RUNS, longrun.CACHE, env = self.saved
        if env is None:
            os.environ.pop("DUALITYIME_LONGRUN_DIR", None)
        else:
            os.environ["DUALITYIME_LONGRUN_DIR"] = env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def record(self, rid, code, fixed=None, ended=True, scripts=()):
        run = {"id": rid, "name": "eval", "command": ["x"], "cwd": code, "code": code, "scripts": list(scripts),
               "fingerprint": longrun.fingerprint(code, None, scripts), "fixed_code": fixed, "log": None,
               "pid": os.getpid(), "created": longrun._created(os.getpid()), "start": time.time() - 60}
        if ended:
            run.update(end=time.time(), exit=0)
        longrun._write(run)

    def hook(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = longrun.status(hook=True)
        return code, err.getvalue()

    def write(self, rel, text, folder=None):
        path = os.path.join(folder or self.root, rel)
        with open(path, "w") as f:
            f.write(text)
        os.utime(path, (time.time() + 5, time.time() + 5))

    def start(self, command, log=None, detach=False):
        args = argparse.Namespace(name="eval", code=self.root, fixed_code=None, log=log)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = longrun.start(args, command, detach=detach)
        return code, out.getvalue()

    def wait(self, rid):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = longrun.wait(rid, poll=0.5)
        return code, out.getvalue()

    def test_a_detached_run_goes_on_by_itself_and_wait_gets_its_end(self):
        # (2026-11-16 a 6-hour run died with the session that started it; the user 2026-11-25: every long run in a
        # background process of its own, not the shell's)
        started = time.time()
        code, out = self.start([sys.executable, "-c", "import time; time.sleep(4); print('done'); raise SystemExit(4)"],
                               detach=True)
        self.assertEqual(code, 0)
        self.assertLess(time.time() - started, 15)  # (back before the run ends)
        (run,) = longrun._runs()
        self.assertNotIn("end", run)
        self.assertTrue(longrun._alive(run))
        self.assertNotEqual(longrun._parent(run["pid"]), os.getpid())  # (not this process's child)
        code, out = self.wait(run["id"])
        self.assertEqual(code, 4)
        self.assertIn("done", out)
        (run,) = longrun._runs()
        self.assertEqual(run["exit"], 4)
        with open(run["log"], encoding="utf-8") as f:
            self.assertEqual(f.read(), "done\n")

    def test_a_detached_run_gets_the_environment_it_was_started_with(self):
        # (a process Windows starts for us has the user's default environment: DUALITYIME_CORPUS set in the shell
        # would be lost)
        os.environ["LONGRUN_TEST_VALUE"] = "ㄅ-69"
        try:
            self.start([sys.executable, "-c", "import os; print(os.environ.get('LONGRUN_TEST_VALUE'))"], detach=True)
        finally:
            del os.environ["LONGRUN_TEST_VALUE"]
        (run,) = longrun._runs()
        self.assertEqual(self.wait(run["id"])[0], 0)
        with open(run["log"], encoding="utf-8") as f:
            self.assertEqual(f.read(), "ㄅ-69\n")

    def test_a_run_of_this_code_is_not_flagged(self):
        self.record("a", self.root)
        self.assertEqual(self.hook()[0], 0)

    def test_a_change_after_the_run_started_flags_it_until_acknowledged(self):
        self.record("a", self.root, ended=False)
        self.write("engine/composer.py", "x = 2\n")
        code, err = self.hook()
        self.assertEqual(code, 2)
        self.assertIn("changed here since it started: engine", err)
        longrun.ack("a", "told the user; rerun")
        self.assertEqual(self.hook()[0], 0)

    def test_an_ack_lasts_until_the_next_change_and_only_in_its_own_folder(self):
        # (code-reviewer 2026-11-21: with the records shared, a copy's ack silenced this folder for good)
        self.record("a", self.root, ended=False)
        self.write("engine/composer.py", "x = 6\n")
        longrun.ack("a", "told the user")
        self.assertEqual(self.hook()[0], 0)
        self.write("engine/composer.py", "x = 7\n")
        code, err = self.hook()
        self.assertEqual(code, 2)
        self.assertIn("changed again after the acknowledgement", err)
        longrun.ack("a", "told the user again")
        longrun.ROOT = self.copy  # (the copy's session sees the same record: its own ack needed there)
        self.assertEqual(self.hook()[0], 2)

    def test_a_finished_run_of_older_code_is_flagged_too(self):
        self.record("a", self.root)
        self.write("engine/composer.py", "x = 3\n")
        self.assertEqual(self.hook()[0], 2)

    def test_a_run_of_another_folder_is_flagged(self):
        # (2026-11-21: a parity run kept running on a copy after the copy was moved back)
        self.record("a", self.copy)
        code, err = self.hook()
        self.assertEqual(code, 2)
        self.assertIn("runs the code of another folder", err)

    def test_a_run_meant_for_other_code_is_listed_not_flagged(self):
        self.record("a", self.copy, fixed="the engine before the change")
        self.write("engine/composer.py", "x = 4\n")
        self.assertEqual(self.hook()[0], 0)

    def test_the_fingerprint_follows_contents_not_times(self):
        before = longrun.fingerprint(self.root)
        self.write("engine/composer.py", "x = 1\n")  # (the same text, a new time)
        self.assertEqual(longrun.fingerprint(self.root), before)
        self.write("engine/composer.py", "x = 5\n")
        self.assertNotEqual(longrun.fingerprint(self.root)["engine"], before["engine"])

    def test_the_evaluation_script_itself_counts(self):
        # (docs/superpowers/items/'s evaluations are outside the groups: the folder of a script the command names)
        command = [sys.executable, os.path.join(self.root, "items", "eval.py")]
        self.record("a", self.root, scripts=longrun._script_dirs(command, self.root))
        self.assertEqual(self.hook()[0], 0)
        self.write("items/helper.py", "y = 1\n")
        code, err = self.hook()
        self.assertEqual(code, 2)
        self.assertIn("scripts", err)

    def test_an_ack_made_while_it_ran_stays_after_it_ends(self):
        child = ("import sys; sys.path.insert(0, %r); import longrun; r = longrun._runs()[0]; "
                 "longrun.ack(r['id'], 'told the user while it ran')" % TOOLS)
        code, out = self.start([sys.executable, "-c", child])
        self.assertEqual(code, 0)
        (run,) = longrun._runs()
        # (the child's longrun has its own folder, the real repo: the ack is kept under that one)
        self.assertEqual([a["reason"] for a in run["acks"].values()], ["told the user while it ran"])
        self.assertEqual(run["exit"], 0)

    def test_start_records_the_exit_and_writes_the_log_lines_once(self):
        log = os.path.join(self.tmp, "run.log")
        code, out = self.start([sys.executable, "-c", "print('one'); print('two'); raise SystemExit(3)"], log)
        self.assertEqual(code, 3)
        with open(log, "rb") as f:
            self.assertEqual(f.read(), b"one\ntwo\n")
        (run,) = longrun._runs()
        self.assertEqual(run["exit"], 3)
        self.assertIn("end", run)


if __name__ == "__main__":
    unittest.main()
