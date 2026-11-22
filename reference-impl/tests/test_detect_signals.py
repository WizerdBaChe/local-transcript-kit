"""The signals, against `docs/evidence/signal_spec_2026-09-09.txt`.

PSM §6.2 and §6.3. **That script is the contract and this file is what says the
module still matches it** -- if the two disagree the script is right. Every number
asserted here is a cell of its printed output, quoted rather than recomputed by a
second method: a test that re-derives the expected value with its own arithmetic
is two implementations agreeing, not a check.

**A run of the three decodes IS the two-sided calibration** (INV-9): `cond=False`
finds nothing on a decode with no loops, `cond=True` finds the loop on a decode
that has one. Both are in the repository, so the calibration never depends on the
environment. The baseline adds the LABELLED control set -- the 9x loop, the six
known-false cases, idx 550 and idx 346 -- and when it is absent those tests skip
naming the variable instead of reporting a smaller green (INV-17).
"""
from __future__ import annotations

import dataclasses

import pytest

from fixtures import decodes
from ltm.detect import candidates, detectors, score, signals, thresholds

IN_REPO = decodes.in_repo()

#: PSM §6.3's table, one row per decode, as printed by the instrument.
EXPECTED = {
    decodes.BASELINE: dict(
        cues=789, gate=1.2993,
        # `(449, 452)` is the S3 shoulder (A-20). It is the ONLY four-cue group in
        # this recording and it is a hand-labelled known-false case, so this row
        # is also the shoulder's measured price: one note, on this decode.
        candidates=[(303, 311, "run"), (449, 452, "near-run"),
                    (346, 346, "cue"), (550, 550, "cue")],
        proposals=[(303, 311)],
        notes=[(346, 346), (449, 452), (550, 550)], dropped=[]),
    decodes.COND_TRUE: dict(
        cues=936, gate=1.2699,
        candidates=[(412, 438, "run")],
        proposals=[(412, 438)], notes=[], dropped=[]),
    decodes.COND_FALSE: dict(
        cues=659, gate=1.2181,
        candidates=[(314, 319, "run"), (250, 250, "cue"), (488, 488, "cue")],
        # `(314, 319)` moved from note to proposal under A-25: a six-cue run of
        # the single character `1`, with S4 silent because the decoder never
        # noticed. It is the only span the amendment moves across all three
        # decodes in this repository -- that count IS the amendment's price.
        proposals=[(314, 319)], notes=[(250, 250)], dropped=[(488, 488)]),
}

#: The labelled control set, from the instrument's control table. `None` is the
#: unlabelled case: **nobody has listened to idx 346's audio** (open item L), so
#: it is not claimed as either.
LABELLED = [
    ("positive cross-cue 303-311 x9", (303, 311), True),
    ("positive within-cue idx 550 x7", (550, 550), True),
    ("negative I don't know x3", (445, 447), False),
    ("negative Nine years x4", (449, 452), False),
    ("negative Yeah x3", (468, 470), False),
    ("negative Okay x3", (472, 474), False),
    ("negative dengdengdeng idx 237", (237, 237), False),
    ("negative Any... idx 749", (749, 749), False),
    ("unlabelled idx 346", (346, 346), None),
]

#: The S4a neighbourhood counts the instrument prints, `(bad, total)`.
S4A = {
    (303, 311): (31, 61), (550, 550): (0, 8), (445, 447): (0, 48),
    (449, 452): (0, 47), (468, 470): (0, 46), (472, 474): (0, 48),
    (237, 237): (0, 46), (749, 749): (0, 16), (346, 346): (24, 44),
}


def _outcome(rec, thr):
    """`span -> disposition`, read off the detector and never recomputed.

    RC-1's structural half: the instrument's control table used to recompute `n`
    for an arbitrary index, which let it disagree with the detector while looking
    identical. So this asks the shipping detector.
    """
    out = {}
    for _i, batch in detectors.EngineLoop().scan(rec, thr):
        for v in batch:
            out[v.span] = v.disposition
    return out


def _spans(part, key):
    return sorted((s.a, s.b) for s in part[key])


