"""How should the text sent before (Enter) count for the run typed next?

    py -3 tools/context_eval.py [--corpus FILE.parquet] [--count N] [--lm] [--out FILE]

Sentences: runs of 4-20 Chinese characters, from the repo's own documents (README.md, docs/; a
document added later changes the sample, and the numbers with it), or
with --corpus from the end of a parquet file (not used for training, as tools/evaluate.py). Each
is cut at every place where the first part converts right on its own (so the user would send it
as it is); the rest is then converted in several ways and compared with the text:

  cur     the engine today: context_word and context_transition (the "whole word" share:
          怎麼 sent, then 辦). Since 2026-11-10 that is single: the tables of the findings were
          made before, with cur = any - to read them again, take the any row and swap fixed and
          broken (off, split and rule are against today's engine now)
  off     the phrase sent last as an ordinary previous phrase, no share
  joint   the last 3 characters sent converted again together with the run, fixed as sent
          (words may cross the boundary): as if Enter had not been pressed
  split   no share when it splits the word of 2+ syllables the run would start with (已經, cut
          into 以 + 精選 after 改 was sent)
  rule    the candidate of 2026-11-10: no share when it splits such a word AND joint writes no
          word across the boundary; else cur
  single  the share only when the phrase sent last has 2+ characters (怎麼 + 辦, not 改 + 以): the
          engine since 2026-11-10, written out here so it can be compared whatever the engine does
  any     the share whatever the length of the phrase sent last: the engine before 2026-11-10
  whole   the whole sentence in one go (reference: nothing was sent)

Prints, for each, how many cuts come out right, and against cur how many it fixes and breaks;
then the same by where the cut is: "boundary" (the whole sentence in one go is right and writes no
word across the cut: where Enter is usually pressed), "inside, 1 char" / "inside, 2+" (it writes a
word across the cut; by the length of the phrase sent last: 國｜家, 怎麼｜辦), "other" (the whole
sentence in one go is wrong anyway). --out writes those sentences to a file. --lm: 長句選字加強 on (slow; needs numpy). A fresh
user.json in a temporary folder: nothing of the user's is read or written.
Findings so far: docs/superpowers/specs/2026-11-10-sent-context-findings.md
"""
import glob
import os
import random
import re
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from engine import create_composer, composer as C  # noqa: E402

JOINT = 3  # characters sent that joint converts again
VARIANTS = ["cur", "off", "joint", "split", "rule", "single", "any", "whole"]
CLASSES = ["boundary", "inside, 1 char", "inside, 2+", "other"]


def share_any(bigram, prev, phrase, unigram):
    """context_transition as it was before 2026-11-10: the share whatever prev's length."""
    if bigram is None or not prev:
        return unigram
    b = bigram.logp(prev, phrase)
    if len(prev) + len(phrase) <= C.MAX_PHRASE_LEN:
        whole, part = bigram.unigram(prev + phrase), bigram.unigram(prev)
        if whole is not None and part is not None:
            share = min(0.0, whole - part)
            if b is None or share > b:
                b = share
    if b is None:
        return C.UNSEEN_PAIR + unigram
    return C.log10_add(C.LOG_BIGRAM_WEIGHT + b, C.UNSEEN_PAIR + unigram)


def share_single(bigram, prev, phrase, unigram):
    """The share only after a phrase of 2+ characters."""
    if prev and len(prev) < 2:
        return C.transition(bigram, prev, phrase, unigram)
    return share_any(bigram, prev, phrase, unigram)
HAN_RUN = re.compile("[一-鿿]{4,20}")
SKIP_FILES = {"2026-11-10-sent-context-findings.md", "2026-11-10-sent-context-single-char.md"}


def option(args, name, default=None):
    return args[args.index(name) + 1] if name in args else default


def repo_runs():
    files = [os.path.join(ROOT, "README.md")] + glob.glob(os.path.join(ROOT, "docs", "**", "*.md"), recursive=True)
    runs = set()
    for path in files:
        # (not the findings and handoffs of this work: they quote the wrong outputs, 以精選好的字,
        # and the numbers in the findings were measured before they existed)
        if os.path.basename(path) in SKIP_FILES or os.path.basename(os.path.dirname(path)) == "progress":
            continue
        with open(path, encoding="utf-8") as f:
            runs.update(HAN_RUN.findall(f.read()))
    runs = sorted(runs)
    random.Random(7).shuffle(runs)
    return runs


def corpus_runs(path, count):
    import pyarrow.parquet as pq
    pf = pq.ParquetFile(path)
    column = "text" if "text" in pf.schema_arrow.names else pf.schema_arrow.names[0]
    texts = pf.read_row_group(pf.num_row_groups - 1, columns=[column]).column(0).to_pylist()
    runs, seen = [], set()
    for text in reversed(texts):
        for run in HAN_RUN.findall(text):
            if run not in seen:
                seen.add(run)
                runs.append(run)
        if len(runs) >= count * 2:  # (some have no reading: more than asked for)
            break
    return runs


