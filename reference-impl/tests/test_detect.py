"""The detectors as a set, and the four rules that are about ABSENCE.

PSM §6.4 / §10. Most of this file tests things that do not happen: a detector that
is not enabled, a list that is empty, a gate that cannot run, a layer that does not
exist. That is the hard half of the product's promise -- **a gate that always
passes because it has no data is not lenient, it is fake**, and it is worse than no
gate because the reader believes it ran.

Every claim here ships with both controls. The pattern is the same each time: the
rule must hold on the shipping configuration (known-true) and must FAIL on a
deliberately wrong one (known-false), because a checker never shown a violation is
not known to detect one.
"""
from __future__ import annotations

import dataclasses
from typing import Iterator

import pytest

from fixtures import decodes
from ltm.core import editscript, model, progress
from ltm.detect import detectors, soundalike, thresholds

IN_REPO = decodes.in_repo()
LOUD = IN_REPO[decodes.COND_TRUE]          # the decode that has a loop in it
QUIET = IN_REPO[decodes.COND_FALSE]        # the shipping default, no loops


@pytest.fixture(params=sorted(IN_REPO))
def decode(request):
    return IN_REPO[request.param]


class Raiser:
    """An enabled detector that fails at runtime. BR-10's cheap second detector.

    Milestone 1 ships one enabled detector, so "one detector finds something while
    another enabled one fails" cannot arise on the real configuration -- which is
    exactly why the defect survived to round 7. A stub that raises is cheaper than
    a second product detector and tests the same field.
    """

    name = "raiser"
    may_delete = False

    def scan(self, rec, thr) -> Iterator[detectors.Step]:
        yield 0, ()
        raise RuntimeError("this detector is broken on purpose")


class WholeSequence:
    """RB-7's negative control: it consumes every cue before its first yield.

    The card's own words -- an interface that takes the whole transcript and
    returns a list "cannot satisfy limit (a)". This class is what that interface
    looks like, and the index check below must reject it.
    """

    name = "whole-sequence"
    may_delete = False

    def scan(self, rec, thr) -> Iterator[detectors.Step]:
        seen = [cue.text for cue in rec.cues]        # all of it, up front
        yield len(seen) - 1, ()


# ---------------------------------------------------------------- INV-9, §6.4

def test_default_set_excludes_uncontrolled_detectors():
    """INV-9: no control pair, no place in the default set.

    Not "off until it improves" -- off until it has a known-true AND a known-false
    control from real audio. A detector with neither cannot be told apart from one
    that fires on everything, so its output would look exactly like
    `engine-loop`'s in the same plan.
    """
    assert detectors.DEFAULT == ("engine-loop", "sparse-cue", "uncovered-span")
    # `glossary` left on 2026-10-30 with its control pair (tests/test_glossary.py).
    assert set(detectors.UNCONTROLLED) == {"speaker-repeat", "filler"}
    assert not set(detectors.DEFAULT) & set(detectors.UNCONTROLLED)


# ---------------------------------------------------------- I-27, uncovered-span

def _uncovered(rec: model.Recording) -> list[tuple[str, int, float, float]]:
    plan, _ = detectors.run(rec, enabled=("uncovered-span",), thr=_THR)
    return [(f.span.cue_id, f.span.char_start, f.evidence.as_dict()["start"],
             f.evidence.as_dict()["end"]) for f in plan.findings]


#: `_timed` recordings have no windows, so the cue-population thresholds are
#: handed in (as `test_sparse_cue_never_proposes_a_delete` does); the detector
#: under test reads none of them.
_THR = thresholds.Thresholds(cr_median=1.0, cue_population=1)


def _with_duration(rec: model.Recording, duration: float) -> model.Recording:
    return dataclasses.replace(rec, duration=duration)


def test_uncovered_span_flags_the_head_gap_i27_measured_and_not_ordinary_pauses():
    """I-27's shape, rung 0 with both controls on the asserted value.

    **Known-true**: the 2026-10-20 lecture whose first cue starts at 06:43 (403 s,
    `docs/evidence/uncovered_span_controls_2026-10-21.txt`), plus a mid-file gap
    and a tail of the same kind. **Known-false**: continuous speech whose longest
    pause is 59.9 s. The asserted value is the (anchor, offset, start, end) list.
    """
    rec = _with_duration(_timed((403.0, 410.0, "so the band diagram"),
                                (410.0, 420.0, "two dimensional gas"),
                                (500.0, 505.0, "okay so homework"),
                                (505.0, 510.0, "double heterostructure")),
                         600.0)
    assert _uncovered(rec) == [("c000", 0, 0.0, 403.0),
                               ("c002", 0, 420.0, 500.0),
                               ("c003", 22, 510.0, 600.0)]
    quiet = _with_duration(_timed((0.5, 10.0, "a"), (69.9, 80.0, "b"),
                                  (80.0, 139.0, "c")), 198.9)
    assert _uncovered(quiet) == []


