"""Where a transcript may have written a lexicon term as something that sounds like it.

Four kinds of hit, in the order they are trusted:

- `wrong`: a form the lexicon DECLARES stands for a term (term-match, longest
  first, whole tokens, `bound="edge"` so CJK written flush against Latin is found).
- `homophone`: a run of CJK characters that reads exactly like a CJK term once
  tones are dropped (海枯 for 海哭, 光鮮 for 光纖). A decoder has already lost the
  tone by the time it picks a character, so the tone is not evidence.
- `near`: the same, after the mergers Taiwan Mandarin speakers and decoders both
  make: zh/z, ch/c, sh/s, and -ng/-n (矽光紙 for 矽光子, 光屏書 for 光頻梳).
- `latin`: a run of Latin words with the same consonant skeleton as a Latin term
  (Council for COMSOL).

**A hit is a place to look, never a correction.** Nothing here rewrites text;
`glossary` turns a hit into a finding, and only a person's `accept` changes a
transcript. The skeleton and the mergers are deliberately coarse: a sound-alike
that is a real word (光線 is light, 海浪 is waves) is found, and saying which one
the speaker meant is above this layer.

Pronunciations: `data/syllables.tsv`, built by `tools/build_syllables.py` from
pypinyin's character table (MIT, notice in `data/NOTICE-pypinyin.txt`). Its
readings are the standard-Mandarin ones; where a Taiwan reading differs, the
toneless, merged comparison absorbs most of the difference, and what it does not
is a miss, not a false hit.
"""
from __future__ import annotations

import functools
import math
import pathlib
import re
from dataclasses import dataclass
from typing import Literal, Sequence

from . import lexicon as lx
from . import term_match

Kind = Literal["wrong", "homophone", "near", "latin"]
KINDS: tuple[Kind, ...] = ("wrong", "homophone", "near", "latin")

_DATA = pathlib.Path(__file__).resolve().parent / "data" / "syllables.tsv"
_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_LATIN_WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")
#: Words of a Latin window may be separated by spaces or hyphens only.
_LATIN_GAP = re.compile(r"[ \-]*")

#: A Latin skeleton shorter than this is never compared: two-consonant skeletons
#: (idea/youtu, Power/Pair) collide with half the dictionary even when EQUAL.
LATIN_MIN = 3
#: A skeleton shorter than this is compared for equality only (Cone/Comb at
#: distance 1 is also come/cone/comb/coin).
LATIN_FUZZY_MIN = 5
#: Edit distance allowed on a skeleton of at least `LATIN_FUZZY_MIN`, as a share
#: of the longer skeleton.
LATIN_FUZZY_SHARE = 0.25


@dataclass(frozen=True, slots=True)
class Hit:
    start: int
    end: int
    entry: int
    kind: Kind


@functools.lru_cache(maxsize=1)
def syllables() -> dict[str, tuple[str, ...]]:
    """Every reading, most common first (pypinyin's order)."""
    out: dict[str, tuple[str, ...]] = {}
    for line in _DATA.read_text(encoding="utf-8").splitlines():
        ch, _, syl = line.partition("\t")
        if ch and syl:
            out[ch] = tuple(syl.split(","))
    return out


def merged(syl: str) -> str:
    """The Taiwan-Mandarin mergers: zh/ch/sh -> z/c/s, -ng -> -n."""
    if syl[:2] in ("zh", "ch", "sh"):
        syl = syl[0] + syl[2:]
    if syl.endswith("ng") and len(syl) > 2:
        syl = syl[:-1]
    return syl


@functools.lru_cache(maxsize=None)
def reading(ch: str, fuzzy: bool) -> str:
    """The PRIMARY reading only, or "" for a character with none.

    Measured on the 2026-10-30 talk: comparing every reading let a rare one decide
    (失 also reads yi, so 一些 read like 失諧 51 times in one transcript). A
    character is written for its common reading far more often than for a rare
    one, so a polyphone read in its rare sense is a miss here, by choice.
    """
    got = syllables().get(ch)
    if not got:
        return ""
    return merged(got[0]) if fuzzy else got[0]