@pytest.fixture(params=sorted(IN_REPO))
def in_repo(request):
    return request.param, IN_REPO[request.param]


def test_reproduces_the_instrument_on_the_in_repo_decodes(in_repo):
    """The two columns that make the other three checkable: candidates and dropped.

    Three plus zero is a different fact from three minus one plus one, which is why
    the table carries both.
    """
    name, rec = in_repo
    want = EXPECTED[name]
    thr = thresholds.of(rec)
    assert len(rec.cues) == want["cues"]
    assert round(thr.cr_median, 4) == want["gate"]
    # INV-1: the gate carries the population it was computed over, and it is the
    # CUE count -- 789 cues against 79 windows on the baseline, so the two
    # populations are different numbers and the field has to say which (verifier
    # V-2 found it asserted nowhere, which is how a threshold ends up quoting a
    # population it did not use).
    assert thr.cue_population == want["cues"]
    assert candidates.candidates(rec.cues) == want["candidates"]
    part = score.partition(score.score_all(rec, thr))
    assert _spans(part, "proposal") == sorted(want["proposals"])
    assert _spans(part, "note") == sorted(want["notes"])
    assert _spans(part, "dropped") == sorted(want["dropped"])


def test_the_gates_population_follows_the_cues_it_measured():
    """V-2's known-false control: the field is arithmetic, not a copy of a constant.

    Halve the cues and the population halves with them. Without this the number
    could be any of three plausible things -- cue count, window count, or a stale
    literal -- and all three read the same on a passing suite.
    """
    rec = IN_REPO[decodes.COND_FALSE]
    full = thresholds.of(rec)
    assert full.cue_population == len(rec.cues)
    half = thresholds.of(dataclasses.replace(rec, cues=rec.cues[:100]))
    assert half.cue_population == 100
    assert half.cue_population != len(rec.windows)


def test_reproduces_the_instrument_on_the_baseline():
    rec = decodes.baseline()
    want = EXPECTED[decodes.BASELINE]
    thr = thresholds.of(rec)
    assert len(rec.cues) == want["cues"]
    assert round(thr.cr_median, 4) == want["gate"]
    assert thr.cue_population == want["cues"]
    assert candidates.candidates(rec.cues) == want["candidates"]
    part = score.partition(score.score_all(rec, thr))
    assert _spans(part, "proposal") == sorted(want["proposals"])
    assert _spans(part, "note") == sorted(want["notes"])
    assert _spans(part, "dropped") == sorted(want["dropped"])


def test_the_calibration_is_two_sided_and_entirely_in_repo():
    """INV-9's pair, with no external prerequisite.

    A gate with only the negative side is scored 100% by a rule that rejects
    everything; a gate with only the positive side is scored 100% by a rule that
    accepts everything. Both, or the detector does not ship.

    **This assertion was restated on 2026-09-09 (A-25), and the reason is the
    whole point of keeping controls instead of deleting them.** It used to read
    "the quiet decode proposes nothing", which stood for the belief that the
    `cond=False` decode had no loop in it. A-25 falsified that belief: idx
    314-319 is **six consecutive cues each holding the single character `1`**,
    24 seconds of it, sitting between 「那么,这是什么课程?」 and "she has to
    take the class from 1 to 4". That is a decoder loop, and the old rule missed
    it because the decoder stayed calm throughout -- which is the exact hole
    UAT A-9 was answered to close.

    So the control is not weakened, it is made SHARPER. "Proposes nothing" was
    only ever evidence about one number; what a calibration has to show is
    DISCRIMINATION, and this decode discriminates: three candidates, three
    different verdicts.
    """
    positive = IN_REPO[decodes.COND_TRUE]
    negative = IN_REPO[decodes.COND_FALSE]
    pos, _ = detectors.run(positive)
    neg, _ = detectors.run(negative)
    assert sum(pos.summary.proposals.values()) == 1

    # The negative side: three candidates, and one of each verdict. A rule that
    # accepted everything would give three proposals; one that rejected
    # everything would give none. Neither passes this line.
    assert len(candidates.candidates(negative.cues)) == 3
    part = score.partition(score.score_all(negative, thresholds.of(negative)))
    assert [len(part[k]) for k in ("proposal", "note", "dropped")] == [1, 1, 1]

    # And the one it proposes is the loop, quoted, so a later change that moves
    # this number cannot claim it is still pointing at the same thing.
    assert sum(neg.summary.proposals.values()) == 1
    assert {negative.cues[i].text for i in range(314, 320)} == {"1"}


