"""Nomination, and the partition that stops a case from vanishing.

PSM §6.25 / PIM §4.05. The defect this file exists for (round 6, RC-1) was not a
wrong number: it was a candidate that reached **no output at all** while the
document described its verdict, because the instrument built spans from S2 and S3
*membership* and a cue satisfying only S4 was never evaluated. So the assertions
here are about the SHAPE of the output -- every candidate lands somewhere, the
spans are disjoint, and nothing but repetition nominates -- rather than about
which spans this recording happens to have.

Two implementations of one rule now exist -- `candidates.candidates()` and the
streaming walk inside `detectors.EngineLoop.scan` (§8.1 needs the second, RB-7) --
so one test here does nothing but hold them equal. **A rule with two
implementations has two behaviours the day nobody checks.**
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from fixtures import decodes
from ltm.core import model
from ltm.detect import candidates, detectors, score, thresholds

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "ltm" / "detect"
CONTROL = pathlib.Path(__file__).resolve().parent / "fixtures" / "nominating_candidates.py_txt"

IN_REPO = decodes.in_repo()


@pytest.fixture(params=sorted(IN_REPO))
def decode(request):
    return IN_REPO[request.param]


def _imported(path: pathlib.Path) -> set[str]:
    """Every module name this file imports, relative forms included."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names.add(base)
            names.update(f"{base}.{a.name}" for a in node.names)
    return names


def _mentions_signals(names: set[str]) -> bool:
    # Match a NAME, never a free substring of the file: `signals` appears in
    # `candidates.py`'s prose ten times and must not fire (the same rule as R8's
    # path-component matching).
    return any(part == "signals" for n in names for part in n.split("."))


def test_candidates_module_cannot_see_s4():
    """INV-6 / §6.25: only repetition nominates, and an absent import is the proof.

    A comment saying "do not use S4 here" is only as good as the next reader's
    attention. This is the known-TRUE side: the shipping module is clean.
    """
    assert not _mentions_signals(_imported(SRC / "candidates.py"))


def test_negative_control_a_nominating_candidate_module_is_caught():
    """And the checker is shown a violation, or it is not known to be a checker."""
    assert _mentions_signals(_imported(CONTROL))


def test_the_edge_runs_the_other_way():
    """`signals.py` imports the two structure functions rather than copying them.

    The direction is the contract: S2 must stay "a repeat unit AND the CR gate" in
    one place. If this ever fails, the gates have been re-implemented next to the
    structure and the two definitions can differ by a constant nobody notices.
    """
    names = _imported(SRC / "signals.py")
    assert any(part == "candidates" for n in names for part in n.split("."))


def test_partition_sums(decode):
    """Every candidate lands in exactly one bucket, and the buckets sum.

    A case that silently vanishes is the exact defect this step was written to
    close, so the sum is asserted rather than the bucket contents.
    """
    thr = thresholds.of(decode)
    cands = candidates.candidates(decode.cues)
    part = score.partition(score.score_all(decode, thr))
    assert len(cands) == sum(len(v) for v in part.values())
    assert {s.disposition for s in part["proposal"]} <= {"proposal"}


def test_negative_control_a_lost_candidate_makes_the_sum_go_red(decode):
    """The sum above must be able to FAIL, or it is decoration.

    Dropping one scored candidate is exactly RC-1's shape -- judged, then absent --
    and the assertion has to catch it.
    """
    thr = thresholds.of(decode)
    cands = candidates.candidates(decode.cues)
    scored = score.score_all(decode, thr)[1:]        # lose one, as RC-1 did
    part = score.partition(scored)
    assert len(cands) != sum(len(v) for v in part.values())


def test_only_repetition_nominates(decode):
    """The third assertion of §6.25, and the one that fails if S4 nominates.

    It must be here even though nothing today violates it: that is what keeps it
    true. S4(a)'s population on the baseline is 205 against repetition's 3, so the
    failure would not be subtle -- it would be three orders of magnitude.
    """
    runs = candidates.s3_runs(decode.cues)
    near = candidates.s3_near_runs(decode.cues)
    units = candidates.repeat_units(decode.cues)
    for a, b, kind in candidates.candidates(decode.cues):
        if kind == "run":
            assert (a, b) in runs
        elif kind == "near-run":
            # The shoulder is still REPETITION nominating -- shorter, not a
            # different signal. S4 gained no power to nominate from A-20.
            assert (a, b) in near
        else:
            assert a == b and a in units


def _run_of(n: int, text: str = "九年") -> list[model.Cue]:
    """`n` adjacent cues with identical text, with clean neighbours either side.

    The padding is not decoration: without a distinct cue on each end the run is
    the whole sequence and `same_text_groups` cannot show that the group CLOSED,
    which is the moment nomination happens.
    """
    body = [model.Cue(f"c{i}", float(i), float(i + 1), text, "w0", "s0")
            for i in range(1, n + 1)]
    return ([model.Cue("c0", 0.0, 1.0, "開始", "w0", "s0")]
            + body
            + [model.Cue(f"c{n + 1}", float(n + 1), float(n + 2), "結束",
                         "w0", "s0")])


