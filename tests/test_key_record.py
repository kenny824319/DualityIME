"""The shared key record of the input method (#73, cpp/tip/key_record.h): cpp/tools/key_record_test.cpp checks how a key's
last event is judged, that another program's notes are seen, and what one note costs. Runs the built sz_keytest.exe
(cpp\\build.cmd); without it the test is skipped and says so."""
import os
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "cpp", "build", "x64-Release", "sz_keytest.exe")


class KeyRecordTest(unittest.TestCase):
    def test_the_record(self):
        if not os.path.exists(EXE):
            self.skipTest("cpp\\build.cmd first: no " + EXE)
        out = subprocess.run([EXE], capture_output=True, timeout=120)
        text = out.stdout.decode("utf-8", "replace")
        self.assertEqual(out.returncode, 0, text)
        self.assertIn("all passed", text)
        self.assertNotIn("FAIL", text)


if __name__ == "__main__":
    unittest.main()