def test_uncovered_span_reaches_past_an_overlapping_cue_and_never_deletes():
    """A gap is measured from the FURTHEST end so far, not the previous cue's end:
    a long cue that overlaps a short one must not open a phantom gap. And INV-8:
    the finding exists (positive control) and carries no op."""
    rec = _with_duration(_timed((0.0, 100.0, "long"), (10.0, 12.0, "short"),
                                (101.0, 110.0, "next")), 110.0)
    assert _uncovered(rec) == []
    lone = _with_duration(_timed((0.0, 5.0, "only")), 300.0)
    plan, _ = detectors.run(lone, enabled=("uncovered-span",), thr=_THR)
    assert plan.summary.findings == {"uncovered-span": 1}
    assert plan.script == () and detectors.UncoveredSpan.may_delete is False
    assert plan.findings[0].evidence.as_dict()["vad_enabled"] == 1.0


# ------------------------------------------------------------ I-23, sparse-cue

def _timed(*spans: tuple[float, float, str]) -> model.Recording:
    """A recording with only the layer `sparse-cue` reads: cue times and text."""
    cues = tuple(model.Cue(f"c{i:03d}", s, e, t, "w0", "st000")
                 for i, (s, e, t) in enumerate(spans))
    return model.Recording(
        id="r", path="p", duration=max(e for _, e, _ in spans) + 1.0, sha256="0",
        report=model.EngineReport(vad_kept=None, vad_enabled=True,
                                  condition_on_previous_text=False,
                                  multilingual=True, degenerated=False),
        stretches=(model.Stretch("st000", 0.0, 300.0, "zh", 1.0),),
        windows=(), cues=cues)


def _sparse(rec: model.Recording) -> list[int]:
    return [i for i, batch in detectors.SparseCue().scan(rec, None) if batch]


def test_sparse_cue_flags_the_shapes_m1_measured_and_not_ordinary_speech():
    """I-23's control pair, in the shapes the real engine produced.

    **Known-true**: the M1 `gap` clip's two stretched cues (26.7 s over five
    characters; 128.0 s over nine) and `far20`'s 44.3 s over one character --
    times copied from `docs/evidence/sparse_cue_controls_2026-09-27.txt`, text
    replaced by placeholders of the same length (R1). **Known-false**: ordinary
    speech at the corpus's own rate, including a long cue that is simply full.
    The asserted value is the flagged INDEX list, which the defect would change
    on both sides.
    """
    rec = _timed((50.0, 56.9, "我們今天談的是結果"),
                 (56.9, 83.6, "那就這樣吧對"),       # 26.7 s, 5 units
                 (83.6, 211.6, "好我們回來繼續講這個"),  # 128.0 s, 9 units
                 (211.6, 215.0, "第一個是"),
                 (215.0, 259.3, "嗯"),                # 44.3 s, 1 unit
                 (259.3, 279.0, "這一段很長但是每一秒都有人在說話所以字很多" * 2))
    assert _sparse(rec) == [1, 2, 4]


@pytest.mark.parametrize("seconds,units,flagged", [
    (15.0, 5, True),        # exactly both bars: 3.0 s/unit
    (15.0, 6, False),       # 2.5 s/unit, under the rate bar
    (14.9, 1, False),       # under the length bar however few the words
    (30.0, 0, True),        # no units at all counts as one, never divides by 0
])
def test_sparse_cue_bars_are_inclusive_and_both_required(seconds, units, flagged):
    """Rung 1 over the four corners of the two bars; the bars are the module's."""
    assert detectors.SPARSE_MIN_SECONDS == 15.0
    assert detectors.SPARSE_MIN_SECONDS_PER_UNIT == 3.0
    rec = _timed((0.0, seconds, "字" * units))
    assert _sparse(rec) == ([0] if flagged else [])


