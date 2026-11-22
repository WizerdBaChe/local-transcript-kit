"""The rules the types carry. PSM section 4, section 10's invariant map.

Every test here names its invariant, and every invariant in the map has a test.
A rule enforced in `__post_init__` is worth a test for one reason: the test is
what proves the enforcement can actually FAIL.
"""
import pytest

from ltm.core.model import (
    LAYERS,
    NO_MAJORITY,
    Cue,
    EngineReport,
    Evidence,
    Finding,
    ModelError,
    Note,
    PlanSummary,
    Rate,
    Recording,
    Revision,
    Span,
    Stretch,
    Window,
    undetermined_stretches,
)


def a_report(**kw):
    base = dict(vad_kept=None, vad_enabled=False,
                condition_on_previous_text=False, multilingual=True,
                degenerated=False)
    base.update(kw)
    return EngineReport(**base)


def a_recording(n_cues=3, n_windows=2, stretches=None):
    stretches = stretches or (Stretch("s0", 0.0, 30.0, "zh", 1.0),)
    windows = tuple(Window(f"w{i}", i * 15.0, (i + 1) * 15.0,
                           -0.3, 0.0, 1.2, 0.01) for i in range(n_windows))
    cues = tuple(Cue(f"c{i}", i * 2.0, i * 2.0 + 2.0, "text",
                     windows[i % len(windows)].id, stretches[0].id)
                 for i in range(n_cues))
    return Recording("run", "a.aac", 30.0, "0" * 64, a_report(),
                     stretches, windows, cues)


# --- INV-1: a ratio must name its population ------------------------------

def test_ratio_requires_population():
    rec = a_recording(n_cues=4, n_windows=2)
    assert rec.rate_over_cues(1).value == 0.25
    assert str(rec.rate_over_cues(1)) == "1/4 cue (0.2500)"
    # The point of the type: there is no way to build a rate without saying
    # which layer it was counted over, and an unknown layer is refused.
    with pytest.raises(ModelError):
        Rate(1, 4, "token")          # milestone 2's layer, absent on purpose
    with pytest.raises(ModelError):
        Rate(1, 0, "cue")            # an empty population is not a population
    with pytest.raises(ModelError):
        Rate(5, 4, "cue")


def test_every_cue_knows_its_window_and_stretch():
    with pytest.raises(ModelError):
        Cue("c0", 0.0, 1.0, "t", "", "s0")
    with pytest.raises(ModelError):
        Cue("c0", 0.0, 1.0, "t", "w0", "")


def test_all_three_layers_are_reachable_as_populations():
    rec = a_recording()
    assert {r.layer for r in (rec.rate_over_cues(1), rec.rate_over_windows(1),
                              rec.rate_over_stretches(1))} == set(LAYERS)


# --- INV-2: the finding's unit is the evidence's layer ---------------------

def test_window_evidence_makes_one_window_finding():
    ev = Evidence.of("window", compression_ratio=2.21, temperature=0.4)
    ok = Finding("f0", Span("c0", 0, 3), "engine-loop", "high", "window", ev,
                 ("delete",))
    assert ok.unit == ev.layer
    with pytest.raises(ModelError):
        Finding("f1", Span("c0", 0, 3), "engine-loop", "high", "cue", ev,
                ("delete",))


def test_evidence_carries_numbers_and_never_prose():
    ev = Evidence.of("cue", n_signals=2)
    assert ev.as_dict() == {"n_signals": 2}
    with pytest.raises(ModelError):
        Evidence("cue", ())          # evidence with no numbers is not evidence
    with pytest.raises(ModelError):
        Evidence.of("token", n=1)


# --- I-3: a finding carries an action; otherwise it is a Note --------------

def test_finding_without_action_is_not_a_finding():
    ev = Evidence.of("cue", n_signals=1)
    with pytest.raises(ModelError):
        Finding("f0", Span("c0", 0, 3), "engine-loop", "info", "cue", ev, ())
    # The alternative is a different TYPE, so a careless caller cannot smuggle
    # an actionless finding through as a weak one.
    note = Note("n0", Span("c0", 0, 3), "engine-loop", "single-signal")
    assert not hasattr(note, "actions")


# --- BR-4: agreement and language are two spellings of one fact -----------

def test_below_half_is_undetermined():
    assert Stretch("s0", 0.0, 30.0, "undetermined", 0.0).language == "undetermined"
    assert Stretch("s1", 0.0, 30.0, "zh", 1.0).agreement == 1.0
    # Exactly at the threshold is a MAJORITY, so it is not undetermined: 0.5 is
    # defined as "no label has majority support", and 0.5 support is a majority
    # of two signals. The boundary is asserted so a later refactor cannot slide
    # it silently.
    assert Stretch("s2", 0.0, 30.0, "zh", NO_MAJORITY).language == "zh"


