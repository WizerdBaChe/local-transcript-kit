"""Scoring. **S4 votes here and only here.**

PSM section 6.25. `score()` may add S4's vote to a candidate; it can never create
one. The disposition is INV-6:

    n >= 2 -> a deletion proposal (`Finding`)
    n == 1 -> a `Note`
    n == 0 -> dropped, and it appears in no output at all

**Two clauses were added to that table on 2026-09-09, and they are mirror
images.** Both are properties of the CANDIDATE rather than adjustments to `n`,
precisely so `n` keeps meaning "how many signals held" -- which is what the
acceptance table quotes:

* **PSM A-20 / UAT A-4: a near-threshold run is a note whatever `n` says.** The
  measurement is in `candidates.S3_NOTE_RUN` -- the run-length threshold sat at
  the median of the decode noise, so jitter moved spans between "delete this"
  and silence.
* **PSM A-25 / UAT A-9: a full-length run is a proposal whatever `n` says.** The
  measurement is the 51-minute recording: the shipping default nominated three
  runs of five or more, the longest eight, and every one came out a note because
  the decoder never reported any distress. See `Scored.stands_alone`.

Between them the two clauses say the same thing from both ends: how long an
identical run is, is itself evidence, and the length is not something `n` was
able to express.

`n == 0` is not hypothetical: the shipping default on the baseline drops idx 488 --
it has a repeat unit, its compression ratio misses the S1 sub-gate, its
neighbourhood is clean and its own temperature is 0.0. The dropped list is
returned rather than discarded, because **"judged and rejected" and "never
considered" must stay distinguishable** -- that is the whole of RC-1.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from ..core import model
from . import signals
from .candidates import Kind, candidates
from .thresholds import Thresholds


@dataclass(frozen=True, slots=True)
class Scored:
    """One candidate with its votes broken out.

    The three votes are kept separately rather than summed on the spot because the
    acceptance table quotes them one by one, and a total is not re-derivable into
    its parts -- `n == 1` from S3 alone and `n == 1` from S4 alone are different
    facts about the same transcript.
    """

    a: int
    b: int
    kind: Kind
    n2: int
    n3: int
    n4: int

    @property
    def n(self) -> int:
        return self.n2 + self.n3 + self.n4

    @property
    def capped(self) -> bool:
        """A near-threshold run may never reach a proposal, whatever else votes.

        The cap is a property of the CANDIDATE, not a subtraction from `n`: the
        span really does have the votes it has, and a reader comparing two decodes
        needs to see that the evidence was the same and only the length differed.
        Hiding the cap inside the arithmetic would make the note look like weaker
        evidence than it is.
        """
        return self.kind == "near-run"

    @property
    def stands_alone(self) -> bool:
        """A full-length identical run needs no second signal (A-25, UAT A-9).

        The mirror image of `capped`, and it exists for the same reason: the
        LENGTH of a cross-cue run is a fact about the transcript, and INV-6's
        arithmetic could not express "this one is long enough on its own".

        Why the user overruled the two-signal rule here: on the 51-minute
        recording the shipping default nominated three runs of five or more --
        the longest **eight identical cues in a row** -- and every one came out a
        note, because the decoder's own state stayed calm and S4 never voted. The
        reader was shown a transcript with an eight-fold loop in it and offered
        nothing to do about it. A decoder that is confidently repeating itself is
        the failure this product exists to surface, and "confidently" is exactly
        why the second signal never arrives.

        The cost is real and is not hidden: genuinely repeated speech -- a roll
        call, a countdown, a list read item by item -- can now be proposed for
        deletion on one signal. Measured over the three decodes in this
        repository the change moves exactly ONE span, and no hand-labelled
        known-false case among them (`test_detect_signals.py`). A proposal is
        still a proposal: nothing is applied until a person or an agent adopts it.
        """
        return self.kind == "run"

    @property
    def disposition(self) -> Literal["proposal", "note", "dropped"]:
        if self.n == 0:
            return "dropped"
        if self.capped:
            return "note"
        if self.stands_alone:
            return "proposal"
        return "proposal" if self.n >= 2 else "note"


def score(rec: model.Recording, a: int, b: int, kind: Kind,
          thr: Thresholds) -> Scored:
    """The votes for one candidate. Never nominates.

    Note what is NOT here: no branch can add a candidate, and S2/S3 are read off
    the candidate's own kind rather than recomputed -- the kinds are disjoint by
    construction (§6.25), so a span cannot be credited by both repetition signals.
    """
    mets = signals.metrics(rec)
    n2 = 1 if (kind == "cue" and signals.s1(mets, a, thr)) else 0
    # Both run kinds vote: cross-cue repetition DID nominate this span, and the
    # near-threshold case differs in what it is allowed to conclude (`capped`),
    # not in what was observed. `signals.s3` keeps the full threshold, so "S3
    # holds" still means what it always meant.
    n3 = 1 if kind in ("run", "near-run") else 0
    n4 = 1 if signals.s4(rec, a, b) else 0
    return Scored(a=a, b=b, kind=kind, n2=n2, n3=n3, n4=n4)


def score_all(rec: model.Recording, thr: Thresholds) -> list[Scored]:
    return [score(rec, a, b, kind, thr) for a, b, kind in candidates(rec.cues)]


def partition(scored: Sequence[Scored]) -> dict[str, list[Scored]]:
    """Every candidate lands in exactly one bucket, and the buckets sum.

    `tests/test_detect_candidates.py::test_partition_sums` asserts that against all
    three decodes: a case that silently vanishes is the exact defect this step was
    written to close.
    """
    out: dict[str, list[Scored]] = {"proposal": [], "note": [], "dropped": []}
    for s in scored:
        out[s.disposition].append(s)
    return out
