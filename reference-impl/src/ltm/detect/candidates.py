"""Nomination. **This module imports nothing from `signals.py`, ever.**

PSM section 6.25 / PIM section 4.05, and it is the step the spec was missing until
v0.8. Getting it wrong changes the output by three orders of magnitude, so the
separation is structural: only repetition nominates, and what enforces that is
that this module *cannot reach* S4.
`tests/test_detect_candidates.py::test_candidates_module_cannot_see_s4` walks these
imports and fails if `signals` appears.

The defect it closes was real and subtle (round 6, RC-1): the instrument used to
build spans from S2 and S3 *membership*, so a cue satisfying only S4 was never
evaluated -- neither proposal nor note, absent from the output entirely -- while
the document said "idx 346 keeps only S4, so INV-6 makes it a note". That sentence
was true of a labelled diagnostic table that recomputed `n` for an arbitrary index
and bypassed the candidate rule. **Two mechanisms, one accidentally matching word.**

The two kinds are disjoint **by construction**: a cross-cue run contributes one
candidate, and a within-cue repeat contributes one only if its index is not already
covered by a run. That is why no span can be credited by both repetition signals
and why there is no precedence rule to maintain (RC-4).
"""
from __future__ import annotations

from typing import Iterator, Literal, Sequence

from ..core import model

#: A repeated unit must be at least this many characters...
S2_MIN_UNIT = 2
#: ...repeated at least this many times, consecutively, inside one cue.
S2_MIN_REPS = 3
#: A cross-cue run needs at least this many adjacent cues with identical text
#: for the run to be able to reach a deletion proposal.
S3_MIN_RUN = 5
#: ...and at least this many to be nominated at all, as a capped note.
#:
#: **The shoulder, and the measurement that put it here.** Seven decodes of one
#: recording with identical arguments produced seven distinct transcripts, and
#: the largest same-text group ranged 2-10 with a median of exactly 5
#: (`docs/evidence/positive_control_2026-09-09.txt`). `S3_MIN_RUN` was therefore
#: a step sitting in the middle of the noise: the same audio decoded twice
#: crossed it in both directions, so decode jitter changed the product's ANSWER
#: -- "delete this" against "nothing to say" -- rather than its confidence.
#: A run of `S3_NOTE_RUN` is now nominated too and is capped at a note, so
#: crossing the boundary moves a span between note and finding. **This moves the
#: cliff, it does not remove it**: a group of three is still invisible. It is
#: placed where the measured mass is. User ruling 2026-09-09 (UAT A-4).
S3_NOTE_RUN = 4
#: ...whose repeated text carries at least this many alphanumerics.
#:
#: **One, not two, and D3 is why.** The rule exists to skip cues that are only
#: punctuation ('...' repeats 11 times in one decode here). Requiring two threw
#: away the worst loop in the whole experiment: 27 adjacent cues of the single
#: character 對, 54.4 s, temperature climbing 0.4 -> 1.0 and compression ratio
#: 2.2105 -> 3.2143. **A one-character Mandarin word is a word.** "At least one
#: alphanumeric" excludes '...' (zero) and keeps 對 (one), which is what the rule
#: was for.
S3_MIN_ALNUM = 1

Kind = Literal["run", "near-run", "cue"]


