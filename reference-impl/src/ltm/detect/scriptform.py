"""Which Chinese script each cue is written in, and where the decoder switched.

The product never converts a transcript's script (design-divergence-map boundary
5; speech-subtitle-lite SL-R3). It can still SAY where the text left the script
the reader expects, and that is worth saying for two reasons: a reader who wants
one script needs to know where the other one is, and a switch is decoder state --
breeze-eval measured large-v3's simplified output coming from the stretch it had
judged to be English.

A character counts only when it belongs to ONE script, read off the same table
`glossary` uses (`data/simplified_variants.tsv`): a simplified-only character
(验, 发) is `hans`; a character the table gives as its traditional form and that
is not itself simplified-only (驗, 發, 髮) is `hant`. Characters both scripts
write (系, 后, 中, 人) count for neither, so a cue of only those is `none`.

User rulings 2026-11-23: the expected script defaults to traditional and can be
switched (`--script hans`); a run of the other script is an info finding with no
edit, and the plan summary carries how many cues are in each script.
"""
from __future__ import annotations

import functools
from typing import Literal, Sequence

from . import soundalike as sa

Script = Literal["hant", "hans"]
SCRIPTS: tuple[Script, ...] = ("hant", "hans")
DEFAULT_SCRIPT: Script = "hant"
Label = Literal["hant", "hans", "none"]

#: A run of the other script shorter than this many cues is not reported. The
#: measured runs on real decodes were 3-263 cues (2026-11-23, evidence
#: `script_switch_controls_2026-11-23.txt`); scattered single cues come from one
#: or two characters and say nothing about the decoder's state. A cost choice,
#: not a line in the data.
MIN_CUES = 2


@functools.lru_cache(maxsize=1)
def _sets() -> tuple[frozenset[str], frozenset[str]]:
    table = sa.simplified_variants()
    hans = frozenset(table)
    hant = frozenset().union(*table.values()) - hans
    return hans, hant


def counts(text: str) -> tuple[int, int]:
    """(simplified-only, traditional-only) characters in `text`."""
    hans, hant = _sets()
    return sum(c in hans for c in text), sum(c in hant for c in text)


def label(text: str) -> Label:
    s, t = counts(text)
    if s > t:
        return "hans"
    if t > s:
        return "hant"
    return "none"


def other_runs(labels: Sequence[Label], expect: Script,
               min_cues: int = MIN_CUES) -> list[tuple[int, int, int]]:
    """Maximal runs of the script that is NOT `expect`: (first, last, cues).

    A `none` cue inside a run does not break it (a line of English or of shared
    characters is no evidence of a switch back); an `expect` cue does. A run
    starts and ends on a cue of the other script, and `cues` counts only those.
    """
    other = "hans" if expect == "hant" else "hant"
    out: list[tuple[int, int, int]] = []
    i, n = 0, len(labels)
    while i < n:
        if labels[i] != other:
            i += 1
            continue
        last, k, j = i, 0, i
        while j < n and labels[j] != expect:
            if labels[j] == other:
                last, k = j, k + 1
            j += 1
        if k >= min_cues:
            out.append((i, last, k))
        i = last + 1
    return out
