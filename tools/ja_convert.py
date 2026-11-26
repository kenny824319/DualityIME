"""#36: the Python engine's Japanese conversion, the same output as cpp sz_ja.exe (tools/ja_compare.py compares them).

    py -3 tools/ja_convert.py [--data <dir>] [--bigram <file>] < romaji lines   (--bigram: "<context sid>\t<romaji>")
    py -3 tools/ja_convert.py [--data <dir>] --keys < key scripts

Romaji lines: each UTF-8 line in gives one line out:
    kana<TAB>pending<TAB>surface|surface|...<TAB>cost<TAB>finished<TAB>boundaries
(to_kana's kana and pending, the cheapest path over that kana with its cost, finish(romaji), and for each two
neighbouring words of the path 1 when they are in different 文節, else 0).
--keys: each line is a key script (tests/ja_scripts.txt: letters as typed, {NAME} a special key, {SPACE},
{HIRA} {KATA} {AUTO}, {FLUSH}, {SPACECONV}, {NOFKEYS}, {NOLMCONTEXT}) typed into a fresh JaComposer (ja_enabled on); after every key
one line
    used<TAB>composition<TAB>cursor<TAB>commit<TAB>page candidates joined by |<TAB>index in page<TAB>all candidates
and "--" after each script.
"""
import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.config import Config  # noqa: E402
from engine.ja_bigram import JaBigram  # noqa: E402
from engine.ja_composer import JaComposer  # noqa: E402
from engine.ja_composer import NBEST_K, NBEST_N  # noqa: E402
from engine.ja_convert import convert, nbest as nbest_of, rescore  # noqa: E402
from engine.ja_mixed import lm_weight_of, load_lm  # noqa: E402
from engine.ja_dict import JaDict  # noqa: E402
from engine.ja_romaji import RomajiTable  # noqa: E402

TOKEN = re.compile(r"\{[A-Z0-9]+\}|.")


def line_out(romaji, table, d, bigram=None, nbest=False, lm=None):
    prev = -1
    if bigram is not None and "\t" in romaji:  # (--bigram: "<context sid>\t<romaji>")
        sid, romaji = romaji.split("\t", 1)
        prev = int(sid)
    kana, pending = table.to_kana(romaji)
    if nbest:  # (plan 5b: "cost:text|cost:text..." - the lattice's best distinct sentences)
        return "|".join(f"{c}:{''.join(x.surface for x in p)}"
                        for p, c in nbest_of(kana, d, bigram=bigram, prev_sid=prev))
    path, cost = convert(kana, d, bigram=bigram, prev_sid=prev)
    if lm is not None and kana:  # (--lm: the 10 best rescored by the model, as the composers do)
        paths = nbest_of(kana, d, bigram=bigram, prev_sid=prev, k=NBEST_K, n=NBEST_N)
        path, cost = paths[rescore(paths, lm.view(""), lm_weight_of(lm))]
    cuts = "".join("1" if d.is_boundary(a.rid, b.lid) else "0" for a, b in zip(path, path[1:]))
    return f"{kana}\t{pending}\t{'|'.join(n.surface for n in path)}\t{cost}\t{table.finish(romaji)}\t{cuts}"


def run_script(script, table, d, bigram=None, lm=None, predictor=None, phrases=None, user=None):
    """The state lines of one key script (plan 6: with the hint at the end; plan 7: user - a ja_user.JaUser - the
    user's words and picks)."""
    cfg = Config()
    cfg.values["ja_enabled"] = True
    c = JaComposer(table, d, cfg, bigram, lm=lm, lm_weight=lm_weight_of(lm), predictor=predictor, phrases=phrases,
                   user=user)
    out = []
    for tok in TOKEN.findall(script):
        if tok == "{SPACECONV}":
            cfg.values["ja_space_convert"] = True
            continue
        if tok == "{NOFKEYS}":
            cfg.values["ja_fkeys"] = False
            continue
        if tok == "{NOLMCONTEXT}":  # (#89: 參考剛送出的字 off from here on)
            cfg.values["ja_lm_context"] = False
            continue
        if tok == "{SPACE}":
            used = c.key(" ")
        elif tok in ("{HIRA}", "{KATA}", "{AUTO}"):
            used = c.set_kana_lock(tok[1:-1].lower())
        elif tok == "{FLUSH}":
            c.commit = c.flush()
            used = True
        elif tok.startswith("{"):
            used = c.key(special=tok[1:-1])
        else:
            used = c.key(tok)
        page, at = c.page_candidates()
        out.append(f"{int(bool(used))}\t{c.composition}\t{c.cursor}\t{c.commit}\t{'|'.join(page)}\t{at}\t"
                   f"{len(c.candidates)}\t{c.suggestions[0] if c.suggestions else ''}")
    out.append("--")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--keys", action="store_true")
    ap.add_argument("--bigram")
    ap.add_argument("--nbest", action="store_true")
    ap.add_argument("--lm")  # (plan 5b: the Japanese model)
    ap.add_argument("--predict")  # (plan 6: the phrase table, key scripts only)
    ap.add_argument("--memory")  # (...and the user's phrases, a file learned into; the day 20000)
    ap.add_argument("--user")  # (plan 7: a folder - its ja_dictionary.json read, ja_picks.json learned into; day 20000)
    a = ap.parse_args()
    table = RomajiTable.load(os.path.join(a.data, "ja_romaji.tsv"))
    d = JaDict.load(os.path.join(a.data, "ja_dict.bin"))  # (ja_conn.bin, ja_seg.bin beside it)
    bigram = None
    if a.bigram:
        bigram = JaBigram.load(d, a.bigram)
        if bigram is None:
            sys.exit("STOPPED: could not read the bigram file (or it is for another ja_dict.bin)")
    predictor = phrases = None
    if a.predict:
        from engine.ja_predict import JaPredict
        predictor = JaPredict.load(d, a.predict)
        if predictor is None:
            sys.exit("STOPPED: could not read the phrase table (or it is for another ja_dict.bin)")
    if a.memory:
        from engine.lexicon import JaPhraseMemory
        phrases = JaPhraseMemory(a.memory, lambda: 20000)
    user = None
    if a.user:
        from engine.ja_user import JaUser
        from engine.lexicon import UserModel
        user = JaUser(UserModel(os.path.join(a.user, "user.json"), lambda: 20000))
    lm = None
    if a.lm:
        lm = load_lm(a.lm)
        if lm is None:
            sys.exit("STOPPED: could not read the model file")
    out = sys.stdout.buffer
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8").rstrip("\r\n")
        lines = run_script(line, table, d, bigram, lm, predictor, phrases, user) if a.keys else \
            [line_out(line, table, d, bigram, a.nbest, lm)]
        out.write("".join(x + "\n" for x in lines).encode("utf-8"))


if __name__ == "__main__":
    main()