def has_repeat_unit(text: str) -> bool:
    """One cue's within-cue repetition, decided from that cue alone.

    Split out from `repeat_units` for the streaming detector (§8.1 / RB-7): a
    detector that must advance per `Cue` cannot ask a whole-sequence function
    whether cue `i` repeats. Both spellings now call this, so the rule has one
    definition and the set version stays a fold over it.
    """
    t = text.strip()
    n = len(t)
    for length in range(S2_MIN_UNIT, n // S2_MIN_REPS + 1):
        for start in range(0, n - S2_MIN_REPS * length + 1):
            unit = t[start:start + length]
            k = 1
            while t[start + k * length:start + (k + 1) * length] == unit:
                k += 1
            if k >= S2_MIN_REPS:
                return True
    return False


def repeat_units(cues: Sequence[model.Cue]) -> set[int]:
    """S2's STRUCTURE, without the compression-ratio gate.

    Split out from S2 deliberately (D5): this is what *nominates* a within-cue
    candidate, and the gate then decides whether S2 actually holds on it. Keeping
    the two in one function is what made an S4-only candidate unrepresentable.
    """
    return {i for i, cue in enumerate(cues) if has_repeat_unit(cue.text)}


def run_qualifies(count: int, text: str) -> bool:
    """S3's rule on one closed group of identical cues, count and text only.

    Same reason as `has_repeat_unit`: the streaming detector knows a group's size
    and its text at the moment the group closes, and nothing else. One definition,
    two callers.
    """
    t = text.strip()
    return (count >= S3_MIN_RUN
            and sum(1 for c in t if c.isalnum()) >= S3_MIN_ALNUM)


def run_is_near(count: int, text: str) -> bool:
    """One short of the bar: nominated, and capped at a note by `score`.

    The alphanumeric rule is the same one, and deliberately so -- a shoulder that
    admitted punctuation would surface the '...' groups the full rule spends a
    constant excluding. `run_qualifies` and this are mutually exclusive by the
    `count <` clause, so a group answers yes to at most one of them and the kinds
    stay disjoint without a precedence rule (RC-4).
    """
    t = text.strip()
    return (S3_NOTE_RUN <= count < S3_MIN_RUN
            and sum(1 for c in t if c.isalnum()) >= S3_MIN_ALNUM)


def same_text_groups(cues: Sequence[model.Cue]) -> Iterator[tuple[int, int]]:
    """Maximal groups of adjacent cues with identical text, closing in order.

    **One definition, two callers** (verifier V-4): `s3_runs` below and the
    streaming walk in `detectors.EngineLoop.scan`. The predicate was duplicated
    verbatim in both for one commit -- formally equivalent, and exactly the shape
    the rest of this module was refactored to avoid, since the two would drift the
    first time either grew a rule about whitespace or case.
    """
    i = 0
    while i < len(cues):
        j = i
        while (j + 1 < len(cues)
               and cues[j + 1].text.strip() == cues[i].text.strip()):
            j += 1
        yield i, j
        i = j + 1


def s3_runs(cues: Sequence[model.Cue]) -> list[tuple[int, int]]:
    """Across adjacent cues: identical text, long enough, and not punctuation.

    This is S3 the SIGNAL, and it keeps the full threshold. `signals.s3` reads it,
    so the shoulder below cannot quietly redefine what "S3 holds" means.
    """
    return [(i, j) for i, j in same_text_groups(cues)
            if run_qualifies(j - i + 1, cues[i].text)]


def s3_near_runs(cues: Sequence[model.Cue]) -> list[tuple[int, int]]:
    """The shoulder: groups one short of the bar. Never S3, always a candidate."""
    return [(i, j) for i, j in same_text_groups(cues)
            if run_is_near(j - i + 1, cues[i].text)]


def candidates(cues: Sequence[model.Cue]) -> list[tuple[int, int, Kind]]:
    """Disjoint spans, tagged by which kind of repetition nominated them.

    Cross-cue runs first, then the shoulder, then what is left over; a cue already
    inside either kind of run is not also offered as a within-cue candidate.
    """
    runs = s3_runs(cues)
    near = s3_near_runs(cues)
    solo = sorted(repeat_units(cues) - covered_indices(cues))
    out: list[tuple[int, int, Kind]] = [(a, b, "run") for a, b in runs]
    out += [(a, b, "near-run") for a, b in near]
    out += [(i, i, "cue") for i in solo]
    return out


def covered_indices(cues: Sequence[model.Cue]) -> set[int]:
    """Which cue indices a run of either strength already claims.

    **The shoulder claims its members too, and that is not a detail.** If it did
    not, a four-cue group whose text also repeats inside each cue would be offered
    once as a capped near-run AND four times as within-cue candidates -- and one
    of those four could reach `n >= 2` and propose a deletion, which is exactly
    the conclusion the cap exists to withhold at this length.

    Called by `candidates()` rather than left beside it as a second copy of the
    same comprehension (verifier V-3, which found it defined and never used): the
    within-cue set is `repeat_units - covered`, and "covered" having two spellings
    is how the disjointness RC-4 relies on would quietly stop holding.
    """
    return {i
            for a, b in s3_runs(cues) + s3_near_runs(cues)
            for i in range(a, b + 1)}