def test_sparse_cue_counts_words_and_characters_the_way_each_is_read():
    """A Latin word is one unit, a CJK character is one unit, punctuation none."""
    assert detectors.text_units("我們 go home, 好嗎？") == 6
    assert detectors.text_units("...!?") == 0
    assert detectors.text_units("don't stop") == 2


def test_sparse_cue_never_proposes_a_delete():
    """INV-8's shape. The words of a stretched cue are usually RIGHT -- the time
    is what is wrong -- so a delete would remove a sentence somebody said.
    **Positive control**: the finding exists, so "no ops" is not "no output"."""
    rec = _timed((0.0, 40.0, "嗯"))
    plan, _ = detectors.run(rec, enabled=("sparse-cue",),
                            thr=thresholds.Thresholds(cr_median=1.0,
                                                      cue_population=1))
    assert plan.summary.findings == {"sparse-cue": 1}
    assert plan.script == () and plan.summary.proposals == {}
    assert detectors.SparseCue.may_delete is False


def test_a_plan_lists_the_detectors_it_did_not_run():
    """`detectors_skipped` is how the absence reaches the reader.

    A quiet plan and a plan that never enabled three detectors must not look the
    same; and the skipped list is derived from what was enabled, not written out
    by hand, so enabling one moves it out of the list in the same call.
    """
    plan, _ = detectors.run(QUIET)
    assert set(plan.summary.detectors_skipped) == set(detectors.UNCONTROLLED)
    plan, _ = detectors.run(QUIET, enabled=("engine-loop", "speaker-repeat"))
    assert "speaker-repeat" not in plan.summary.detectors_skipped
    assert set(plan.summary.detectors_skipped) == {"filler"}


def test_enabling_an_uncontrolled_detector_says_so_in_one_line():
    """§6.4: `--enable filler` prints one line saying the detector has no controls.

    Printed rather than logged, because the user asked for it and is owed what it
    is worth. The known-false control is the shipping default, which must print
    nothing -- a warning that always appears is wallpaper.
    """
    assert detectors.warnings_for(detectors.DEFAULT) == []
    lines = detectors.warnings_for(("engine-loop", "filler"))
    assert len(lines) == 1 and lines[0].startswith("filler:")
    assert len(detectors.warnings_for(tuple(detectors.UNCONTROLLED))) == 2


# ------------------------------------------------------------------- INV-8

def test_speaker_repeat_never_proposes_delete():
    """INV-8, under every flag, on the decode with the worst loop in it.

    The reason is not caution about false positives: a human repeating themselves
    for emphasis and an engine looping are the SAME TEXT, so deleting the first
    removes something the speaker said. `may_delete` is a property of the class,
    and `run` refuses the plan outright if such a detector ever produces an op --
    so the rule cannot be undone by a flag, only by editing the type.
    """
    assert detectors.SpeakerRepeat.may_delete is False
    plan, verdicts = detectors.run(
        LOUD, enabled=("engine-loop", "speaker-repeat"))
    mine = [v for v in verdicts if v.code == "speaker-repeat"]
    assert mine, "the control is empty: this decode must give it something to find"
    assert all(v.ops == () for v in mine)
    assert all(not v.proposes_deletion for v in mine)
    assert "speaker-repeat" not in plan.summary.proposals
    assert "speaker-repeat" in plan.summary.findings
    for op in plan.script:
        assert op.origin != "detector:speaker-repeat"


def test_negative_control_a_deleting_speaker_repeat_is_refused(monkeypatch):
    """And the guard can fire, on the real code path.

    A detector that declares `may_delete = False` and then emits a delete is
    refused by `run` itself -- the test does not raise on its own behalf, it
    substitutes a violating detector and asserts the product catches it.
    """

    class Deleting(detectors.SpeakerRepeat):
        may_delete = False                      # the declaration it will break

        def scan(self, rec, thr):
            for i, batch in super().scan(rec, thr):
                yield i, tuple(
                    detectors.Verdict(
                        v.disposition, v.code, v.span, v.signals,
                        finding=v.finding, note=v.note,
                        ops=detectors._whole_cue_deletes(
                            rec, v.span[0], v.span[1], v.code))
                    for v in batch)

    monkeypatch.setattr(detectors, "build",
                        lambda enabled, terms=None, lex=None: [Deleting()])
    with pytest.raises(detectors.DetectError) as e:
        detectors.run(LOUD)
    assert "INV-8" in str(e.value)


# ------------------------------------------------------------------- INV-7