def sounds_like(a: str, b: str, fuzzy: bool) -> bool:
    """Same length, every character pair reads alike, and not identical."""
    if len(a) != len(b) or a == b:
        return False
    for x, y in zip(a, b):
        if x == y:
            continue
        rx = reading(x, fuzzy)
        if not rx or rx != reading(y, fuzzy):
            return False
    return True


def is_cjk_term(term: str) -> bool:
    return len(term) >= 2 and all(_CJK.match(c) for c in term)


#: A cue's language case, read from its own text (no user label, no decode
#: language): no Latin word -> `zh-only`, no CJK character -> `en-only`, both ->
#: `mixed`, neither -> `en-only` (nothing for a homophone to read).
Case = Literal["zh-only", "mixed", "en-only"]
CASES: tuple[Case, ...] = ("zh-only", "mixed", "en-only")


def case_of(text: str) -> Case:
    has_cjk = bool(_CJK.search(text))
    has_lat = bool(_LATIN_WORD.search(text))
    if has_cjk and has_lat:
        return "mixed"
    return "zh-only" if has_cjk else "en-only"


def prior(span: str, term: str, freq: dict[str, int] | None) -> float | None:
    """sum log10(f+1) over the term's CJK characters minus the span's, or None
    when either side has no CJK character. >0: the lexicon term is the commoner
    text. Character level only -- it cannot know that 光纖 is a word and 光線 is
    also one; that needs word frequency."""
    if not freq:
        return None
    a = [c for c in term if _CJK.match(c)]
    b = [c for c in span if _CJK.match(c)]
    if not a or not b:
        return None
    lf = lambda cs: sum(math.log10(freq.get(c, 0) + 1) for c in cs)
    return lf(a) - lf(b)


_GROUPS = {**dict.fromkeys("bfpv", "1"), **dict.fromkeys("cgjkqsxz", "2"),
           **dict.fromkeys("dt", "3"), "l": "4", **dict.fromkeys("mn", "5"),
           "r": "6"}


def skeleton(words: str) -> str:
    """A Soundex-like consonant skeleton, untruncated, first letter included as its
    group so C and K agree. Vowels separate repeats; h, w, y do not."""
    out: list[str] = []
    last = ""
    for c in words.lower():
        g = _GROUPS.get(c)
        if g is None:
            if c in "aeiou" or not c.isalpha():
                last = ""
            continue
        if g != last:
            out.append(g)
        last = g
    return "".join(out)


def _distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def skeletons_close(a: str, b: str) -> bool:
    if min(len(a), len(b)) < LATIN_MIN:
        return False
    if min(len(a), len(b)) < LATIN_FUZZY_MIN:
        return a == b
    return _distance(a, b) <= int(LATIN_FUZZY_SHARE * max(len(a), len(b)))


def _onset(word: str) -> str:
    """The first LETTER, with c/k/q as one (Council/COMSOL, Curve/Kerr).

    A skeleton group is too wide an onset: it puts s, z, c, k, j in one class,
    and on a recording with no photonics in it that let 「knowledge」 stand for
    `Simulink`, 「story」 for `jitter`, 「Chinese」 for `Zemax` (2026-10-30).
    """
    c = word[:1].lower()
    return "k" if c in "ckq" else c


#: Shared leading letters that make two words one word in two forms.
INFLECTION_STEM = 6


def _inflection(ws: Sequence[str], ts: Sequence[str]) -> bool:
    """Every word equal but the last, and the last two share a stem: 「harmonic」 for
    `harmonics`, 「intensity modulator」 for `intensity modulation`. That is another
    correct word, not a mishearing (both flagged on the 2026-10-30 Breeze decode)."""
    low_w = [w.lower() for w in ws]
    low_t = [t.lower() for t in ts]
    if low_w[:-1] != low_t[:-1]:
        return False
    a, b = low_w[-1], low_t[-1]
    k = 0
    while k < min(len(a), len(b)) and a[k] == b[k]:
        k += 1
    return k >= INFLECTION_STEM or (k == min(len(a), len(b)) and k >= 4)


