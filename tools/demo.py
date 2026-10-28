"""Try the engine in a terminal: type keys as on a zhuyin keyboard, press Enter.

    python tools/demo.py                     (interactive, in a normal terminal)
    python tools/demo.py su3cl3 "hello "     (each argument is one sentence)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from engine import create_composer  # noqa: E402

c = create_composer(None)


def convert_line(line):
    c.reset()  # each line on its own, without the previous one as context
    out = ""
    for ch in line:
        c.key(ch)
        out += c.commit
    c.key(special="ENTER")
    return out + c.commit


if len(sys.argv) > 1:
    # python tools/demo.py su3cl3 "ji3ul4m/4Python vu,3t/6g4"  (one sentence per argument)
    for line in sys.argv[1:]:
        print(f"{line}  →  {convert_line(line)}")
else:
    while True:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            break
        print(convert_line(line))