def test_empty_vocabulary_finds_nothing():
    """INV-7: an empty declared list calibrates nothing, so it finds nothing.

    It does not fall back to a built-in list of fillers, because a built-in list is
    an undeclared rule wearing the user's clothes -- and nobody could tell from the
    output which of the two produced a finding.
    """
    plan, verdicts = detectors.run(QUIET, enabled=("engine-loop", "filler"),
                                   terms={"filler": ()})
    assert [v for v in verdicts if v.code == "filler"] == []
    assert "filler" not in plan.summary.findings

    # ...and the known-true side: a declared term IS found, so the emptiness above
    # is the rule and not a broken detector.
    word = QUIET.cues[0].text[:2]
    _plan, verdicts = detectors.run(QUIET, enabled=("engine-loop", "filler"),
                                    terms={"filler": (word,)})
    assert [v for v in verdicts if v.code == "filler"]


def test_the_declared_list_is_an_upper_bound_not_a_seed():
    """Every `glossary` finding is about a DECLARED term and nothing else.

    Rewritten 2026-10-30 with the detector (it used to pin the placeholder's
    「the term itself is found」, which is the opposite of PIM §6.4's definition:
    a hit is where the text DIFFERS from the declared form). A `--term` entry
    declares a correct form with no wrong forms, so every finding is a
    sound-alike of it, never the word itself, and carries no op.
    """
    word = next(c.text[i:i + 2] for c in QUIET.cues for i in range(len(c.text) - 1)
                if soundalike.is_cjk_term(c.text[i:i + 2]))
    _plan, verdicts = detectors.run(QUIET, enabled=("engine-loop", "glossary"),
                                    terms={"glossary": (word,)})
    for v in verdicts:
        if v.code == "glossary":
            cue = next(c for c in QUIET.cues if c.id == v.finding.span.cue_id)
            found = cue.text[v.finding.span.char_start:v.finding.span.char_end]
            assert found != word
            assert soundalike.sounds_like(found, word, fuzzy=True)
            assert v.finding.evidence.as_dict()["entry"] == 0.0
            assert v.ops == ()


def test_rule_not_applied_to_engine_loop():
    """INV-7 must NOT bound `engine-loop`: nobody declares a hallucination first.

    Bounding it by a list would bound it to zero, so the request is refused rather
    than ignored -- an ignored `terms={"engine-loop": [...]}` would let a caller
    believe the detector was scoped when it was not.
    """
    with pytest.raises(detectors.DetectError) as e:
        detectors.run(QUIET, terms={"engine-loop": ("對",)})
    assert "engine-loop" in str(e.value)
    assert detectors.DECLARED == ("filler", "glossary")
    # The known-false control for the same guard: a legal `terms` entry passes.
    detectors.run(QUIET, enabled=("engine-loop", "filler"),
                  terms={"filler": ("嗯",)})


# ------------------------------------------------------------------- INV-2

def test_window_evidence_makes_one_window_finding():
    """INV-2: the unit and the evidence's layer are one fact, enforced by the type.

    Window-level evidence produces ONE window-level finding, never N cue-level
    ones -- the shape that would turn a single decode-metric fact into a hundred
    separate complaints about the text.
    """
    evidence = model.Evidence.of("window", avg_logprob=-1.4)
    ok = model.Finding(id="w-1", span=model.Span("c0", 0, 3), code="probe",
                       severity="warn", unit="window", evidence=evidence,
                       actions=("keep",))
    assert ok.unit == ok.evidence.layer

    with pytest.raises(model.ModelError):
        model.Finding(id="w-2", span=model.Span("c0", 0, 3), code="probe",
                      severity="warn", unit="cue", evidence=evidence,
                      actions=("keep",))


def test_every_shipping_finding_agrees_with_its_own_evidence(decode):
    """The live half of INV-2, over both in-repo decodes and all four detectors."""
    _plan, verdicts = detectors.run(
        decode, enabled=("engine-loop", "speaker-repeat", "filler", "glossary"),
        terms={"filler": ("嗯",), "glossary": ("對",)})
    for v in verdicts:
        if v.finding is not None:
            assert v.finding.unit == v.finding.evidence.layer
            assert v.finding.evidence.values          # numbers, never prose
            assert all(isinstance(x, float) for _k, x in v.finding.evidence.values)


