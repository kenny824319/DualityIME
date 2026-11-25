"""#36: the engine process's Japanese side (cpp/engine/ja_session.cpp) through cpp/tools/ja_session_test.cpp:
requests routed to the Japanese composer, ja_flush sending once, 日文輸入 off sending what was typed and saying so,
and the Japanese data never read while 日文輸入 is off. Needs cpp\\build.cmd first (skipped otherwise)."""
import json
import os
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "cpp", "build", "x64-Release", "sz_jasession.exe")


def run(requests):
    data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests).encode("utf-8")
    out = subprocess.run([EXE, os.path.join(ROOT, "data")], input=data, capture_output=True, check=True).stdout
    return [json.loads(line) for line in out.decode("utf-8").splitlines()]


def keys(text):
    return [{"op": "key", "ch": ch, "ja": True} for ch in text]


ON = {"set": {"ja_enabled": True}}


@unittest.skipUnless(os.path.exists(EXE), "sz_jasession.exe not built (cpp\\build.cmd)")
class JaSessionTest(unittest.TestCase):
    def test_keys_routed(self):
        out = run([ON] + keys("kyou"))
        last = out[-1]["reply"]
        self.assertEqual((last["composition"], last["ja"], last["handled"]), ("今日", True, True))
        self.assertEqual(last["cursor"], 2)

    def test_flush_sends_once(self):
        out = run([ON] + keys("kyou") + [{"op": "ja_flush"}, {"op": "ja_flush"}])
        self.assertEqual(out[-2]["reply"]["commit"], "今日")
        self.assertEqual(out[-2]["reply"]["composition"], "")
        self.assertEqual(out[-1]["reply"]["commit"], "")

    def test_enter_commits(self):
        out = run([ON] + keys("kyou") + [{"op": "key", "special": "ENTER", "ja": True}])
        self.assertEqual(out[-1]["reply"]["commit"], "今日")

    def test_candidates_in_reply(self):
        out = run([ON] + keys("hasi") + [{"op": "key", "special": "DOWN", "ja": True}])
        r = out[-1]["reply"]
        self.assertIn("箸", r["candidates"])
        self.assertEqual((r["cand_index"], r["cand_labels"]), (0, "123456789"))

    def test_kana_lock(self):
        out = run([ON, {"op": "ja_kana", "lock": "kata"}] + keys("kyou"))
        self.assertEqual(out[-1]["reply"]["composition"], "キョウ")
        self.assertEqual(out[-1]["reply"]["kana_lock"], "kata")
        out = run([ON, {"op": "ja_kana", "lock": "kata"}, {"op": "ja_flush"}] + keys("kyou"))
        self.assertEqual(out[-1]["reply"]["kana_lock"], "auto")  # (switching to Chinese: back to 自動)

    def test_disabled_goes_back(self):
        out = run([ON] + keys("kyou") + [{"set": {"ja_enabled": False}}, {"op": "key", "ch": "a", "ja": True}])
        r = out[-1]["reply"]
        self.assertEqual((r["handled"], r["ja_off"], r["commit"], r["composition"]), (False, True, "今日", ""))

    def test_nothing_read_while_off(self):
        out = run(keys("kyou") + [{"op": "ja_flush"}])
        self.assertTrue(all(o["reply"]["ja_off"] for o in out))
        self.assertFalse(any(o["loaded"] for o in out))
        out = run([ON] + keys("k"))
        self.assertTrue(out[-1]["loaded"])

    def test_not_japanese(self):
        out = run([{"op": "key", "ch": "a"}, {"op": "flush"}])
        self.assertTrue(all(o.get("not_japanese") for o in out))


if __name__ == "__main__":
    unittest.main()
