"""The text the next student model (#41) reads: Chinese characters, and English kept as one mark.

The same rule as the engines' (docs/architecture/decisions.md, #42): after NFKC, each Chinese
character (tools/lm/prepare.py RANGES) is one unit; a run of ASCII letters and digits, single - . _ allowed between
them (Qwen3-1.7B, v3.2.6, 1.5), is one EN; everything else (punctuation, spaces, symbols) is dropped and does not cut
the text.
"""
import re
import unicodedata

EN = "<en>"
UNITS = re.compile(r"[㐀-鿿豈-﫿]|[A-Za-z0-9]+(?:[-._][A-Za-z0-9]+)*")


def to_units(text):
    """The units of text: Chinese characters as themselves, each English or alphanumeric run as EN."""
    return [m if len(m) == 1 and not m.isascii() else EN for m in UNITS.findall(unicodedata.normalize("NFKC", text))]