def test_an_engine_loop_finding_says_which_rule_promoted_it(decode):
    """G-6, on the frozen decodes rather than on a constructed case.

    `signals` says how many voted; `stands_alone` says whether INV-6's two-signal
    arithmetic was overruled because the run was long enough on its own (A-25).
    Without the second field, `signals=1` on a finding is indistinguishable from
    a scoring bug -- and on the real 51-minute recording ALL THREE findings score
    1, so this is the ordinary case and not the corner.

    **Measured, and it is worth saying plainly: on both frozen decodes the field
    is 1.0 every time** -- cond=False scores (1, 1) and cond=True scores (2, 1).
    So these fixtures are the positive control and nothing more; a test that
    stopped here would be green on an implementation that hard-coded 1.0. The
    negative control is therefore constructed below, at the layer that decides
    it: a `cue`-kind candidate is an in-cue repeat, is not a full-length run, and
    must report 0.0 whatever else votes for it.
    """
    _plan, verdicts = detectors.run(decode, enabled=("engine-loop",))
    seen = 0
    for v in verdicts:
        if v.finding is None or v.finding.code != "engine-loop":
            continue
        got = dict(v.finding.evidence.values)
        assert "stands_alone" in got, got
        assert got["stands_alone"] == 1.0
        if got["signals"] < 2.0:
            assert got["stands_alone"] == 1.0, (
                "a one-signal finding that did not come through `stands_alone` "
                "is INV-6 being broken, not applied")
        seen += 1
    assert seen, "no engine-loop finding in either frozen decode"

    # The negative control: the other candidate kind, which must read 0.0.
    from ltm.detect import score as scoring
    thr = thresholds.of(decode)
    kinds = {k: scoring.score(decode, 0, 0, k, thr).stands_alone
             for k in ("run", "near-run", "cue")}
    assert kinds == {"run": True, "near-run": False, "cue": False}, kinds


# ------------------------------------------------------- INV-17, T-3, INV-1

def test_absent_gate_marks_unverified(decode):
    """INV-17 / T-3: a gate that cannot run must not pass quietly.

    Both markers are present on every plan, including the one with no findings at
    all. Present-with-zero means "the gate ran and nothing was left ungated";
    ABSENT means "this round never reached that gate", and the two must not share a
    spelling on disk -- a record you cannot tell apart from another is not a record.
    """
    plan, _ = detectors.run(decode)
    for marker in detectors.UNVERIFIED_MARKERS:
        assert marker in plan.summary.unverified
        assert plan.summary.unverified[marker] == 0


def test_engine_health_says_how_language_was_decided():
    """PIM v1.4 section 5.5: `multilingual` is named in the plan's engine health.

    It decides how `language_detected` may be read (E-3(d)), so a plan that drops it
    hides the one condition round 10's worst defect was about. On, off and
    not-reported are three different sentences: the controls are that each one
    survives as itself -- a summary that hard-codes the shipping default passes the
    first and fails the other two.
    """
    for said in (True, False, None):
        rec = dataclasses.replace(
            QUIET, report=dataclasses.replace(QUIET.report, multilingual=said))
        plan, _ = detectors.run(rec)
        assert "multilingual" in plan.summary.engine_health
        assert plan.summary.engine_health["multilingual"] is said, said


def test_a_missing_marker_is_not_a_zero():
    """The known-false control for the rule above, and it is the whole point.

    A summary that never reached the gate raises rather than reporting 0/n, because
    "we checked and found none" is a claim and this round has not earned it.
    """
    plan, _ = detectors.run(LOUD)
    stripped = model.PlanSummary(
        findings=plan.summary.findings, proposals=plan.summary.proposals,
        notes=plan.summary.notes, unverified={},        # never reached the gate
        detectors_skipped=plan.summary.detectors_skipped,
        detectors_failed=plan.summary.detectors_failed,
        engine_health=plan.summary.engine_health)
    blind = model.Plan(run=plan.run, findings=plan.findings, notes=plan.notes,
                       script=plan.script, summary=stripped)
    with pytest.raises(detectors.DetectError):
        detectors.unverified_rate(blind, "no-token-layer")


