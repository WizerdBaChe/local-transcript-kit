"""Gap Q: the instrument and the module, recomputed side by side.

`docs/evidence/signal_spec_2026-09-09.py` is THE definition -- `signals.py` says so
in its first line, and `test_detect_signals.py` asserts the module reproduces the
numbers that script PRINTED. Those numbers are transcribed by hand into `EXPECTED`.
That catches a change in the module. **It cannot catch a change in the script**,
because nothing re-reads it: edit a constant in the contract and the transcribed
numbers keep agreeing with a file that no longer says what they say. The two would
drift apart while a green suite reported that they match.

So this file imports the script and recomputes BOTH sides from the SAME fixtures,
comparing sets and votes rather than a remembered table. Nothing here is hand-copied
from the script's output; that is the whole point of it existing beside the other
file rather than instead of it.

**The two divergences are ruled amendments, and they are named here rather than
tolerated.** The product has moved past the 2026-09-09 script twice:

* **A-20, `near-run`.** `candidates.py` also nominates four-cue groups, capped so
  they can only ever be notes. The script has no such kind. The product therefore
  has candidates the script does not.
* **A-25, `stands_alone`.** A full run proposes on one signal. The script's rule is
  `n >= 2 -> proposal`, so the two disagree on exactly the spans where a run scores
  `n == 1`.

Any OTHER difference is drift, which is what gap Q asked for. The list of permitted
exceptions is asserted to be EXACTLY these -- a third one fails this file, and the
day an amendment is withdrawn the assertion fails too rather than quietly passing.

Controls, both directions, because a comparison that has never failed is not known
to be comparing: `test_a_drift_in_the_script_is_caught` moves a constant in the
script's namespace, `test_a_drift_in_the_module_is_caught` moves the matching one in
`signals`, and each must break the agreement it belongs to.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

from fixtures import decodes
from ltm.detect import candidates, score, signals, thresholds

SPEC_PATH = (pathlib.Path(__file__).resolve().parents[1]
             / "docs" / "evidence" / "signal_spec_2026-09-09.py")

#: The spans where the product deliberately disagrees with the 2026-09-09 script,
#: one row per amendment, measured over every decode in this repository. A-20 adds
#: candidates the script never nominates; A-25 moves one span from note to proposal.
#: These are the ONLY permitted differences.
A20_EXTRA_CANDIDATES = {decodes.BASELINE: [(449, 452)]}
A25_MOVED = {decodes.COND_FALSE: [(314, 319)]}


def load_spec():
    """A FRESH module each call, so a control that mutates it cannot leak."""
    spec = importlib.util.spec_from_file_location("ltm_signal_spec", SPEC_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def segments_for(module):
    """The script's own inputs, read the way the script reads them.

    `cond_experiment_segments.json` is a FROZEN fixture (RD-2): re-decoding the same
    audio gives a different 27x run at different indices, so every index below is a
    property of the saved file and of nothing else.
    """
    base = json.loads(module.BASE_SEGMENTS.read_text("utf-8"))["segments"]
    full = json.loads((module.SP / "cond_experiment_segments.json")
                      .read_text("utf-8"))
    return {decodes.BASELINE: base,
            decodes.COND_TRUE: full["True"]["segments"],
            decodes.COND_FALSE: full["False"]["segments"]}


def recordings():
    recs = dict(decodes.in_repo())
    recs[decodes.BASELINE] = decodes.baseline()
    return recs


RECORDINGS = recordings()


@pytest.fixture(params=sorted(RECORDINGS))
def decode(request):
    module = load_spec()
    name = request.param
    return name, RECORDINGS[name], module, segments_for(module)[name]


def test_the_gate_is_the_same_number_on_both_sides(decode):
    """S1's threshold is per file; if the two populations differ, nothing below means
    anything -- so this is asserted first and separately."""
    _name, rec, module, segs = decode
    assert thresholds.of(rec).cr_median == pytest.approx(
        module.cr_median(segs), rel=0, abs=1e-12)


def test_the_signal_sets_are_identical(decode):
    """S2, S3 and S4, as SETS, recomputed on both sides from the same fixture."""
    _name, rec, module, segs = decode
    thr = thresholds.of(rec)
    gate = module.cr_median(segs)

    assert signals.s2(rec, thr) == module.s2(segs, gate)
    assert candidates.s3_runs(rec.cues) == module.s3_runs(segs)
    assert signals.s3(rec) == {i for a, b in module.s3_runs(segs)
                               for i in range(a, b + 1)}

    # S4 is asked per SPAN, so it is compared on the spans that exist, and on both
    # of its measures separately -- an OR that agreed by luck on the disjunction
    # while disagreeing on a measure would be drift that this file exists to catch.
    for a, b, _kind in candidates.candidates(rec.cues):
        assert signals.s4a_count(rec, a, b) == module.s4_count(segs, a, b), (a, b)
        assert signals.s4b_fallback(rec, a, b) == module.s4_fallback(segs, a, b)
        assert signals.s4(rec, a, b) == module.s4(segs, a, b), (a, b)


def test_the_candidates_differ_only_by_the_near_run_amendment(decode):
    """A-20's price, stated as the difference between two computed lists."""
    name, rec, module, segs = decode
    mine = candidates.candidates(rec.cues)
    theirs, _runs, _covered = module.candidates(segs)

    assert [(a, b, k) for a, b, k in mine if k != "near-run"] == theirs
    extra = [(a, b) for a, b, k in mine if k == "near-run"]
    assert extra == A20_EXTRA_CANDIDATES.get(name, []), (name, extra)


