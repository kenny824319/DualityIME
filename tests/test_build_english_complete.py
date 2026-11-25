"""tools/build_english_complete.py (#40): the words it leaves out as offensive - every form on an ESDB line marked
(offensive-N) or (vulgar-N), however the line is written, and LDNOOBW's words with their inflections (findings #137:
niggas, shitted, faggots, kikes, wetbacks stayed and were offered for fag, kik, nigg, wetb)."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import build_english_complete as b  # noqa: E402


def lines_file(text):
    f = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False)
    f.write(text)
    f.close()
    return f.name


class OffensiveTest(unittest.TestCase):
    def test_every_form_on_a_marked_esdb_line(self):
        path = lines_file(
            "60: nigga <n> (offensive-1): (niggas | niggaz) #! unmarked variants: niggas, niggaz\n"
            "70: shitted (vulgar-1)\n"
            "60: bullshit <v> (vulgar-1): (bullshitted | ?: bullshit | -: bullshat), bullshitting, bullshits\n"
            "40: A: asshole <n> (vulgar-1): assholes, asshole's\n"
            "60: -nigger <v> (offensive-1): niggered, niggering, niggers\n"
            "60: _: niggerhead <n> (offensive-1)\n"
            "35: house <n>: houses, house's\n")
        try:
            got = b.offensive(path)
        finally:
            os.unlink(path)
        for w in ("nigga", "niggas", "niggaz", "shitted", "bullshitted", "bullshat", "bullshitting", "bullshits",
                  "asshole", "assholes", "niggered", "niggering", "niggers", "niggerhead"):
            self.assertIn(w, got)
        for w in ("house", "houses", "unmarked", "variants", "offensive", "vulgar"):
            self.assertNotIn(w, got)

    def test_ldnoobw_words_with_their_inflections(self):
        path = lines_file("faggot\nkike\nwetback\ndarkie\nwank\nslut\nhussy\ngrope\nalabama hot pocket\n")
        try:
            got = b.ldnoobw(path)
        finally:
            os.unlink(path)
        for w in ("faggot", "faggots", "kikes", "wetbacks", "darkies", "wanker", "wankers", "wanking", "sluts",
                  "hussies", "slutty"):
            self.assertIn(w, got)
        for w in ("grope", "gropes", "groped", "groping"):  # (KEEP: a plain word, with its forms)
            self.assertNotIn(w, got)

    def test_derived_words_and_slurs_neither_source_marks(self):
        # (findings #141: dickhead, poofter, whorehouse...; #142, the user 2026-11-25: slurs against groups, with their
        # plurals - but chink, chinking (a crack, filling gaps) stay)
        for w in ("dickhead", "poofter", "whorehouse", "bitchiness", "sluttish", "boobies"):
            self.assertIn(w, b.DERIVED)
        got = b.slurs()
        for w in ("redskins", "dagoes", "golliwog", "negroid", "mulatto", "mulattoes", "sodomite", "chinks", "gooks",
                  "honkies", "coolies"):
            self.assertIn(w, got)
        for w in ("chink", "chinking", "chinked"):
            self.assertNotIn(w, got)


if __name__ == "__main__":
    unittest.main()
