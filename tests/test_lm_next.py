"""tools/lm/next: the next student model of 長句選字加強 (#41) - its training text with English kept as one mark
(docs/architecture/decisions.md, #42)."""
import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEXT = os.path.join(ROOT, "tools", "lm", "next")


def load(name):
    # (by its path: tools/lm on sys.path would shadow modules of other tests)
    spec = importlib.util.spec_from_file_location(f"lm_next_{name}", os.path.join(NEXT, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TextUnitsTest(unittest.TestCase):
    def setUp(self):
        self.text = load("text")

    def test_english_and_digits_one_mark(self):
        self.assertEqual(self.text.to_units("我在用Qwen3-1.7B做蒸餾"), ["我", "在", "用", "<en>", "做", "蒸", "餾"])

    def test_full_width_folded(self):
        self.assertEqual(self.text.to_units("Ｇｉｔｈｕｂ，辨識"), ["<en>", "辨", "識"])

    def test_number_with_point(self):
        self.assertEqual(self.text.to_units("1.5倍"), ["<en>", "倍"])

    def test_space_makes_two(self):
        self.assertEqual(self.text.to_units("A - B"), ["<en>", "<en>"])

    def test_punctuation_dropped(self):
        self.assertEqual(self.text.to_units("。，"), [])
        self.assertEqual(self.text.to_units("你好，世界"), ["你", "好", "世", "界"])

    def test_mark_constant(self):
        self.assertEqual(self.text.EN, "<en>")


class PrepareNextTest(unittest.TestCase):
    def test_encode_matches_units(self):
        # (prepare_next.encode_text works on whole texts with numpy: the same units as text.to_units, per paragraph)
        import numpy as np
        prep, text = load("prepare_next"), load("text")
        vocab = ["<unk>", "<s>", "<en>"] + list("我在用做蒸餾你好世界倍")
        lut = np.zeros(0x110000, dtype=np.uint16)
        for i, ch in enumerate(vocab[3:], 3):
            lut[ord(ch)] = i
        lut[ord(prep.MARK)] = prep.EN_ID
        doc = "我在用Qwen3-1.7B做蒸餾\n你\n\n你好，世界 1.5倍\n。"
        ids = prep.encode_text(doc, lut)
        want = []
        for para in doc.split("\n"):
            u = text.to_units(para)
            if len(u) >= 2:
                want += ["<s>"] + u
        self.assertEqual([vocab[i] for i in ids], want)
        self.assertEqual(want.count("<s>"), 2)  # (你 alone: under 2 units)


class ModelsTest(unittest.TestCase):
    def setUp(self):
        try:
            import torch  # noqa: F401
        except ImportError:
            self.skipTest("no torch")
        self.m = load("models")

    def test_causal(self):
        # (changing the character at 5 changes nothing before it, in both prototypes)
        import torch
        torch.manual_seed(0)
        for model in (self.m.TinyTransformer(50, d=32, layers=2, heads=4, ffn=64),
                      self.m.GruAttn(50, emb=16, hidden=32, layers=2, heads=4)):
            model.eval()
            a = torch.randint(3, 50, (1, 12))
            b = a.clone()
            b[0, 5] = (a[0, 5] + 1 - 3) % 47 + 3
            with torch.no_grad():
                la, lb = model(a), model(b)
            self.assertTrue(torch.allclose(la[0, :5], lb[0, :5], atol=1e-5), type(model).__name__)
            self.assertFalse(torch.allclose(la[0, 5:], lb[0, 5:]), type(model).__name__)

    def test_light_budget(self):
        # light: int8 weights <= 12M, cost per character <= 1.10x the shipped XL GRU's (plan, Global Constraints)
        spec = importlib.util.spec_from_file_location("lm_train_budget", os.path.join(ROOT, "tools", "lm", "train.py"))
        train = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(train)
        xl = train.CharLM(12263, 384, 768, 2)  # (the vocabulary of today's model: 12263)
        limit = 1.10 * self.m.macs_per_step(xl, 72)
        for arch in ("tf", "ga"):
            model = self.m.build(arch, "light", 12264)
            self.assertLessEqual(self.m.count_int8_weights(model), 12_000_000, arch)
            self.assertLessEqual(self.m.macs_per_step(model, 72), limit, arch)


class IntNextTest(unittest.TestCase):
    """engine/lm_next.py: the integer inference of the next student (plan Task 13) against the float prototypes."""

    def setUp(self):
        try:
            import torch  # noqa: F401
        except ImportError:
            self.skipTest("no torch")
        from engine import lm_next
        self.ln = lm_next
        self.m = load("models")
        self.qn = load("quantize_next")

    def test_isqrt_exact(self):
        import math
        import random
        rng = random.Random(3)
        for v in [0, 1, 2, 3, 4, 15, 16, 17, 2 ** 40 - 1, 2 ** 40, 2 ** 62 + 12345] + \
                [rng.randrange(1, 2 ** 50) for _ in range(2000)]:
            self.assertEqual(self.ln.isqrt(v), math.isqrt(v), v)

    def test_clip_keeps_int32_sums(self):
        for n in (16, 384, 640, 1280, 3072):
            c = self.ln.clip_of(n)
            self.assertLess(127 * c * n, 2 ** 31)
            self.assertLessEqual(c, 32767)
        self.assertEqual(self.ln.clip_of(1280), (2 ** 31 - 1) // (127 * 1280))

    def test_load_refuses_what_cpp_refuses(self):
        # (a file loads in both engines or in neither: sizes no model has and threshold text other than a plain integer
        # are refused here as in cpp/engine/lm_next.cpp - code-reviewer 2026-11-16)
        import struct
        import tempfile
        torch = __import__("torch")
        torch.manual_seed(0)
        model = self.m.TinyTransformer(60, d=16, layers=1, heads=2, ffn=32)
        vocab = ["<unk>", "<s>", "<en>"] + [chr(0x4E00 + i) for i in range(57)]
        h, t = self.qn.quantize(model, weight=0.6, context_max=32)
        with tempfile.TemporaryDirectory() as d:
            ok = os.path.join(d, "ok.bin")
            self.qn.export(h, t, vocab, {"ㄅㄚ˙": 1234}, ok)
            self.ln.NextLM(ok)  # (the control: loads)
            for q16 in (" 5", "1_000", "５", "1" * 19, ""):
                path = os.path.join(d, "t.bin")
                self.qn.export(h, t, vocab, {"ㄅㄚ˙": 0}, path)
                data = open(path, "rb").read().replace("ㄅㄚ˙\t0".encode(), f"ㄅㄚ˙\t{q16}".encode())
                head = 4 + 4 * len(self.ln.HEADER)
                vlen, = struct.unpack_from("<I", data, head)
                tpos = head + 4 + vlen
                tlen, = struct.unpack_from("<I", data, tpos)
                data = data[:tpos] + struct.pack("<I", len(f"ㄅㄚ˙\t{q16}".encode())) + data[tpos + 4:]
                open(path, "wb").write(data)
                with self.assertRaises(ValueError, msg=repr(q16)):
                    self.ln.NextLM(path)
            raw = bytearray(open(ok, "rb").read())
            layers_at = 4 + 4 * self.ln.HEADER.index("layers")
            struct.pack_into("<I", raw, layers_at, 0)
            bad = os.path.join(d, "layers0.bin")
            open(bad, "wb").write(bytes(raw))
            with self.assertRaises(ValueError):
                self.ln.NextLM(bad)

    def test_large_input_shifted_not_clipped(self):
        # (a trained model's rare large activation - 37 against the down projection's clip of 12.9 - was cut, moving
        # every score after it: now the vector is shifted right to fit and the bits given back at requant)
        import numpy as np
        from engine.lm import requant
        rng = np.random.default_rng(0)
        n = 1280
        w = rng.integers(-127, 128, (8, n)).astype(np.int64)
        m = rng.integers(1 << 16, 1 << 18, 8).astype(np.int32)
        x = rng.integers(-3000, 3000, n).astype(np.int64)
        self.assertEqual(self.ln.shift_of(x, self.ln.clip_of(n)), 0)
        xs, k = self.ln.shifted(x, n)
        np.testing.assert_array_equal(self.ln.requant_shift(w @ xs, m, k), requant(w @ x, m))  # (k = 0: requant)
        x[7] = 38273  # (tf_light_s1's largest, Q10)
        xs, k = self.ln.shifted(x, n)
        self.assertEqual(k, 2)
        self.assertLessEqual(int(np.abs(xs).max()), self.ln.clip_of(n))
        exact = (w @ x) * m.astype(np.int64) / (1 << 24)
        got = self.ln.requant_shift(w @ xs, m, k)
        # (within what the 2 low bits dropped from each input can move: n * 127 * 3 / 4, times the multiplier)
        bound = n * 127 * 3 / 4 * m.astype(np.float64) / (1 << 24) + 1
        self.assertTrue((np.abs(got - exact) <= bound).all(), (got - exact, bound))
        self.assertEqual(self.ln.shift_of(np.array([-13211]), 13210), 1)  # (floor: -13211 >> 1 = -6606)

    def roundtrip(self, model):
        import tempfile
        import numpy as np
        import torch
        torch.manual_seed(0)
        vocab = ["<unk>", "<s>", "<en>"] + [chr(0x4E00 + i) for i in range(model.cfg["vocab"] - 3)]
        h, t = self.qn.quantize(model, weight=0.65, context_max=32)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "lm.bin")
            self.qn.export(h, t, vocab, {"ㄅㄚ˙": 1234}, path)
            lm = self.ln.NextLM(path)
        self.assertEqual((lm.lm_weight, lm.context_max, lm.order_thresholds), (0.65, 32, {"ㄅㄚ˙": 1234}))
        for name, a in t.items():  # (every array read back as exported: the slopes, Q16, were once written as int8)
            np.testing.assert_array_equal(lm.t[name], a, err_msg=name)
        self.assertEqual([n for n in t if self.ln.is_int8(n)], [n for n in t if n.rstrip("0123456789").endswith("_q")
                                                                and t[n].dtype == np.int8])
        rng = np.random.default_rng(1)
        dev = np.concatenate([np.concatenate(([1], rng.integers(3, model.cfg["vocab"], 30))) for _ in range(20)])
        return self.qn.check(model, lm, dev.astype(np.uint16), 12, model.cfg["vocab"])

    def test_transformer_int_matches_float(self):
        r = self.roundtrip(self.m.TinyTransformer(60, d=32, layers=2, heads=4, ffn=64))
        self.assertLess(r["mean_diff"], 0.05, r)
        self.assertLess(r["clear_best_differs"], 0.02, r)  # (an untrained model's candidates are near ties)
        self.assertEqual(r["clipped"], 0, r)

    def test_gru_attention_int_matches_float(self):
        import torch
        torch.manual_seed(0)
        model = self.m.GruAttn(60, emb=16, hidden=32, layers=2, heads=4)
        with torch.no_grad():  # (its embedding starts at N(0, 1): logits that large make every small error large)
            model.emb.weight.mul_(0.1)
        r = self.roundtrip(model)
        self.assertLess(r["mean_diff"], 0.05, r)
        self.assertLess(r["clear_best_differs"], 0.02, r)  # (an untrained model's candidates are near ties)
        self.assertEqual(r["clipped"], 0, r)


if __name__ == "__main__":
    unittest.main()