def test_single_signal_yields_note_not_proposal():
    """INV-6 executed, on the two cases that hurt.

    idx 550 is 7 repetitions inside one cue with a CLEAN neighbourhood (0/8) and
    temperature 0.0 -- the decoder was calm and confident while repeating itself
    seven times. It is a note, and that is a real detection hole reported honestly
    (open item M), not a tidy result. idx 346 keeps only S4 because its
    compression ratio 1.2740 is below the file median: nobody has listened to that
    audio (open item L), and round five blocked the reading that made it a
    deletion. **A change that turns either into a proposal has weakened INV-6 and
    must fail here.**
    """
    rec = decodes.baseline()
    thr = thresholds.of(rec)
    outcome = _outcome(rec, thr)
    assert outcome[(550, 550)] == "note"
    assert outcome[(346, 346)] == "note"
    scored = {(s.a, s.b): s for s in score.score_all(rec, thr)}
    assert scored[(550, 550)].n == 1 and scored[(550, 550)].n2 == 1
    assert scored[(346, 346)].n == 1 and scored[(346, 346)].n4 == 1


def test_two_positives_need_different_pairs():
    """The test that keeps S4 an OR, and it fails if anyone "simplifies" it.

    `303-311` is caught by S3+S4(a): its neighbourhood decoded badly (31/61) while
    its own temperature never left 0.0. `412-438` is caught by S3+S4(b): its
    neighbourhood is CLEAN (0/14) and the engine's own temperature climbed to 1.0.
    Neither measure alone catches both.
    """
    base = decodes.baseline()
    loud = IN_REPO[decodes.COND_TRUE]

    a_bad, a_tot = signals.s4a_count(base, 303, 311)
    assert (a_bad, a_tot) == (31, 61)
    assert signals.s4b_fallback(base, 303, 311) == 0.0      # (b) alone misses it

    b_bad, b_tot = signals.s4a_count(loud, 412, 438)
    assert (b_bad, b_tot) == (0, 14)                        # (a) alone misses it
    assert signals.s4b_fallback(loud, 412, 438) == 1.0

    assert signals.s4(base, 303, 311) and signals.s4(loud, 412, 438)


def test_a_full_run_alone_proposes_and_the_carve_out_stops_there():
    """A-25's price, and the regression case a loosened gate has to ship with.

    What the old rule caught that this one does not: "one signal never proposes
    a deletion". A-25 carves an exception out of that, so the test has to show
    the carve-out is NARROW -- otherwise the rule has quietly become "one signal
    proposes".

    Priced over every decode in this repository. Exactly one span moves, and it
    is `cond=False` idx 314-319: six cues in a row, each of them the single
    character `1`. The two mechanisms that used to hold it are both still there
    and both still visible -- S4 does not vote (`n == 1`) and it is not a
    near-run -- so what moved it is the kind and only the kind.

    And the three ways a single signal is still refused a proposal, each on real
    data: a within-cue repeat (idx 550, seven repetitions inside one cue, calm
    decoder), an S4-only cue (idx 346), and the shoulder (449-452, `Nine years`
    said four times). If any of those ever proposes, the carve-out leaked.
    """
    moved = []
    for name, rec in list(IN_REPO.items()) + [(decodes.BASELINE, decodes.baseline())]:
        thr = thresholds.of(rec)
        for s in score.score_all(rec, thr):
            if s.kind == "run" and s.n < 2:
                moved.append((name, (s.a, s.b), s.n))
    assert moved == [(decodes.COND_FALSE, (314, 319), 1)], moved

    quiet = IN_REPO[decodes.COND_FALSE]
    assert [quiet.cues[i].text for i in range(314, 320)] == ["1"] * 6

    rec = decodes.baseline()
    outcome = _outcome(rec, thresholds.of(rec))
    for span in ((550, 550), (346, 346), (449, 452)):
        assert outcome.get(span) == "note", (span, outcome.get(span))


