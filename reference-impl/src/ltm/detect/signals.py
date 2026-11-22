"""S2, S3, S4. `docs/evidence/signal_spec_2026-09-09.py` is the definition.

That script is the contract and this module reproduces its constants and control
flow; if the two disagree, **the script is right**
(`tests/test_detect_signals.py` reproduces its printed numbers on the two in-repo
decodes). S1 lives here as S2's sub-gate and is not a member of INV-6's count.

Three rules, each with the measurement that forced it:

* **S1 is not a member.** It is gzip compressibility, and text repetition is what
  pushes it up -- while S3 counts text repetition. On the baseline all 9 of S3's
  cues fall inside S1: overlap 100%. Two measures of one thing are not two signals
  however the invariant is worded.
* **S4 is one signal with two measures.** (a) the neighbourhood decoded badly, or
  (b) the span's own temperature fell back. Both answer *was the decoder
  struggling*, so counting them separately would let a span reach INV-6's two-signal
  bar by asking one question twice -- exactly the mistake S1xS3 was blocked for.
* **S4(a) is a COUNT, not a ratio.** Swept: every negative sits at exactly 0.0000
  and the positive at 0.5082 (`docs/evidence/s4_threshold_sweep_2026-09-09.txt`).
  Nothing lies between, so 0.0001, 0.25 and 0.49 give an identical partition. A
  ratio threshold here is uncalibratable, and writing one implies a calibration
  that never happened.

This module imports the two structure functions from `candidates.py` -- the edge
runs that way and only that way, so that "S2 = a repeat unit **plus** the gate"
exists in one place.
"""
from __future__ import annotations

from typing import Sequence

from ..core import model
from .candidates import repeat_units, s3_runs          # one-way edge (§6.25)
from .thresholds import Thresholds

#: S4a's neighbourhood half-width, in seconds, either side of the span.
WINDOW = 30.0
#: S4a: a cue whose `avg_logprob` is at or below this decoded badly.
LP_BAD = -1.0
#: S4b: faster-whisper retries a failed window up the ladder
#: (0.0, 0.2, 0.4, 0.6, 0.8, 1.0). Reaching 0.4 means it failed TWICE and said so
#: -- the engine reporting its own distress rather than us inferring it, and a rung
#: of the engine's ladder rather than a number fitted to this file. Measured: all
#: six known-false controls top out at 0.20; the 27x loop runs 0.40 to 1.00.
TEMP_FALLBACK = 0.4


def metrics(rec: model.Recording) -> list[model.Window]:
    """Each cue's window, in cue order. INV-1's resolution, done once."""
    by_id = {w.id: w for w in rec.windows}
    out = []
    for cue in rec.cues:
        w = by_id.get(cue.window_id)
        if w is None:
            raise model.ModelError(
                f"cue {cue.id!r} names window {cue.window_id!r}, which is absent")
        out.append(w)
    return out


def s1(mets: Sequence[model.Window], i: int, thr: Thresholds) -> bool:
    """NOT counted toward INV-6. S2's secondary gate only (D1).

    "Demoted to a secondary gate" has to MEAN something: a demoted signal that
    still floats free is just an uncounted signal. The consequence is concrete --
    idx 346 (compression ratio 1.2740, below this file's median) fails S2 and keeps
    only S4, so it is a note and not a deletion.
    """
    return mets[i].compression_ratio >= thr.s1_gate


def s2(rec: model.Recording, thr: Thresholds) -> set[int]:
    """Within one cue: a repeated unit AND the S1 gate."""
    mets = metrics(rec)
    return {i for i in repeat_units(rec.cues) if s1(mets, i, thr)}


def s3(rec: model.Recording) -> set[int]:
    """Every cue index inside a qualifying cross-cue run."""
    return {i for a, b in s3_runs(rec.cues) for i in range(a, b + 1)}


def s4a_count(rec: model.Recording, a: int, b: int) -> tuple[int, int]:
    """S4a: decoder state AROUND the span, excluding the span itself.

    Returns `(bad, total)` so a caller can print both and a reader can check the
    fraction for themselves -- the fraction is not the rule, but hiding it would
    make the rule unauditable.
    """
    cues, mets = rec.cues, metrics(rec)
    lo = cues[a].start - WINDOW
    hi = cues[b].end + WINDOW
    near = [k for k, c in enumerate(cues)
            if not (a <= k <= b) and c.end > lo and c.start < hi]
    return sum(1 for k in near if mets[k].avg_logprob <= LP_BAD), len(near)


def s4b_fallback(rec: model.Recording, a: int, b: int) -> float:
    """S4b: the span's OWN temperature, the highest across it.

    Earlier drafts rejected temperature as a signal. That rejection was about
    NEIGHBOURHOOD temperature, which is genuinely useless here (the negatives sit
    in neighbourhoods with 22/48, 30/46 and 32/48 windows above zero). Own
    temperature is a different measurement and had never been tested -- which is
    why the rejection did not transfer.
    """
    mets = metrics(rec)
    return max(mets[k].temperature for k in range(a, b + 1))


def s4(rec: model.Recording, a: int, b: int) -> bool:
    """Decoder distress, by either measure. One signal."""
    bad, _ = s4a_count(rec, a, b)
    return bad > 0 or s4b_fallback(rec, a, b) >= TEMP_FALLBACK
