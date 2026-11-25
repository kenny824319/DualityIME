"""tools/compare.py's command line. --cases N is a sample: the guard lets compare.py run outside tools/longrun.py only
with it (CLAUDE.md Workflow 2), so it must never add the random cases - 2026-11-25 `--cases 15` still ran 2000 random
cases on 15 settings, the max ones among them, for 78 minutes before it was stopped."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import compare  # noqa: E402


class CompareArgsTest(unittest.TestCase):
    def test_cases_is_a_sample_with_no_random_cases(self):
        a = compare.parse_args(["--cases", "15"])
        self.assertEqual((a["cases"], a["count"]), (15, 0))

    def test_a_count_with_cases_stops_and_says_how(self):
        with self.assertRaises(SystemExit) as e:
            compare.parse_args(["2000", "--cases", "3"])
        self.assertIn("longrun.py", str(e.exception.code))

    def test_count_zero_with_cases_is_the_same_sample(self):
        a = compare.parse_args(["0", "--cases", "15", "--only", "9"])
        self.assertEqual((a["cases"], a["count"], a["only"]), (15, 0, 9))

    def test_no_arguments_run_everything(self):
        a = compare.parse_args([])
        self.assertEqual((a["cases"], a["count"], a["seed"]), (None, 2000, 1))

    def test_workers_take_the_hand_cases_with_the_count(self):
        # (main_jobs passes the parent's hand-written cases to each setting's process by --hand, the count beside it)
        a = compare.parse_args(["2000", "1", "--only", "3", "--hand", "15"])
        self.assertEqual((a["cases"], a["count"], a["seed"], a["only"]), (15, 2000, 1, 3))


if __name__ == "__main__":
    unittest.main()