def main(args):
    use_lm = "--lm" in args
    count = int(option(args, "--count", 100000))
    corpus = option(args, "--corpus")
    out_path = option(args, "--out")

    tmp = tempfile.TemporaryDirectory()
    c = create_composer(os.path.join(tmp.name, "user.json"), os.path.join(tmp.name, "config.json"))
    c.config.values["long_sentence"] = use_lm
    lexicon, bigram, user = c.lexicon, c.bigram, c.user
    lm = c._lm() if use_lm else None
    if use_lm and lm is None:
        sys.exit("--lm: the language model did not load (numpy? data/lm.bin?)")

    best = {}
    for reading, lst in lexicon.entries.items():
        for p, s in lst:
            if s > best.get(p, ("", -99))[1]:
                best[p] = (reading, s)
    best = {p: r for p, (r, _) in best.items()}

    def reading_of(text):
        out, i = [], 0
        while i < len(text):
            for l in range(min(8, len(text) - i), 0, -1):
                r = best.get(text[i:i + l])
                if r:
                    out.extend(r.split("-"))
                    i += l
                    break
            else:
                return None
        return out

    samples = []
    for text in (corpus_runs(corpus, count) if corpus else repo_runs()):
        rd = reading_of(text)
        if rd and len(rd) == len(text):
            samples.append((text, rd))
    samples = samples[:count]

    def conv(rd, overrides=None, prev=None):
        return C.convert(rd, lexicon, user, overrides or {}, bigram, prev, lm)

    def text_of(path):
        return "".join(p for _, _, p in path)

    with_share, without_share = C.context_transition, C.transition

    def conv_with(fn, rd, prev):
        C.context_transition = fn
        try:
            return conv(rd, None, prev)
        finally:
            C.context_transition = with_share

    right = dict.fromkeys(VARIANTS, 0)
    fixed = {v: [] for v in VARIANTS}   # wrong in cur, right in v
    broken = {v: [] for v in VARIANTS}  # right in cur, wrong in v
    by_class = {c: {v: [0, 0] for v in VARIANTS} for c in CLASSES}  # [fixed, broken]
    class_cuts = dict.fromkeys(CLASSES, 0)
    cuts = 0
    started = time.time()
    for text, rd in samples:
        n = len(rd)
        whole_path = conv(rd)
        whole = text_of(whole_path)
        for k in range(1, n):
            first = conv(rd[:k])
            if text_of(first) != text[:k]:
                continue
            sent_last = first[-1][2]  # context_word after Enter
            want = text[k:]
            cur = conv(rd[k:], None, sent_last)
            off = conv_with(without_share, rd[k:], sent_last)
            if whole != text:
                where = "other"
            elif not any(s < k < s + l for s, l, _ in whole_path):
                where = "boundary"
            else:
                where = "inside, 1 char" if len(sent_last) < 2 else "inside, 2+"
            class_cuts[where] += 1
            a = max(0, k - JOINT)
            joint = conv(rd[a:], {(i - a, 1): text[i] for i in range(a, k)})
            across = any(s < k - a < s + l for s, l, _ in joint)
            joint_text = text_of(joint)
            splits = cur[0][1] == 1 and off[0][1] >= 2
            out = {
                "cur": text_of(cur),
                "off": text_of(off),
                "joint": joint_text[k - a:] if len(joint_text) == n - a else None,
                "split": text_of(off if splits else cur),
                "rule": text_of(off if splits and not across else cur),
                "single": text_of(conv_with(share_single, rd[k:], sent_last)),
                "any": text_of(conv_with(share_any, rd[k:], sent_last)),
                "whole": whole[k:] if len(whole) == n else None,
            }
            cuts += 1
            for v in VARIANTS:
                ok = out[v] == want
                right[v] += ok
                if ok and out["cur"] != want:
                    fixed[v].append(f"{text[:k]}|{want}  (cur: {out['cur']})")
                    by_class[where][v][0] += 1
                if not ok and out["cur"] == want:
                    broken[v].append(f"{text[:k]}|{want}  ({v}: {out[v]})")
                    by_class[where][v][1] += 1

    print(f"{len(samples)} sentences, {cuts} cuts, lm={'on' if use_lm else 'off'}, "
          f"{'corpus ' + corpus if corpus else 'repo documents'}, {time.time() - started:.0f} s")
    print(f"{'':8} {'right':>7} {'%':>7} {'fixed':>6} {'broken':>6} {'net':>6}")
    for v in VARIANTS:
        print(f"{v:8} {right[v]:7d} {right[v] / max(cuts, 1):7.2%} {len(fixed[v]):6d} {len(broken[v]):6d} "
              f"{len(fixed[v]) - len(broken[v]):+6d}")
    print("by where the cut is (fixed / broken against cur):")
    print(f"{'':8}" + "".join(f"{c + ' ' + str(class_cuts[c]):>24}" for c in CLASSES))
    for v in VARIANTS[1:]:
        print(f"{v:8}" + "".join(f"{'+%d -%d' % tuple(by_class[c][v]):>24}" for c in CLASSES))
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            for v in VARIANTS[1:]:
                f.write(f"===== {v}: fixed {len(fixed[v])}\n" + "".join(line + "\n" for line in fixed[v]))
                f.write(f"===== {v}: broken {len(broken[v])}\n" + "".join(line + "\n" for line in broken[v]))
        print("sentences:", out_path)
    tmp.cleanup()


if __name__ == "__main__":
    main(sys.argv[1:])