def test_no_known_false_control_can_ever_become_a_proposal():
    """The claim that survived the shoulder, and it is the one that matters.

    Five of the six are still not candidates at all -- repetition never nominated
    them, and a case that was never nominated cannot be turned into a proposal by
    a scoring change. The sixth is priced in the next test. **No known-false case
    may propose a deletion**, and that is asserted here for all six regardless of
    which mechanism keeps them out, so a future change cannot satisfy this by
    moving a case from one mechanism to the other.
    """
    rec = decodes.baseline()
    thr = thresholds.of(rec)
    outcome = _outcome(rec, thr)
    for label, span, truth in LABELLED:
        if truth is False:
            assert outcome.get(span, "not nominated") != "proposal", label


def test_the_shoulder_costs_exactly_one_known_false_note_on_this_recording():
    """The price of A-20, stated as a number instead of a hope.

    Loosening a nomination rule buys sensitivity with false alarms, and the honest
    form of that trade is a count taken on real audio rather than an assurance.
    Measured over the whole 36.4-minute baseline: **the group-size histogram is
    {2: 14, 3: 3, 4: 1, 9: 1}**, so exactly ONE group sits on the shoulder -- and
    it is `Nine years x4`, a hand-labelled known-false case. The other two decodes
    in the repo have no four-cue group at all, so on this recording the shoulder's
    entire cost is this one note.

    It stays a note by TWO independent mechanisms, and the test asserts both: the
    cap (`capped`, which holds whatever votes arrive) and the fact that S4 does not
    hold here anyway (`n == 1`). If a later change removes the cap, this test still
    passes on this datum -- which is why `test_the_cap_is_what_holds_a_near_run_at_a_note`
    exists with its own positive control.
    """
    rec = decodes.baseline()
    thr = thresholds.of(rec)
    outcome = _outcome(rec, thr)
    scored = {(s.a, s.b): s for s in score.score_all(rec, thr)}

    priced = [span for label, span, truth in LABELLED
              if truth is False and span in outcome]
    assert priced == [(449, 452)]
    assert outcome[(449, 452)] == "note"
    assert scored[(449, 452)].kind == "near-run"
    assert scored[(449, 452)].capped
    assert scored[(449, 452)].n == 1          # S4 does not hold here either

    sizes = [b - a + 1 for a, b in candidates.same_text_groups(rec.cues)]
    assert sorted(n for n in sizes if n >= 2) == [2] * 14 + [3] * 3 + [4, 9]


def test_the_cap_is_what_holds_a_near_run_at_a_note():
    """The positive control for the cap, because the baseline cannot provide one.

    On real audio the one near-run has `n == 1`, so the baseline cannot tell a
    working cap from an absent one -- a cap that has only ever been asked about
    spans that were notes anyway is not known to be doing anything. Here the same
    span is scored as a full run and as a near-run: the full run reaches
    `proposal`, the near-run does not, and the ONLY difference is the kind.
    """
    rec = decodes.baseline()
    thr = thresholds.of(rec)
    full = score.score(rec, 303, 311, "run", thr)
    near = score.score(rec, 303, 311, "near-run", thr)
    assert full.n == near.n == 2               # identical evidence...
    assert full.disposition == "proposal"      # ...and different conclusions
    assert near.disposition == "note"
    assert near.capped and not full.capped


def test_s4b_separation_is_a_rung_of_the_engines_own_ladder():
    """0.4 is a separation, not a fitted number.

    faster-whisper retries up (0.0, 0.2, 0.4, 0.6, 0.8, 1.0); every one of the six
    known-false controls tops out at 0.2, one rung, and the 27x loop runs 0.4 to
    1.0. A threshold that sits between a measured maximum and a measured minimum
    is calibrated; one chosen inside a cluster is not.
    """
    rec = decodes.baseline()
    tops = [signals.s4b_fallback(rec, a, b)
            for _l, (a, b), truth in LABELLED if truth is False]
    assert max(tops) == 0.2
    assert signals.TEMP_FALLBACK == 0.4 > max(tops)