def test_the_votes_agree_on_every_shared_candidate(decode):
    """n2, n3, n4 per span -- the numbers INV-6 counts, not just the verdict.

    Asserting the disposition alone would pass while both sides reached it by
    different arithmetic, which is the failure mode that blocked v0.6 twice: two
    rules that happen to agree on every case in front of them.
    """
    _name, rec, module, segs = decode
    thr = thresholds.of(rec)
    mine = {(s.a, s.b): s for s in score.score_all(rec, thr)}
    theirs = {(a, b): (n2, n3, n4)
              for a, b, _n, _kind, n2, n3, n4 in sum(module.engine_loop(segs)[:3],
                                                     [])}

    assert set(theirs) <= set(mine)
    for span, (n2, n3, n4) in theirs.items():
        s = mine[span]
        assert (s.n2, s.n3, s.n4) == (n2, n3, n4), span


def test_the_dispositions_differ_only_by_the_stands_alone_amendment(decode):
    """A-25's price: the set of spans the two rules judge differently, as a list."""
    name, rec, module, segs = decode
    thr = thresholds.of(rec)
    mine = {(s.a, s.b): s.disposition for s in score.score_all(rec, thr)}

    proposals, notes, dropped, *_ = module.engine_loop(segs)
    theirs = {}
    for rows, verdict in ((proposals, "proposal"), (notes, "note"),
                          (dropped, "dropped")):
        for a, b, *_ in rows:
            theirs[(a, b)] = verdict

    disagree = sorted(span for span, verdict in theirs.items()
                      if mine[span] != verdict)
    assert disagree == A25_MOVED.get(name, []), (name, disagree, mine, theirs)
    for span in disagree:
        # ...and it moved for the stated reason, not by coincidence.
        s = next(s for s in score.score_all(rec, thr) if (s.a, s.b) == span)
        assert s.kind == "run" and s.n == 1 and s.stands_alone


def test_a_drift_in_the_script_is_caught():
    """Positive control, script side: move the contract and the agreement must break.

    `S3_MIN_RUN` is the constant D3's neighbour: at 7 the nine-cue loop still
    qualifies but the six-cue run in the quiet decode does not, so the sets move on
    real data rather than on an invented input.
    """
    module = load_spec()
    segs = segments_for(module)[decodes.COND_FALSE]
    rec = RECORDINGS[decodes.COND_FALSE]
    assert candidates.s3_runs(rec.cues) == module.s3_runs(segs)   # agrees first

    module.S3_MIN_RUN = 7
    assert candidates.s3_runs(rec.cues) != module.s3_runs(segs)

    # ...and a fresh load is unaffected, so the mutation did not escape this test.
    assert candidates.s3_runs(rec.cues) == load_spec().s3_runs(segs)


def test_a_drift_in_the_module_is_caught(monkeypatch):
    """Positive control, module side: the same comparison must fail from either end.

    **The mutation is derived from the data, not picked**, and the first draft of
    this test is the reason. It moved `LP_BAD` from -1.0 to -0.5 and the counts did
    not budge -- 31/61 before and after -- so the control passed nothing while
    reading as calibrated. `avg_logprob` is a per-WINDOW number that every cue in the
    window inherits, so those 61 neighbours carry **three** distinct values
    (-1.1691, -0.3084, -0.2071) and a threshold moved inside a gap is a no-op. A
    ceiling taken from the recording's own worst value cannot land in a gap: every
    neighbour then counts as badly decoded, and `(bad, total)` must reach `total`.
    """
    module = load_spec()
    segs = segments_for(module)[decodes.BASELINE]
    rec = RECORDINGS[decodes.BASELINE]
    a, b = 303, 311
    assert signals.s4a_count(rec, a, b) == module.s4_count(segs, a, b)

    bad, total = signals.s4a_count(rec, a, b)
    assert 0 < bad < total, (
        "the mutation below moves a count that is already at its ceiling into "
        "itself; if this span ever becomes all-bad, pick another one")

    monkeypatch.setattr(signals, "LP_BAD",
                        max(m.avg_logprob for m in signals.metrics(rec)))
    assert signals.s4a_count(rec, a, b) == (total, total)
    assert signals.s4a_count(rec, a, b) != module.s4_count(segs, a, b)
