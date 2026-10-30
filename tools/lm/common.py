"""What the language model tools (長句選字加強, docs/superpowers/specs/2026-10-29-long-sentence-lm-design.md)
share: which corpus files train and which only evaluate, and where the work goes.

The corpus and everything made from it stay outside the repository (C:\\Users\\kenny\\Data):
DUALITYIME_CORPUS and DUALITYIME_LM_WORK move them.
"""
import os

CORPUS = os.environ.get("DUALITYIME_CORPUS", r"C:\Users\kenny\Data\corpus")
WORK = os.environ.get("DUALITYIME_LM_WORK", r"C:\Users\kenny\Data\lm")

WIKI = os.path.join(CORPUS, "wikipedia-zhtw", "20231001")
WEB = os.path.join(CORPUS, "CC-100-zh-Hant", "data")

TRAIN = [os.path.join(WIKI, f) for f in (
    "train-00000-of-00004-54b5cfc2972af646.parquet",
    "train-00001-of-00004-6f8ce79d6db05981.parquet",
    "train-00002-of-00004-60f89c91525385b0.parquet",
)] + [os.path.join(WEB, f) for f in (
    "train-00000-of-00037-f513ba7a2eb96f04.parquet",
    "train-00001-of-00037-41abe0d0d1b73619.parquet",
    "train-00002-of-00037-ce5df00f3f10ff37.parquet",
    "train-00003-of-00037-25d87105eb4072b3.parquet",
    "train-00004-of-00037-3d083705287de59b.parquet",
    "train-00005-of-00037-ffc48ef91dfb9d91.parquet",
    "train-00006-of-00037-66bacaf262a52be2.parquet",
)]

# never trained on: only tools/evaluate.py and tools/lm/evaluate_lm.py read them
EVAL = {
    "wiki": os.path.join(WIKI, "train-00003-of-00004-51e5ae7ccaa65ef5.parquet"),
    "web": os.path.join(WEB, "train-00007-of-00037-5d8afb78127e36c8.parquet"),
}

assert not set(TRAIN) & set(EVAL.values()), "an evaluation file among the training ones"


def text_column(pf):
    """The column with the text in a corpus parquet (text for the encyclopedia, line for the web)."""
    names = pf.schema_arrow.names
    return "text" if "text" in names else names[0]
