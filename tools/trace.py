"""Feeds key scripts to the Python engine and prints its state after every
key, in the same format as cpp/tools/trace.cpp (see there), so
tools/compare.py can diff the two engines.

    python tools/trace.py <user.json> <script file> [config.json]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import Composer, load_resources  # noqa: E402


def state(c, extra):
    return "|".join([c.commit, c.composition, str(c.cursor), "\x1f".join(c.candidates), str(c.cand_index),
                     c.suggestions[0] if c.suggestions else "",
                     "-" if c.symbol_page is None else str(c.symbol_page), str(c.symbol_cursor),
                     "\x1f".join(c.replacements), str(c.repl_index), "%s@%d" % c.zhuyin_tip(), "%d,%d" % (c.cand_expanded, c.cand_first), extra])


def main(user_path, script_path, config_path=None):
    table, lexicon, english, user, bigram, config = load_resources(user_path, config_path)
    user.today = lambda: 20000
    c = Composer(table, lexicon, english, user, bigram, config)
    out = []
    with open(script_path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    for line in lines:
        line = line.rstrip("\r")
        out.append("# " + line)
        caps = False
        i = 0
        while i < len(line):
            extra = ""
            if line[i] == "{":
                close = line.index("}", i)
                name = line[i + 1:close]
                i = close + 1
                if name == "CAPS":
                    caps = not caps
                    continue
                if name == "UNDO":
                    extra = "undo:" + c.undo_commit()
                elif name == "FW":
                    extra = "fw:" + ("1" if c.toggle_full_width() else "0")
                elif name == "RESET":
                    c.reset()
                elif name == "LB":
                    extra = "1" if c.key("{", caps=caps) else "0"
                elif name[0] == "#":
                    extra = "1" if c.type_literal(name[1]) else "0"
                elif name[0] == "^":  # Ctrl + a key: the full-width mark (setting ctrl_punct)
                    extra = "1" if c.type_full_punct(name[1]) else "0"
                else:
                    extra = "1" if c.key(special=name) else "0"
            else:
                ch = line[i]
                if caps and ch.isascii() and ch.isalpha():
                    ch = ch.swapcase()
                extra = "1" if c.key(ch, caps=caps) else "0"
                i += 1
            out.append(state(c, extra))
        # each line ends the sentence
        if c.symbol_page is not None:
            c.key(special="ESC")
        if c.candidates:
            c.key(special="ESC")
        if c.raw:
            c.key(special="ENTER")
            out.append(state(c, "end"))
    sys.stdout.buffer.write(("\n".join(out) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main(*sys.argv[1:4])
