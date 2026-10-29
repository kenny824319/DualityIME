"""Measures 代碼記憶: real codes (letters and digits) in four places, as they come out now, and
after the user put them right twice the way one does (the keys back from the bar above the text,
or 代碼是 from the list), each code on its own memory.

    py tools/code_memory_eval.py [--show] [--bigram FILE]

Places: alone, after Chinese (ji3), with Chinese right after (g4 是), after an English sentence sent
(look ). The design (docs/superpowers/specs/2026-10-27-code-memory-design.md) expects 10, 10, 60 and
12 wrong now, and none once put right twice.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from engine import load_resources  # noqa: E402
from engine.bigram import load_other  # noqa: E402
from engine.composer import Composer  # noqa: E402
from engine.lexicon import UserModel  # noqa: E402

CODES = ["gpt4", "gpt4o", "mp3", "mp4", "h264", "h265", "win10", "win11", "ps4", "ps5", "i5", "i7", "i9", "b2b",
         "3d", "4k", "usb3", "wifi6", "rtx4090", "m2", "a4", "f1", "cs2", "gta5", "x86", "x64", "utf8", "md5", "sha1",
         "s3", "ec2", "k8s", "ipv4", "ipv6", "ios17", "web3", "html5", "css3", "es6", "py3", "pm2", "4g", "5g", "v2",
         "s23", "a54", "tw1", "e3", "u4", "b4", "c4", "j2", "m1", "m3", "m4", "n95", "kn95", "r2", "t5", "z3"]
# (name, English sent before, keys before, keys after, what goes out around the code)
PLACES = [("alone", "", "", " ", ("", " ")), ("after Chinese", "", "ji3", " ", ("我", " ")),
          ("Chinese right after", "", "", "g4", ("", "是")), ("after English", "look ", "", " ", ("", " "))]


def typed(c, sent_before, keys, enter=True):
    c.reset()
    if sent_before:
        for ch in sent_before:
            c.key(ch)
        c.key(special="ENTER")
    out = ""
    for ch in keys:
        c.key(ch)
        out += c.commit
    if enter and c.composition:
        c.key(special="ENTER")
        out += c.commit
    return out


def put_right(c, code, place):
    """What a user does to get the code back, then Enter. False if there is no way."""
    name, sent_before, before, after, (lead, tail) = place
    want = lead + code + tail
    typed(c, sent_before, before + code + after, enter=False)
    if c.composition == want:
        c.key(special="ENTER")
        return True
    start = len(before)
    end = start + len(code)
    # 代碼是 from the list (the code kept as typed, the rest Chinese)
    if c.key(special="DOWN") and c.candidates:
        if want in c.candidates:
            c._pick(c.candidates.index(want))
            c.key(special="ENTER")
            return True
        c.key(special="ESC")  # (the list only)
    def right():  # the code as typed, and no keys left as typed after it (the Chinese is choosing as usual)
        rest = c.composition[len(lead + code):]
        return c.composition.startswith(lead + code) and not any(ch.isascii() and ch.isalnum() for ch in rest)

    def chinese_after():  # the Chinese after the code from the bar, the caret before its keys (h264 + 是)
        if after.strip() and end < len(c.raw):
            c.caret = end
            c._render()
            if tail.strip() in c.replacements:
                c._pick_replacement(c.replacements.index(tail.strip()))

    chinese_after()
    for _ in range(4):  # the keys back from the bar, on each character inside the code read as Chinese
        if right():
            break
        zh = next((t for t in c.tokens if t.kind == "zh" and start <= t.start < end), None)
        if zh is None:
            break
        c.caret = zh.start
        c._render()
        keys = zh.raw.rstrip(" ")
        if keys not in c.replacements:
            break
        c._pick_replacement(c.replacements.index(keys))
        chinese_after()  # (the keys back may take the Chinese after them along: a54g4)
    ok = right()
    c.key(special="ENTER")
    return ok


def main(argv):
    show = "--show" in argv
    table, lexicon, english, _, bigram, config = load_resources()
    if "--bigram" in argv:  # (another context model file: tools/build_bigram.py --keep)
        bigram = load_other(lexicon, argv[argv.index("--bigram") + 1])
    config.set(remember_words=False)
    wrong_now = {p[0]: [] for p in PLACES}
    wrong_after = {p[0]: [] for p in PLACES}
    no_way = []
    for code in CODES:
        user = UserModel(os.path.join(tempfile.mkdtemp(), "user.json"))
        user.today = lambda: 20000
        c = Composer(table, lexicon, english, user, bigram, config)
        results = []
        for place in PLACES:
            name, sent_before, before, after, (lead, tail) = place
            out = typed(c, sent_before, before + code + after)
            results.append(out)
            if out != lead + code + tail:
                wrong_now[name].append((code, out))
        # put right twice where it came out wrong
        for place, out in zip(PLACES, results):
            name, sent_before, before, after, (lead, tail) = place
            if out != lead + code + tail:
                for _ in range(2):
                    if not put_right(c, code, place):
                        no_way.append((code, name))
                        break
        for place in PLACES:
            name, sent_before, before, after, (lead, tail) = place
            out = typed(c, sent_before, before + code + after)
            if out != lead + code + tail:
                wrong_after[name].append((code, out))
    print(f"{len(CODES)} codes")
    for name, *_ in PLACES:
        print(f"  {name:20} wrong now {len(wrong_now[name]):3}   after putting right twice {len(wrong_after[name]):3}")
        if show:
            for code, out in wrong_after[name]:
                print(f"      still wrong: {code!r} -> {out!r}")
    print(f"  no way to put right: {len(no_way)}", no_way[:10] if show else "")


if __name__ == "__main__":
    main(sys.argv[1:])
