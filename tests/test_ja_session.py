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

    def test_flush_with_list_open_sends_the_text(self):
        # (findings #163: Shift from 純日文 to 中日混打 with the list open - what is typed goes out, not lost)
        down = {"op": "key", "special": "DOWN", "ja": True}
        out = run([ON] + keys("hasi") + [down, down, {"op": "ja_flush", "keep_lock": True}])
        shown, r = out[-2]["reply"], out[-1]["reply"]
        self.assertTrue(shown["candidates"])
        # (what the screen shows goes out - the text, which moving through the list does not change, as in Chinese)
        self.assertEqual((r["commit"], r["composition"], r["candidates"]), (shown["composition"], "", []))
        self.assertTrue(r["commit"])

    def test_flush_keeping_the_lock(self):
        # (Shift between 中日混打 and 純日文: the kana lock stays - only switching to Chinese sets it back)
        out = run([ON, {"op": "ja_kana", "lock": "kata"}, {"op": "ja_flush", "keep_lock": True}] + keys("kyou"))
        self.assertEqual((out[-1]["reply"]["composition"], out[-1]["reply"]["kana_lock"]), ("キョウ", "kata"))

    def test_mixed_flag(self):
        # (中日混打's flag on a Chinese request: on only with 日文輸入 on and the data there; asked for but not
        # possible says ja_off, so the IME goes back to Chinese - findings #171)
        def flag(requests):
            return [o["mixed"] for o in run(requests) if "mixed" in o]
        self.assertEqual(flag([{"mixed": {"op": "key", "ch": "a"}}]), [{"on": False, "ja_off": False}])
        self.assertEqual(flag([{"mixed": {"op": "key", "ch": "a", "ja_mixed": True}}]), [{"on": False, "ja_off": True}])
        self.assertEqual(flag([ON, {"mixed": {"op": "key", "ch": "a", "ja_mixed": True}}]),
                         [{"on": True, "ja_off": False}])
        self.assertEqual(flag([ON, {"mixed": {"op": "key", "ch": "a", "ja_mixed": False}}]),
                         [{"on": False, "ja_off": False}])

    def test_mixed_flag_without_data(self):
        out = subprocess.run([EXE, os.path.join(ROOT, "no-such-folder")], capture_output=True, check=True,
                             input=(json.dumps(ON) + "\n" + json.dumps(
                                 {"mixed": {"op": "key", "ch": "a", "ja_mixed": True}}) + "\n").encode()).stdout
        last = json.loads(out.decode("utf-8").splitlines()[-1])
        self.assertEqual(last["mixed"], {"on": False, "ja_off": True})

    def test_context_in_engine(self):
        # (plan 5: the engine process's 純日文 reads the context, the last word sent - real data: きく after 薬が is 効く,
        # alone 聞く; since plan 5b's model いって is 行って alone too, so the example moved)
        enter = {"op": "key", "special": "ENTER", "ja": True}
        fresh = run([ON] + keys("kiku"))[-1]["reply"]["composition"]
        after = run([ON] + keys("kusuriga") + [enter] + keys("kiku"))[-1]["reply"]["composition"]
        self.assertEqual((fresh, after), ("聞く", "効く"))

    def test_switching_forgets_context(self):
        # (leaving 純日文 - Ctrl+F2 to Chinese, Shift to 中日混打 - sends what is typed and forgets the context: what is
        # typed meanwhile is not 純日文's)
        for flush in ({"op": "ja_flush"}, {"op": "ja_flush", "keep_lock": True}):
            out = run([ON] + keys("kusuriga") + [flush] + keys("kiku"))
            self.assertEqual(out[-1]["reply"]["composition"], "聞く", flush)

    def test_context_lost_forgets(self):
        # (findings #177: #37's check - the caret moved, a message sent - forgets 純日文's context too, as 中日混打's)
        enter = {"op": "key", "special": "ENTER", "ja": True}
        out = run([ON] + keys("kusuriga") + [enter, {"context_lost": True}] + keys("kiku"))
        self.assertEqual(out[-1]["reply"]["composition"], "聞く")

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