def test_unverified_zero_states_its_population():
    """INV-1 / T-3: the population is the GATE's subject, and in milestone 1 it is
    empty on both decodes.

    The population is DERIVED from the plan rather than stored beside the count,
    because a second copy of a number is a copy that can drift. **What it is derived
    FROM is the load-bearing half**, and the first version of this test locked in the
    wrong one: it read the population off the plan's proposal count, so the decode
    with a loop in it reported `0/1` -- a sentence a reader takes as "one op needed
    word-level verification and passed". `no-token-layer` guards T-1 trigger (b),
    whose subject is an `advisor:*` edit INSIDE a cue; milestone 1 has no `Advisor`
    (R12) and emits only whole-cue deletes, so the honest number is that **nothing
    was in scope at all** -- which is why `unverified_rate` returns `None` here and
    `unverified_scope` exists to say "0 out of 0" rather than print a bare zero.

    §0's own words about the reading this test used to assert: "marking every
    detector proposal `no-token-layer` would be the opposite error".
    """
    for rec in (LOUD, QUIET):
        plan, _ = detectors.run(rec)
        assert detectors.unverified_scope(plan, "no-token-layer", rec) == (0, 0)
        assert detectors.unverified_rate(plan, "no-token-layer", rec) is None
        assert plan.summary.unverified["no-token-layer"] == 0

    # And the two decodes differ in every way the wrong rule was reading: one has a
    # 27-cue proposal and 27 ops, the other a 6-cue one and 6 ops. The population is
    # 0 on both, so this pair distinguishes the two candidate definitions on real
    # data -- what it needs is a DIFFERENCE in ops, which the wrong rule read as a
    # population; "one of them has none" was never the load-bearing half, and A-25
    # gave the quiet decode a proposal of its own (a six-cue run of `1`).
    loud, _ = detectors.run(LOUD)
    quiet, _ = detectors.run(QUIET)
    assert sum(loud.summary.proposals.values()) == 1 and len(loud.script) == 27
    assert sum(quiet.summary.proposals.values()) == 1 and len(quiet.script) == 6


def test_negative_control_an_intra_cue_advisor_op_moves_the_population():
    """The known-false control the rule above has no way to produce by itself.

    Milestone 1 cannot make the population nonzero -- that is the point of R12 -- so
    a test using only shipping output cannot tell "the population is empty" apart
    from "the population is always empty because the function returns a constant".
    This builds the milestone-2 shape by hand: one `advisor:*` op inside a cue, which
    the gate owns, and one `advisor:*` delete covering a whole cue, which it does not.

    So the number moves to exactly 1, and `unverified_rate` starts printing a
    fraction on its own arithmetic rather than on a constant somebody edits later.
    """
    inside, outside = LOUD.cues[0], LOUD.cues[1]
    assert len(inside.text) > 1 and len(outside.text) > 0, "the control needs text"
    intra = editscript.EditOp(op="replace", span=model.Span(inside.id, 0, 1),
                              text="X", code="probe", origin="advisor:probe@v1")
    whole = editscript.EditOp(
        op="delete", span=model.Span(outside.id, 0, len(outside.text)),
        text=None, code="probe", origin="advisor:probe@v1")

    plan, _ = detectors.run(LOUD)
    probed = model.Plan(run=plan.run, findings=plan.findings, notes=plan.notes,
                        script=editscript.merge(plan.script, (intra, whole)),
                        summary=plan.summary)

    assert detectors.unverified_scope(probed, "no-token-layer", LOUD) == (0, 1)
    rate = detectors.unverified_rate(probed, "no-token-layer", LOUD)
    assert rate is not None and (rate.count, rate.population) == (0, 1)
    assert "0/1" in str(rate)

    # Without a recording the whole-cue op cannot be recognised as whole-cue, so it
    # COUNTS: over-stating how much went unverified is the safe direction for a mark
    # whose whole job is to admit doubt. Asserted rather than left to the docstring.
    assert detectors.unverified_scope(probed, "no-token-layer") == (0, 2)

    # ...and `no-vad` is a separate scope rule, not the same one twice: nothing in
    # milestone 1 consumes `vad_kept`, so these ops move nothing there.
    assert detectors.unverified_scope(probed, "no-vad", LOUD) == (0, 0)
    with pytest.raises(detectors.DetectError):
        detectors.unverified_scope(probed, "no-such-gate", LOUD)


