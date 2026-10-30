"""tools/autocorrect_eval.py with the language model in 注音自動校正's sentence score (run_score).

    py tools/lm/autocorrect_lm.py <S|M|L> <λ> [autocorrect_eval options]

run_score(window) becomes the best, over the window's nbest (tools/lm/decode.py), of the
engine's score + λ * the model's log10 probability: what 長句選字加強 would give the reading
choices of the 2026-10-29 autocorrect (a tone left out, a key next to the right one spelling
another syllable). Only in this process: the engine's files are not touched.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import WORK  # noqa: E402
from decode import Scorer, candidates_by_syllable, nbest  # noqa: E402
from intlm import IntLM  # noqa: E402
from engine import composer  # noqa: E402


def main(argv):
    size, lam = argv[0], float(argv[1])
    work = os.environ.get("DUALITYIME_LM_WORK", WORK)
    from engine import load_resources
    _, lexicon, _, _, _, _ = load_resources(None)
    vocab = open(os.path.join(work, "vocab.txt"), encoding="utf-8").read().rstrip("\n").split("\n")
    scorer = Scorer(IntLM(os.path.join(work, "models", size + ".npz")), vocab, candidates_by_syllable(lexicon, vocab))
    plain = composer.run_score

    def run_score(readings, lexicon, user, bigram):
        base = plain(readings, lexicon, user, bigram)
        if base is None:
            return None
        _, options, _ = composer._search(readings, lexicon, user, {}, bigram, None)
        if len(scorer.cache) > 200000:  # (states of every sentence so far: kept from growing)
            scorer.cache = {"": scorer.cache[""]}
            scorer.score_cache = {}
        # (every hypothesis with the model's score: the model's log probability is never above 0,
        # so the engine's own best without it would always win a max with it)
        best = None
        for score, text in nbest(readings, options, bigram):
            if len(text) == len(readings):
                s = score + lam * scorer.phrase_score("", text, readings)
                best = s if best is None else max(best, s)
        return base if best is None else best

    composer.run_score = run_score
    composer._RUN_SCORES.clear()
    import autocorrect_eval
    print(f"language model {size}, λ {lam} in run_score", flush=True)
    autocorrect_eval.main(argv[2:])


if __name__ == "__main__":
    main(sys.argv[1:])