def test_agreement_and_language_cannot_disagree():
    with pytest.raises(ModelError):
        Stretch("s0", 0.0, 30.0, "zh", 0.2)            # low but labelled
    with pytest.raises(ModelError):
        Stretch("s1", 0.0, 30.0, "undetermined", 0.9)  # labelled but confident
    with pytest.raises(ModelError):
        Stretch("s2", 0.0, 30.0, "zh", 1.4)


def test_undetermined_stretches_are_reportable():
    stretches = (Stretch("s0", 0.0, 10.0, "zh", 1.0),
                 Stretch("s1", 10.0, 20.0, "undetermined", 0.0))
    rec = a_recording(stretches=stretches)
    assert [s.id for s in undetermined_stretches(rec)] == ["s1"]


# --- E-1 / E-2: the engine contract ---------------------------------------

def test_condition_flag_distinguishes_absent_from_false():
    assert a_report(condition_on_previous_text=None).condition_on_previous_text is None
    assert a_report(condition_on_previous_text=False).condition_on_previous_text is False


def test_vad_kept_contract_three_branches():
    """E-2's three branches, and the fourth cell the field grid also has.

    A previous version of this test called `vad_enabled=True, vad_kept=None`
    "not reported at all" -- it is not: that cell is the engine saying VAD ran and
    then saying nothing about how much it kept. **"Not reported" is
    `vad_enabled is None`**, which the field could not even express until
    `vad_enabled` became `bool | None` (PIM section 11: this field is what
    distinguishes 「沒回報」 from 「那一次沒跑」). Naming three branches while
    covering two semantics is how a contract passes a test it does not satisfy.
    """
    # (a) enabled, and it said how much -- the only trustworthy cell
    a = a_report(vad_enabled=True, vad_kept=2100.0)
    assert a.vad_kept_is_trustworthy() and a.vad_verdict() == "trustworthy"
    # (b) it ran without VAD: the number may exist and still means nothing --
    #     the measured case was 10.00 s LONGER than the audio on a --no-vad run
    b = a_report(vad_enabled=False, vad_kept=2193.68)
    assert not b.vad_kept_is_trustworthy() and b.vad_verdict() == "did-not-run"
    # (c) the engine said nothing about VAD: not the same fact as (b)
    c = a_report(vad_enabled=None, vad_kept=None)
    assert not c.vad_kept_is_trustworthy() and c.vad_verdict() == "not-reported"
    # the fourth cell: enabled, no number. Also unusable, and distinct from (c)
    d = a_report(vad_enabled=True, vad_kept=None)
    assert not d.vad_kept_is_trustworthy()
    assert d.vad_verdict() == "enabled-but-silent" != c.vad_verdict()


def test_multilingual_distinguishes_absent_from_false():
    """E-3(a)/(c): a plan written before E-3 says nothing, and that is not `False`."""
    assert a_report(multilingual=None).multilingual is None
    assert a_report(multilingual=False).multilingual is False


def test_engine_report_has_exactly_five_fields():
    # The three deleted fields must not come back by accident, and a sixth
    # field is a spec change, not an implementation detail. The fifth was one:
    # PIM v1.2/v1.3 section 2.1 added `multilingual` (E-3(c)).
    assert EngineReport.__slots__ == (
        "vad_kept", "vad_enabled", "condition_on_previous_text", "multilingual",
        "degenerated")


# --- the plan summary, and the two detector-health fields (RE-3) ----------

def a_summary(**kw):
    base = dict(findings={}, proposals={}, notes={}, unverified={},
                detectors_skipped=(), detectors_failed=(), engine_health={})
    base.update(kw)
    return PlanSummary(**base)


def test_skipped_and_failed_are_separate_fields():
    s = a_summary(detectors_skipped=("filler",),
                  detectors_failed=(("glossary", "no list declared"),))
    assert s.detectors_skipped == ("filler",)
    assert s.detectors_failed == (("glossary", "no list declared"),)
    assert not s.healthy
    assert a_summary(detectors_skipped=("filler",)).healthy


def test_absence_and_zero_are_different_records():
    ran_and_found_none = a_summary(unverified={"no-vad": 0})
    never_reached = a_summary(unverified={})
    assert ran_and_found_none.unverified != never_reached.unverified
    assert "no-vad" in ran_and_found_none.unverified
    assert "no-vad" not in never_reached.unverified


# --- the revision chain ---------------------------------------------------

def test_revision_chain_is_explicit():
    assert Revision("r000", None, "decode").serial == "r000"
    assert Revision("r001", "r000", "edit").source == "r000"
    assert Revision("r002", "r001", "declined").kind == "declined"
    with pytest.raises(ModelError):
        Revision("r001", "r000", "decode")     # a decode has no source
    with pytest.raises(ModelError):
        Revision("r001", None, "edit")         # an edit must say what from
    with pytest.raises(ModelError):
        Revision("1", None, "decode")


def test_span_cannot_be_negative():
    assert len(Span("c0", 2, 5)) == 3
    with pytest.raises(ModelError):
        Span("c0", 5, 2)
    with pytest.raises(ModelError):
        Span("c0", -1, 2)