def test_a_candidate_containing_an_empty_cue_fails_loudly():
    """V-5: the op count and the finding's own extent may not disagree in silence.

    `_whole_cue_deletes` used to SKIP a cue with no text, which would leave a
    27-cue finding proposing 26 deletions with nothing to notice it. The candidate
    rule cannot nominate an empty cue today -- S2 needs six characters and S3 needs
    one alphanumeric -- so the reachable-tomorrow case is the one that gets the loud
    failure. The known-false control is the same call on a cue that has text.
    """
    empty = dataclasses.replace(LOUD.cues[0], id="c-empty", text="")
    rec = dataclasses.replace(LOUD, cues=(empty,) + LOUD.cues[:2])
    with pytest.raises(detectors.DetectError) as e:
        detectors._whole_cue_deletes(rec, 0, 2, "probe")
    assert "c-empty" in str(e.value)
    assert len(detectors._whole_cue_deletes(rec, 1, 2, "probe")) == 2


# --------------------------------------------------------------------- T-1

def test_word_level_request_fails_naming_the_absent_layer():
    """T-1 trigger (a) is live in milestone 1 and the layer is absent.

    So the request FAILS, naming the layer. It must not return cue-level output as
    though it had complied: that is the worst of the three outcomes, because the
    caller asked for word-level precision and would be handed something shaped like
    a compliant answer. The known-false control is the same call without the flag.
    """
    with pytest.raises(detectors.DetectError) as e:
        detectors.run(QUIET, word_level=True)
    assert detectors.TOKEN_LAYER in str(e.value)
    plan, _ = detectors.run(QUIET, word_level=False)
    assert plan.summary.unverified["no-token-layer"] == 0


# -------------------------------------------------------------- BR-10, RB-7

def test_a_failed_detector_is_recorded_and_does_not_kill_the_round(monkeypatch):
    """BR-10 on the live path: a plan may carry findings AND a failure.

    The edge `detecting -> proposing` does not ask about detector health, so
    without the second field the failure would have nowhere to land and the run
    would look identical to a clean one.
    """
    thr = thresholds.of(LOUD)
    plan, verdicts = detectors.run(LOUD, thr=thr)
    assert plan.summary.detectors_failed == ()

    # The same run with a broken detector alongside the working one.
    original = detectors.build
    monkeypatch.setattr(
        detectors, "build",
        lambda enabled, terms=None, lex=None:
        original(("engine-loop",), terms, lex) + [Raiser()])
    plan, verdicts = detectors.run(LOUD, thr=thr)
    assert plan.summary.detectors_failed == (("raiser", "exception"),)
    assert plan.summary.findings == {"engine-loop": 1}
    assert not plan.summary.healthy
    assert plan.summary.detectors_skipped != ()      # two fields, not one

    # The reason is a CODE from the PIM's three (§5.5, 「缺輸入、缺可選層、擲出例外」),
    # not a sentence -- and the set is enforced rather than declared, so a fourth
    # reason invented at a failure site is refused (verifier V-6: the tuple existed
    # and nothing consulted it, which is a vocabulary with no authority).
    assert set(detectors.FAILURE_REASONS) == {
        "missing-input", "missing-optional-layer", "exception"}
    for _name, reason in plan.summary.detectors_failed:
        assert reason in detectors.FAILURE_REASONS
    assert detectors.failure("raiser", "missing-input") == ("raiser", "missing-input")
    with pytest.raises(detectors.DetectError):
        detectors.failure("raiser", "the detector was unhappy")


def test_the_detector_interface_advances_once_per_cue():
    """RB-7 with its negative control, checked as an index sequence (rung 1).

    A stopwatch cannot fail here -- 936 cues take milliseconds either way -- so the
    property is the sequence of indices, which a whole-sequence detector cannot
    produce however fast it is.
    """
    thr = thresholds.of(QUIET)
    good = [i for i, _b in detectors.EngineLoop().scan(QUIET, thr)]
    assert good == list(range(len(QUIET.cues)))
    bad = [i for i, _b in WholeSequence().scan(QUIET, thr)]
    assert bad != list(range(len(QUIET.cues)))