def test_s4a_is_a_count_and_no_ratio_threshold_is_calibratable():
    """D2's evidence, quoted: every negative is 0.0000 and the positive 0.5082.

    Nothing lies between, so 0.0001, 0.25 and 0.49 give an identical partition --
    writing one of them would imply a calibration that never happened. This test
    is what makes that statement re-checkable rather than remembered.
    """
    rec = decodes.baseline()
    fractions = {}
    for label, (a, b), truth in LABELLED:
        bad, tot = signals.s4a_count(rec, a, b)
        assert (bad, tot) == S4A[(a, b)], label
        fractions[label] = bad / tot if tot else 0.0
    negatives = [f for l, f in fractions.items() if l.startswith("negative")]
    assert set(negatives) == {0.0}
    assert round(fractions["positive cross-cue 303-311 x9"], 4) == 0.5082


def test_s1_is_not_an_independent_signal():
    """Overlap 100%: every cue S3 selects is already in S1.

    Two measures of one thing are not two signals however the invariant is
    worded -- and S1 is gzip compressibility while S3 counts text repetition, so
    crediting both asks one question twice.
    """
    rec = decodes.baseline()
    thr = thresholds.of(rec)
    mets = signals.metrics(rec)
    s1 = {i for i in range(len(rec.cues)) if signals.s1(mets, i, thr)}
    s3 = signals.s3(rec)
    assert len(s3) == 9
    assert s3 <= s1                              # overlap 100% of the smaller
    assert len(s1) == 396


def test_s4s_population_is_why_it_may_not_nominate():
    """205 against 3. The reason the module boundary exists, as a number.

    S4(a) holds for any cue within 30 s of a badly-scored one, and this recording
    has 29 such cues. A nominating S4 would emit hundreds of candidates whose only
    evidence is "somebody nearby decoded badly".
    """
    rec = decodes.baseline()
    s4 = {i for i in range(len(rec.cues)) if signals.s4(rec, i, i)}
    assert len(s4) == 205
    # Four since A-20 added the shoulder, and the ratio is the point: 205 against
    # 4 is the same argument 205 against 3 was.
    assert len(candidates.candidates(rec.cues)) == 4


def test_the_gate_is_the_cue_median_and_a_window_percentile_is_a_different_number():
    """RH-6: the two numbers are not equal -- their VERDICTS are, here.

    1.2993 against 1.4741 under this document's linear-interpolation convention,
    and on this recording both pass idx 550 and both fail idx 346. So an
    implementation that quietly swapped one for the other would look correct here
    and would be two products on the next recording. This test pins which one
    ships; it also records why the coincidence is not a licence.
    """
    rec = decodes.baseline()
    thr = thresholds.of(rec)
    window_p75 = thresholds.percentile(
        [w.compression_ratio for w in rec.windows], 0.75)
    assert round(thr.cr_median, 4) == 1.2993
    assert round(window_p75, 4) == 1.4741
    assert thr.s1_gate != window_p75
    mets = signals.metrics(rec)
    for i, verdict in ((550, True), (346, False)):
        assert (mets[i].compression_ratio >= thr.s1_gate) is verdict
        assert (mets[i].compression_ratio >= window_p75) is verdict


def test_the_window_population_counts_runs_not_distinct_tuples():
    """79 runs, 77 distinct tuples (PSM §6.1 / PIM §4.1.4).

    Counting distinct would shrink every window-level denominator by two, silently
    and in the safe-looking direction.
    """
    rec = decodes.baseline()
    assert len(rec.windows) == 79
    distinct = {(w.avg_logprob, w.temperature, w.compression_ratio,
                 w.no_speech_prob) for w in rec.windows}
    assert len(distinct) == 77