def latin_close(window: str, term: str) -> bool:
    """One word: `skeletons_close` on the whole. Several: WORD BY WORD.

    Measured 2026-10-30: comparing the concatenated skeleton of a multi-word
    window let 「health insurance」 pass for `locking range` and 「was talking」
    for `mode-locking` on a recording with no photonics in it -- a quarter of a
    long skeleton is room for anything. Aligned, every word must open on the
    same consonant group and sit within one edit (a third of its skeleton when
    longer), which still lets 「Relay Backscattering」 through for `Rayleigh
    backscattering` (relay/rayleigh are one edit apart).
    """
    ws = _LATIN_WORD.findall(window)
    ts = _LATIN_WORD.findall(term)
    if len(ws) != len(ts) or _onset(ws[0]) != _onset(ts[0]):
        return False
    if _inflection(ws, ts):
        return False
    if len(ts) == 1:
        return skeletons_close(skeleton(window), skeleton(term))
    for w, t in zip(ws, ts):
        a, b = skeleton(w), skeleton(t)
        if not a or not b or a[0] != b[0]:
            return False
        if a != b and (min(len(a), len(b)) < 2
                       or _distance(a, b) > max(1, int(0.34 * max(len(a), len(b))))):
            return False
    return True


def is_latin_term(term: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z]+(?:['’\- ][A-Za-z]+)*", term))


def _latin_windows(text: str, n: int) -> list[tuple[int, int]]:
    words = [(m.start(), m.end()) for m in _LATIN_WORD.finditer(text)]
    out = []
    for i in range(len(words) - n + 1):
        ok = all(_LATIN_GAP.fullmatch(text[words[k][1]:words[k + 1][0]])
                 for k in range(i, i + n - 1))
        if ok:
            out.append((words[i][0], words[i + n - 1][1]))
    return out


def _overlaps(s: int, e: int, taken: Sequence[tuple[int, int]]) -> bool:
    return any(s < te and ts < e for ts, te in taken)


def _straddles(s: int, e: int, guard: Sequence[tuple[int, int]]) -> bool:
    """A correct form vetoes a sound-alike that lies inside it or cuts through it,
    and not one that CONTAINS it: in 「Relay Backscattering」 the correct
    `backscattering` is part of the two-word sound-alike of `Rayleigh
    backscattering`, and must not shield it (measured: it did, until this rule)."""
    return any(s < ge and gs < e and not (s <= gs and ge <= e) for gs, ge in guard)


def _folded(text: str) -> str:
    """Lower-cased when that keeps every offset; the text itself otherwise."""
    low = text.lower()
    return low if len(low) == len(text) else text


def _declared(text: str, lex: lx.Lexicon) -> list[Hit]:
    """Known wrong forms, through term-match. Correct forms and keep contexts take
    part as block terms, so a wrong form inside either is shielded."""
    hits: list[Hit] = []
    for sensitive in (True, False):
        owner: dict[str, int] = {}
        for i, e in enumerate(lex.entries):
            if e.case is sensitive:
                for w in e.wrong:
                    owner.setdefault(w if sensitive else w.lower(), i)
        if not owner:
            continue
        blocks = {*(e.term for e in lex.entries), *lex.keep}
        hay = text
        if not sensitive:
            hay = _folded(text)
            blocks = {b.lower() for b in blocks}
        blocks -= set(owner)
        for s, e, k in term_match.match_text(hay, [*owner, *blocks], bound="edge"):
            if k in owner:
                hits.append(Hit(s, e, owner[k], "wrong"))
    return hits


def _guarded(text: str, lex: lx.Lexicon) -> list[tuple[int, int]]:
    """Spans no sound-alike may touch: every correct form and keep context present."""
    keys = [*(e.term for e in lex.entries), *lex.keep]
    spans = [(s, e) for s, e, _ in term_match.match_text(text, keys, bound="edge")]
    lows = [e.term.lower() for e in lex.entries if not e.case]
    spans += [(s, e) for s, e, _ in
              term_match.match_text(_folded(text), lows, bound="edge")]
    return spans


@functools.lru_cache(maxsize=8)
def _plan(lex: lx.Lexicon) -> tuple[dict[int, list[tuple[int, str, str]]],
                                    list[tuple[int, int, str]]]:
    """Per-lexicon precomputation: CJK terms by length with both reading keys,
    Latin terms with word count and skeleton."""
    cjk: dict[int, list[tuple[int, str, str]]] = {}
    latin: list[tuple[int, int, str]] = []
    for i, e in enumerate(lex.entries):
        if not e.sound:
            continue
        if is_cjk_term(e.term):
            exact = "|".join(reading(c, False) for c in e.term)
            near = "|".join(reading(c, True) for c in e.term)
            if all(reading(c, False) for c in e.term):
                cjk.setdefault(len(e.term), []).append((i, exact, near))
        elif is_latin_term(e.term):
            sk = skeleton(e.term)
            if len(sk) >= LATIN_MIN:
                latin.append((i, len(_LATIN_WORD.findall(e.term)), sk))
    return cjk, latin


def find(text: str, lex: lx.Lexicon, kinds: Sequence[Kind] = KINDS) -> list[Hit]:
    """Every hit in `text`, non-overlapping, in text order. A declared wrong form
    wins its span over any sound-alike; among sound-alikes the more trusted kind
    wins, then the longer span, then the earlier one."""
    taken: list[tuple[int, int]] = []
    out: list[Hit] = []
    if "wrong" in kinds:
        for h in _declared(text, lex):
            if not _overlaps(h.start, h.end, taken):
                out.append(h)
                taken.append((h.start, h.end))
    guard = _guarded(text, lex)
    cjk, latin = _plan(lex)
    cands: list[Hit] = []
    cost: dict[int, tuple[int, int]] = {}
    if cjk and ("homophone" in kinds or "near" in kinds):
        ex = [reading(c, False) for c in text]
        nr = [reading(c, True) for c in text]
        for n, terms in cjk.items():
            for s in range(len(text) - n + 1):
                if not all(ex[s:s + n]):
                    continue
                window = text[s:s + n]
                kx, kn = "|".join(ex[s:s + n]), "|".join(nr[s:s + n])
                for i, exact, near in terms:
                    if window == lex.entries[i].term:
                        continue
                    if "homophone" in kinds and kx == exact:
                        cands.append(Hit(s, s + n, i, "homophone"))
                    elif "near" in kinds and kn == near:
                        cands.append(Hit(s, s + n, i, "near"))
    if latin and "latin" in kinds:
        for i, n, want in latin:
            term = lex.entries[i].term.lower()
            for s, end in _latin_windows(text, n):
                window = text[s:end]
                if window.lower() == term:
                    continue
                got = skeleton(window)
                if latin_close(window, lex.entries[i].term):
                    cands.append(Hit(s, end, i, "latin"))
                    cost[len(cands) - 1] = (_distance(got, want),
                                            _distance(window.lower(), term))
    rank = {k: r for r, k in enumerate(KINDS)}
    # Same span, two Latin terms close to it: the closer skeleton wins, then the
    # closer spelling (Relay Backscattering is one skeleton edit from both
    # `Rayleigh backscattering` and `Rayleigh scattering`, and far closer to the
    # first in letters). Lexicon order decides only a full tie.
    order = sorted(range(len(cands)), key=lambda k: (
        rank[cands[k].kind], -(cands[k].end - cands[k].start), cands[k].start,
        cost.get(k, (0, 0)), k))
    cands = [cands[k] for k in order]
    for h in cands:
        if _overlaps(h.start, h.end, taken) or _straddles(h.start, h.end, guard):
            continue
        out.append(h)
        taken.append((h.start, h.end))
    out.sort(key=lambda h: (h.start, h.end))
    return out