@pytest.mark.parametrize("length,kind", [
    (1, None), (2, None), (3, None),        # below the shoulder: invisible
    (4, "near-run"),                        # the shoulder (A-20)
    (5, "run"), (6, "run"), (7, "run"),     # the bar
])
def test_every_run_length_around_the_two_thresholds(length, kind):
    """Rung 1: the domain is finite and small, so enumerate it (A-20 / UAT A-4).

    The thresholds are 4 and 5 and the whole question is what happens at each of
    them, so a spot check at one length would leave the other boundary asserted
    nowhere. `None` means the run was not nominated at all -- not dropped,
    ABSENT -- which is the state the shoulder was added to shrink.

    The neighbours are what makes this a test of length alone: identical text,
    identical metrics, one cue more or less.
    """
    cues = _run_of(length)
    got = [(a, b, k) for a, b, k in candidates.candidates(cues) if k != "cue"]
    if kind is None:
        assert got == []
    else:
        assert got == [(1, length, kind)]


def test_a_length_the_shoulder_admits_still_obeys_the_punctuation_rule():
    """The shoulder inherits S3's alphanumeric rule, and here is the control.

    Four cues of '...' are the case `S3_MIN_ALNUM` exists for, and a shoulder that
    forgot it would surface exactly the groups the full rule spends a constant
    excluding -- a loosening that lands on punctuation is how a detector starts
    crying wolf. Four cues of a one-character word stay admitted, which is the
    other half of the same rule (D3).
    """
    assert candidates.candidates(_run_of(4, "...")) == []
    assert candidates.candidates(_run_of(4, "對")) == [(1, 4, "near-run")]


def test_candidate_spans_are_pairwise_disjoint(decode):
    """S2 and S3 cannot both be credited on one span (RC-4), by construction.

    Which is why there is no precedence rule to maintain: a cue inside a
    qualifying run is never offered as a within-cue candidate.
    """
    seen: set[int] = set()
    for a, b, _kind in candidates.candidates(decode.cues):
        span = set(range(a, b + 1))
        assert not (span & seen)
        seen |= span


def test_the_streaming_detector_nominates_exactly_what_candidates_does(decode):
    """The two implementations of one rule, held equal.

    `EngineLoop.scan` walks cue by cue because INV-22 limit (a) requires it
    (RB-7), and `candidates()` is the definition §6.25 asserts against. Neither
    can be deleted, so this test is the seam: it fails the day the streaming walk
    starts grouping differently, which is the only way a run could be nominated
    from one and not the other.
    """
    thr = thresholds.of(decode)
    walked = sorted(v.span for _i, batch in detectors.EngineLoop().scan(decode, thr)
                    for v in batch)
    declared = sorted((a, b) for a, b, _k in candidates.candidates(decode.cues))
    assert walked == declared


def test_the_streaming_detector_advances_once_per_cue(decode):
    """RB-7 as an exhaustive enumeration, not a stopwatch (rung 1).

    A timing assertion cannot go red here -- 789 cues take milliseconds either
    way -- so the property checked is the index sequence, which a whole-sequence
    detector cannot produce. The negative control lives in `test_detect.py`,
    where a deliberately whole-sequence fake fails this same check.
    """
    thr = thresholds.of(decode)
    steps = [i for i, _batch in detectors.EngineLoop().scan(decode, thr)]
    assert steps == list(range(len(decode.cues)))


def test_the_baseline_candidate_set_when_the_payload_is_present():
    """The labelled decode's three candidates, or a skip that names the variable.

    Skip and not pass (INV-17): a run without the baseline has lost the labelled
    control set, and reporting a smaller green would hide that.

    **The seam is checked here too** (verifier V-4). The two in-repo decodes above
    exercise it, but the baseline is the only one carrying the labelled control set
    and the 9x run, so it is the decode a grouping bug would be worst on and the one
    the streaming walk was never asked about.
    """
    rec = decodes.baseline()
    got = candidates.candidates(rec.cues)
    assert got == [(303, 311, "run"), (449, 452, "near-run"),
                   (346, 346, "cue"), (550, 550, "cue")]

    thr = thresholds.of(rec)
    walked = sorted(v.span for _i, batch in detectors.EngineLoop().scan(rec, thr)
                    for v in batch)
    assert walked == sorted((a, b) for a, b, _k in got)
    steps = [i for i, _batch in detectors.EngineLoop().scan(rec, thr)]
    assert steps == list(range(len(rec.cues)))