def test_detecting_answers_a_cancel_within_one_cue():
    """R13: `detecting` is cancellable, and the poll point is per cue.

    The user's complaint is 「單純反灰然後等待很久」, so a phase that emits progress
    once per cue and polls once per SCAN would answer a cancel only when the whole
    thing finished -- the same defect at a smaller scale. The check is an index
    count rather than a stopwatch, for the reason A-1 gives: 936 cues take
    milliseconds and no timing assertion here can go red.

    Two controls. Known-true: a cancel requested at cue 5 stops the run, and the
    proof that it stopped EARLY is that fewer than `len(cues)` steps were emitted.
    Known-false: the same run with no cancellation goes all the way, so the check
    is not passing because `run` stops early anyway.
    """
    seen: list[int] = []
    cancellation = progress.Cancellation()

    def step(_name, done, _total):
        seen.append(done)
        if done == 5:
            reply = cancellation.request("detecting")
            assert reply.accepted, "R13 puts `detecting` in CANCELLABLE"

    with pytest.raises(progress.Cancelled):
        detectors.run(LOUD, on_step=step, cancel=cancellation)
    assert seen == list(range(1, 6))
    assert len(seen) < len(LOUD.cues)

    # ...and without the request the same run completes, so the stop above is the
    # cancel and not an unrelated early exit.
    # Once per cue PER DETECTOR: the default set has two since 2026-09-27.
    full: list[int] = []
    detectors.run(LOUD, on_step=lambda _n, d, _t: full.append(d),
                  cancel=progress.Cancellation())
    assert len(full) == len(LOUD.cues) * len(detectors.DEFAULT)


def test_run_reports_progress_once_per_cue_per_detector():
    """Where INV-22's `detect` denominator comes from, and it is the CALLER's job.

    `total` is fixed on entry (the cue count is already known), and no detector
    emits progress itself -- so a detector cannot report a denominator the state
    machine did not ask for.
    """
    seen: list[tuple[str, int, int]] = []
    detectors.run(QUIET, enabled=("engine-loop", "speaker-repeat"),
                  on_step=lambda name, done, total: seen.append((name, done, total)))
    n = len(QUIET.cues)
    assert len(seen) == 2 * n
    assert [d for _n, d, _t in seen[:n]] == list(range(1, n + 1))
    assert {t for _n, _d, t in seen} == {n}


# ------------------------------------------------------------ the proposals

def test_a_proposal_deletes_exactly_the_cues_it_names():
    """The anchor cannot silently under-delete.

    A `Span` lives inside ONE cue (INV-5), so a 27-cue run is 27 ops while the
    `Finding` anchors on the first cue and states the extent as a number. Those two
    could disagree, and this is what stops them: the op count equals the evidence's
    `cues`, and every op covers its whole cue.
    """
    plan, verdicts = detectors.run(LOUD)
    proposal = next(v for v in verdicts if v.proposes_deletion)
    extent = proposal.finding.evidence.as_dict()["cues"]
    assert len(proposal.ops) == extent == 27
    by_id = {c.id: c for c in LOUD.cues}
    for op in proposal.ops:
        cue = by_id[op.span.cue_id]
        assert (op.span.char_start, op.span.char_end) == (0, len(cue.text))
        assert op.op == "delete" and op.text is None
        assert op.origin == "detector:engine-loop"


def test_the_script_a_detector_proposes_is_provably_revertible():
    """INV-4 reaches into this step: what detection proposes must be provable.

    Nothing is written here -- `prove` runs in memory -- but a detector that
    proposed an unprovable script would push the failure to `applying`, where the
    only honest answer left is `failed(proof)`. Catching it at the source is
    cheaper and names the right module.
    """
    plan, _ = detectors.run(LOUD)
    source = {c.id: c.text for c in LOUD.cues}
    assert editscript.prove(source, plan.script)
    applied, _journal = editscript.apply(source, plan.script)
    assert sum(1 for k, v in applied.items() if v != source[k]) == 27


def test_a_dropped_candidate_is_still_a_verdict():
    """RC-1: "judged and rejected" and "never considered" stay distinguishable.

    idx 488 on the shipping default has a repeat unit, misses the S1 sub-gate, sits
    in a clean neighbourhood and never left temperature 0.0. It appears in no
    finding and no note -- and it must still be visible as a decision, or the
    output cannot be told from one where the candidate rule never ran.
    """
    plan, verdicts = detectors.run(QUIET)
    dropped = [v for v in verdicts if v.disposition == "dropped"]
    assert [v.span for v in dropped] == [(488, 488)]
    assert all(v.signals == 0 for v in dropped)
    # One of each, which is what makes "dropped" a verdict rather than a synonym
    # for "nothing happened" -- this decode also has a finding and a note (A-25
    # moved 314-319 into the first of those).
    assert len(plan.findings) == 1 and len(plan.notes) == 1
    # ...and the drop reaches no on-disk record, which is why it is returned
    # beside the plan rather than stored in it.
    assert not any(n.id.endswith("488-488") for n in plan.notes)
