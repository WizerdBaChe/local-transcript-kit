"""Per-file thresholds. PSM section 6.1.

**Why per file rather than a constant is not a style choice.** The shipping default
(`cond=False`) and the calibration source (`cond=True`) have different
distributions on the *same recording*: maximum compression ratio 1.606 against
2.336, and the S1 gate median moves 1.2993 -> 1.2181 across the three decodes in
`docs/evidence/signal_spec_2026-09-09.txt`. A constant calibrated on one is
mis-set on the other.

Two things this module deliberately does NOT have:

* **No window-level p75.** PIM v0.7 mentioned one as an alternative and no script
  ever computed it; v0.8 deleted it (RD-5). The two numbers are not equal -- their
  verdicts are: the cue median is 1.2993 and a window p75 is 1.4741 under this
  document's convention, and on this recording both pass idx 550 and both fail idx
  346 (`docs/evidence/threshold_families_2026-09-09.txt`). An implementation that
  swapped one for the other would look correct here and would be two different
  products on the next recording.
* **No MAD or stdev family.** They exist in `threshold_families_2026-09-09.py` as
  the record of why the adopted rule was chosen over them; every family that
  passes the three controls flags 7.6-12.7% of windows and 10.0-18.6% of cues,
  against the adopted rule's 0.13% of cues. `mean + 2*stdev` on the cue population
  lands at 1.9736, **above** the positive control at 1.7877: the right tail
  inflates stdev past the very case the instrument is meant to catch.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Sequence

from ..core import model


@dataclass(frozen=True, slots=True)
class Thresholds:
    """Everything the signals need that depends on the file under analysis.

    A frozen object rather than loose numbers, so that a caller cannot compute one
    gate from this file and another from a constant -- which is exactly how the two
    distributions above would get mixed.
    """

    cr_median: float
    #: INV-1: a threshold computed over a population states the population.
    cue_population: int

    @property
    def s1_gate(self) -> float:
        return self.cr_median


def percentile(values: Sequence[float], q: float) -> float:
    """Linear interpolation, stated once and used everywhere (PSM §6.1).

    The convention is load-bearing rather than pedantic: the same window p75 is
    1.4741 by interpolation and 1.4819 by nearest-rank, and a document that quotes
    one without naming the convention has published a number its reader cannot
    check (round 7 RF-4).
    """
    if not values:
        raise model.ModelError("no values: a percentile needs a population")
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] + (xs[hi] - xs[lo]) * frac


def compression_ratios(rec: model.Recording) -> list[float]:
    """One value per CUE, resolved through its window.

    Cue-level, not window-level: `cr_median` is the median over cues (PSM §6.1),
    and the two populations are different sizes -- 789 cues against 79 windows on
    the baseline -- so which one is counted changes the number.
    """
    by_id = {w.id: w for w in rec.windows}
    out = []
    for cue in rec.cues:
        w = by_id.get(cue.window_id)
        if w is None:
            raise model.ModelError(
                f"cue {cue.id!r} names window {cue.window_id!r}, which is absent; "
                f"a metric with no population is not a metric (INV-1)")
        out.append(w.compression_ratio)
    return out


def of(rec: model.Recording) -> Thresholds:
    values = compression_ratios(rec)
    if not values:
        raise model.ModelError("a recording with no cues has no thresholds")
    return Thresholds(cr_median=statistics.median(values),
                      cue_population=len(values))
