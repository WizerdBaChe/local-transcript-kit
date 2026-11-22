"""The verbs end to end, through the stub sidecar and a real store on disk.

PSM §9. `test_agentapi_schema.py` holds the two rules ABOUT the face -- INV-12 and
the one-verb-set identity. This file runs the face: intake, decode, detect, plan,
accept, cite, and the refusals each of them owes.

The engine is the stub from `tests/fixtures/stub_sidecar.py`, so these assertions
are deterministic and fast; `test_engine.py::test_one_real_decode_through_the_real_sidecar`
is what says the real one works. The store is a `tmp_path`, so nothing here can
reach the user's own output root.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest

from fixtures import stub_decode
from ltm.agentapi import schema, verbs
from ltm.core import model, progress
from ltm.detect import detectors, score, thresholds
from ltm.engine import contract
from ltm.store import write
from ltm.store import layout, write

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STUB = os.path.join(HERE, "tests", "fixtures", "stub_sidecar.py")


@pytest.fixture()
def root(tmp_path):
    return tmp_path / "TranscriptDesk"


@pytest.fixture()
def audio(tmp_path):
    p = tmp_path / "訪談 2026.m4a"          # CJK and a space, on purpose
    p.write_bytes(b"not really audio; the stub never opens it")
    return p


@pytest.fixture()
def weights(tmp_path, monkeypatch):
    d = stub_decode.fake_weights(tmp_path / "weights")
    monkeypatch.setenv(contract.MODEL_DIR_VAR, str(d))
    monkeypatch.setenv(contract.SIDECAR_PYTHON_VAR, sys.executable)
    return d


@pytest.fixture()
def stub(monkeypatch):
    monkeypatch.setenv("LTM_STUB", json.dumps(stub_decode.DECODE))


def decoded(root, audio, *, on_progress=None, cancellation=None, **over):
    """intake + decode, the two acts every other verb needs to have happened."""
    ctx = verbs.Context(root=root, on_progress=on_progress,
                        cancellation=cancellation)
    got = verbs.intake(ctx, path=str(audio))
    args = dict(run=got.run, duration_seconds=stub_decode.DURATION,
                python=None, script=STUB)
    args.update(over)
    return ctx, got.run, verbs.decode(ctx, **args)


# --- intake ---------------------------------------------------------------

def test_intake_hashes_content_and_copies_nothing(root, audio):
    """R10's 「ordinary door」, and the media stays where the user put it.

    A 53 MB copy per run is a cost nobody asked for; `_intake/` holds a pointer.
    The hash is of the CONTENT (§7), so the same talk under another name resolves
    to the same run -- which is the property a filename cannot give.
    """
    ctx = verbs.Context(root=root)
    first = verbs.intake(ctx, path=str(audio))
    assert first.run.endswith(first.sha256[:8])
    assert first.bytes == float(audio.stat().st_size)

    assert first.recognised == 0.0 and first.high_water == -1.0

    # I-19 / §9.2: identity is the hash and ONLY the hash. The same audio under
    # another name is the same run -- the spec has said so since v0.1 and the
    # implementation did the opposite until 2026-09-11, splitting thirteen
    # revisions of a real recording across two folders.
    twin = audio.parent / "another name.m4a"
    twin.write_bytes(audio.read_bytes())
    second = verbs.intake(ctx, path=str(twin))
    assert second.sha256 == first.sha256
    assert second.run == first.run, "same content, so the same run"
    assert second.recognised == 1.0, (
        "recognising it silently is the other half of the defect: the user has "
        "to be told that the work they already did is what they just re-entered")

    # The other direction, and it is the half a filename cannot give: two
    # different recordings that happen to share a name must NOT collide.
    namesake = audio.parent / "elsewhere" / audio.name
    namesake.parent.mkdir()
    namesake.write_bytes(audio.read_bytes() + b" different content")
    third = verbs.intake(ctx, path=str(namesake))
    assert third.sha256 != first.sha256 and third.run != first.run
    assert third.recognised == 0.0

    # Nothing under the root is a copy of the media. Checked by CONTENT and not by
    # size: this fixture's audio is 41 bytes, so a size comparison would pass on a
    # root that did hold a copy -- the instrument has to work on the fixture it is
    # given, not only on the 53 MB file the rule is about.
    media = audio.read_bytes()
    for p in root.rglob("*"):
        if p.is_file():
            assert p.read_bytes() != media, p
    # ...and the pointer record names where the file actually is. `second` handed
    # the same audio over from a different path, and the pointer followed it:
    # `_intake/` answers 「where is this media NOW」, and the most recent intake is
    # the most recent answer. The first path is not kept as a second candidate --
    # a pointer with two values is a pointer that cannot be followed.
    marker = json.loads((root / layout.INTAKE / f"{first.run}.json").read_text("utf-8"))
    assert marker["source"] == str(twin.resolve())
    assert marker["run"] == first.run

    with pytest.raises(schema.AgentError) as e:
        verbs.intake(ctx, path=str(audio.parent / "not-here.m4a"))
    assert e.value.code == "bad-argument"


# --- decode ---------------------------------------------------------------

def test_decode_writes_nothing_beside_the_audio(root, audio, weights, stub):
    """Rung 0. The person's folder is read, never written (the media stays where
    the user put it, and so does everything around it).

    Until 2026-10-14 every decode left `<audio>.engine.log` next to the recording
    -- zero bytes on a clean run -- because the runner's stderr default was derived
    from the AUDIO path and the verb never passed its own. Found when a decode of a
    recording sitting in another person's inbox left a file in that inbox; the
    September corpus notes had already seen three of them and filed them as
    「not part of the corpus」, which described the symptom and left the cause.
    Control, by value: before the fix `after` held one extra name,
    `訪談 2026.engine.log`. (The fixture's root sits in the same temporary folder
    as the audio, so the root's own name is left out of both listings.)
    """
    def beside():
        return sorted(p.name for p in audio.parent.iterdir() if p != root)
    before = beside()
    _, run, _ = decoded(root, audio)
    after = beside()
    assert after == before
    log = layout.engine_log(root, run)
    assert log.is_file() and log.is_relative_to(root)


def test_decode_places_a_round_and_records_its_provenance(root, audio, weights,
                                                          stub):
    """The four layers, the member set, and E-1's audit trail on disk."""
    ctx, run, got = decoded(root, audio)
    assert got.rev == "r000"
    assert got.counts["cues"] == 12.0
    assert got.counts["slots"] == 3.0
    assert got.counts["stretches"] >= 1.0

    record = write.read_origin(root, run)
    provenance = write.decode_record(record, got.rev)
    assert provenance["condition_on_previous_text"] is False
    assert provenance["language_slots"] is True
    assert record["high_water"] == 0
    members = sorted(p.name.split(".")[-1] for p in got.paths and
                     [__import__("pathlib").Path(p) for p in got.paths])
    assert members == ["json", "json", "srt", "txt"]


def test_a_silent_recording_is_a_round_with_no_words_not_an_engine_failure(
        root, audio, weights, monkeypatch):
    """I-22. VAD on over audio with nobody speaking: the engine writes nothing.

    Measured on three M1 clips (`docs/m1-silence-probe-2026-09-27.md`), and until
    2026-09-27 each came back `engine-failed: no-segments` -- a correct answer
    reported as a crash. Now it is a round: zero cues, one `undetermined` stretch,
    an empty plan, and `health["vad_kept"] == 0.0` saying WHY there are no words.

    **Negative control** beside it: the same silence with VAD off, where
    `vad_kept` is meaningless (E-2(a)) and must come back -1.0 rather than the
    150.0 the engine fills in -- otherwise a caller would read "VAD kept the whole
    file" off a decode in which VAD never ran.
    """
    from ltm.ui import cli
    silent = {"segments": 0, "gap": 0.01, "vad_kept": 0.0,
              "slot_langs": ["en", "nn", "en"]}
    monkeypatch.setenv("LTM_STUB", json.dumps(silent))
    ctx, run, got = decoded(root, audio)
    assert got.counts["cues"] == 0.0 and got.counts["windows"] == 0.0
    assert got.counts["stretches"] == 1.0
    assert got.health["vad_enabled"] == 1.0
    assert got.health["vad_kept"] == 0.0
    assert got.duration == stub_decode.DURATION
    assert got.ops == 0.0 and not got.findings and not got.detectors_failed
    shown = verbs.show(ctx, run=run)
    assert [str(x) for x in shown.languages] == ["undetermined"]
    # `detect` over the empty round answers too, rather than refusing a recording
    # with no thresholds.
    again = verbs.detect(ctx, run=run, rev=got.rev)
    assert not again.findings and not again.detectors_failed
    assert "found no speech" in cli.render(got)

    monkeypatch.setenv("LTM_STUB", json.dumps({**silent, "vad_kept": 90.0}))
    _, _, off = decoded(root, audio, vad=False)
    assert off.counts["cues"] == 0.0
    assert off.health["vad_enabled"] == 0.0
    assert off.health["vad_kept"] == -1.0
    said = cli.render(off)
    assert "did not run" in said and "found no speech" not in said


def test_a_long_cue_with_few_words_reaches_the_reader_with_what_it_means(
        root, audio, weights, monkeypatch):
    """I-23 through the shipped path: `decode` runs `sparse-cue` by default.

    Two 20-second cues holding one and two characters -- the shape M1 measured when
    VAD stitched a real sentence across a pause. Both must come back as findings,
    neither as a proposed edit (the words may be right; the time is what is
    wrong), and the CLI must say what the finding MEANS, because
    `seconds=20, units=1` alone reads as a slow speaker. **Known-false control**:
    the standard decode (7.5 s cues, full sentences) produces no `sparse-cue`.
    """
    from ltm.ui import cli
    monkeypatch.setenv("LTM_STUB", json.dumps(
        {"segments": 2, "gap": 0.01, "seg_seconds": 20.0,
         "texts": ["嗯", "對對"], "slot_langs": ["zh", "zh", "zh"]}))
    ctx, run, got = decoded(root, audio)
    assert got.findings.get("sparse-cue") == 2.0
    assert "sparse-cue" not in got.proposals
    shown = cli.describe_plan(verbs.plan(ctx, run=run, rev=got.rev), root)
    assert sum(cli.FINDING_SENTENCE["sparse-cue"] in line
               for line in shown) == 2

    monkeypatch.setenv("LTM_STUB", json.dumps(stub_decode.DECODE))
    _, _, plain = decoded(root, audio)
    assert "sparse-cue" not in plain.findings


def test_an_empty_decode_has_no_failure_code_to_fall_back_on():
    """I-22's other half: `no-segments` is out of the vocabulary, not just unused.

    A code left in `CODES` after nothing produces it is a word every surface still
    has to translate, and the next person to meet an empty decode would reach for
    it. **Positive control**: a code still in the set builds.
    """
    with pytest.raises(contract.ContractError):
        contract.EngineFailure(code="no-segments")
    assert contract.EngineFailure(code="no-report").code == "no-report"


def test_multilingual_is_the_default_and_every_record_says_so(root, audio, weights,
                                                              stub):
    """E-3(b)/(c): on by default, in `_origin.json`, in the plan, in the response.

    Both arms, because one arm is not a measurement: a field hard-wired to `True`
    (or a `health` entry hard-wired to 1.0) passes every assertion below that only
    ever decodes with the default. So the same three carriers are read again with
    the flag OFF, and the two readings must differ -- that difference is the whole
    claim. Round 12, R12b-S7.
    """
    ctx, run, got = decoded(root, audio)
    record = write.read_origin(root, run)
    assert write.decode_record(record, got.rev)["multilingual"] is True
    plan = json.loads((root / layout.STORE / run / "_plans" / f"{got.rev}.json")
                      .read_text("utf-8"))
    assert plan["recording"]["report"]["multilingual"] is True
    assert got.health["multilingual"] == 1.0

    off = verbs.decode(ctx, run=run, multilingual=False, script=STUB,
                       duration_seconds=stub_decode.DURATION)
    assert off.rev != got.rev
    record = write.read_origin(root, run)
    assert write.decode_record(record, off.rev)["multilingual"] is False
    plan_off = json.loads((root / layout.STORE / run / "_plans" / f"{off.rev}.json")
                          .read_text("utf-8"))
    assert plan_off["recording"]["report"]["multilingual"] is False
    assert off.health["multilingual"] == 0.0
    # The revision that ran first is not rewritten by the one that ran second: a
    # per-run field rather than a per-revision one would make both rounds say the
    # same thing, and the earlier round's number would change meaning silently.
    assert write.decode_record(record, got.rev)["multilingual"] is True


def test_a_plan_written_before_e3_still_loads_and_says_not_reported(root, audio,
                                                                     weights, stub):
    """PSM 17.0 (3): the key is absent from every older plan, and that is `None`.

    The control is the same record with the key present: it must read back as
    written, so a reader that returned `None` for everything could not pass.
    """
    ctx, run, got = decoded(root, audio)
    plan = json.loads((root / layout.STORE / run / "_plans" / f"{got.rev}.json")
                      .read_text("utf-8"))
    assert verbs._recording_of(plan).report.multilingual is True
    del plan["recording"]["report"]["multilingual"]
    assert verbs._recording_of(plan).report.multilingual is None

    # And the three states stay three on the way OUT. `health` is typed `float`,
    # so "not reported" has to be encoded rather than carried; if `None` encoded to
    # the same number as `False`, an agent reading an older round would be told the
    # decode ran monolingual when nobody ever measured it. Round 12, R12b-S7.
    assert [verbs._as_number(v) for v in (True, False, None)] == [1.0, 0.0, -1.0]


def test_a_pinned_language_with_multilingual_is_refused_before_anything_starts(
        root, audio, weights, stub, tmp_path):
    """E-3(f) at the verb, and the ORDER is what is being tested.

    The interpreter handed in does not exist. A refusal raised after the spawn
    would surface as `engine-failed` / `interpreter-missing`; getting
    `bad-argument` therefore proves the check ran first. The positive half is the
    same pin with the flag off, which must decode and record both settings.
    """
    ctx = verbs.Context(root=root)
    run = verbs.intake(ctx, path=str(audio)).run
    with pytest.raises(schema.AgentError) as e:
        verbs.decode(ctx, run=run, language="zh",
                     python=str(tmp_path / "no-such-python.exe"), script=STUB,
                     duration_seconds=stub_decode.DURATION)
    assert (e.value.code, e.value.subject) == ("bad-argument",
                                               "language-with-multilingual")
    assert write.decode_record(write.read_origin(root, run), "r000") == {}

    with pytest.raises(schema.AgentError) as spawned:
        verbs.decode(ctx, run=run, multilingual=False,
                     python=str(tmp_path / "no-such-python.exe"), script=STUB,
                     duration_seconds=stub_decode.DURATION)
    assert spawned.value.code == "engine-failed", (
        "the control: with a legal combination the same missing interpreter must "
        "surface as an engine failure, or the order assertion above proves nothing")

    ok = verbs.decode(ctx, run=run, language="zh", multilingual=False, script=STUB,
                      duration_seconds=stub_decode.DURATION)
    provenance = write.decode_record(write.read_origin(root, run), ok.rev)
    assert provenance["language_asked"] == "zh"
    assert provenance["multilingual"] is False


def test_the_configuration_the_evidence_was_measured_with_is_reachable(root, audio,
                                                                       weights, stub):
    """Step 7's finding (a), as a test rather than a paragraph.

    §6.3's cue counts came from `language="zh", vad_filter=False`. The shipping
    default is neither, and until this test existed the CLI had no way to ask for
    the configuration the product's own evidence was made with -- so the numbers in
    the document could not be reproduced through the product. Two decodes, two
    revisions, two different recorded configurations, and `show` reads each back as
    ITS OWN (the per-revision repair, from the other side).
    """
    ctx, run, first = decoded(root, audio)
    second = verbs.decode(ctx, run=run, language="zh", vad=False,
                          multilingual=False,
                          duration_seconds=stub_decode.DURATION, script=STUB)
    assert (first.rev, second.rev) == ("r000", "r001")

    record = write.read_origin(root, run)
    default = write.decode_record(record, "r000")
    pinned = write.decode_record(record, "r001")
    assert default["language_asked"] is None and default["vad"] is True
    assert pinned["language_asked"] == "zh" and pinned["vad"] is False
    # The pin crossed the wire: the child reported back the language it was given.
    assert pinned["language_detected"] == "zh"

    # E-3: the default decode ran per-window detection, the pinned one did not,
    # and each revision says which.
    assert default["multilingual"] is True and pinned["multilingual"] is False

    # And `show` does not hand one revision the other's settings.
    assert verbs.show(ctx, run=run, rev="r000").counts["cues"] == 12.0
    assert verbs.show(ctx, run=run, rev="r001").counts["cues"] == 12.0


def test_the_record_fingerprints_the_transcript_not_just_its_size(root, audio,
                                                                  weights, stub):
    """I-1's instrument: a decode is not reproducible, so the store says WHICH one.

    Measured on the real recording: seven decodes with identical arguments produced
    seven distinct transcripts (661..728 cues), two proposing deletions and five
    proposing none. A cue count cannot tell those apart, and the control below is
    the case that proves it: two cue lists of the same total length with the
    boundary moved, which the count cannot separate and the fingerprint must.
    """
    ctx, run, first = decoded(root, audio)
    again = verbs.decode(ctx, run=run, duration_seconds=stub_decode.DURATION,
                         script=STUB)
    record = write.read_origin(root, run)
    a = write.decode_record(record, first.rev)["text_sha256"]
    b = write.decode_record(record, again.rev)["text_sha256"]
    assert a == b, "the stub is deterministic, so these must agree"
    assert len(a) == 64

    same_length = verbs._text_digest(
        [model.Cue(id="c000", start=0.0, end=1.0, text="ab",
                   window_id="w000", stretch_id="st000"),
         model.Cue(id="c001", start=1.0, end=2.0, text="c",
                   window_id="w000", stretch_id="st000")])
    moved_boundary = verbs._text_digest(
        [model.Cue(id="c000", start=0.0, end=1.0, text="a",
                   window_id="w000", stretch_id="st000"),
         model.Cue(id="c001", start=1.0, end=2.0, text="bc",
                   window_id="w000", stretch_id="st000")])
    assert same_length != moved_boundary, (
        "where the boundaries fall is what S3 counts, so it is part of the "
        "fingerprint")


def test_a_language_that_is_not_a_code_is_refused(root, audio, weights, stub):
    """INV-12's spirit on the way IN: `Chinese` and `zh` are the same decode that
    cannot be compared afterwards, so only the code form is accepted."""
    ctx, run, _ = decoded(root, audio)
    with pytest.raises(schema.AgentError) as e:
        verbs.decode(ctx, run=run, language="Chinese", script=STUB)
    assert (e.value.code, e.value.subject) == ("bad-argument", "language")
    # ...and the control: a three-letter code is a code (`yue`), not an overrun.
    verbs.decode(ctx, run=run, language="yue", multilingual=False,
                 duration_seconds=stub_decode.DURATION, script=STUB)


def test_the_arithmetic_is_askable_and_a_typo_in_it_is_refused(root, audio,
                                                               weights, stub):
    """`float16` is a hard failure without a GPU, so it may not be the only option.

    Observed on this machine: the CUDA device stopped being visible mid-session and
    every decode came back `crashed`, because `float16` is the default and CPU
    backends refuse it outright. The product had no way to be asked for anything
    else, so a machine without a working GPU could not run it at all.

    Both values travel to another interpreter, where a typo would come back as
    `crashed` -- a code that says the child died and nothing about why -- so both
    are closed sets checked here, before the child is spawned.
    """
    ctx, run, _ = decoded(root, audio)
    got = verbs.decode(ctx, run=run, compute_type="int8", device="cpu",
                       duration_seconds=stub_decode.DURATION, script=STUB)
    provenance = write.decode_record(write.read_origin(root, run), got.rev)
    assert (provenance["compute_type"], provenance["device"]) == ("int8", "cpu")

    for bad, subject in ((dict(compute_type="float8"), "compute-type"),
                         (dict(device="gpu"), "device")):
        with pytest.raises(schema.AgentError) as e:
            verbs.decode(ctx, run=run, script=STUB, **bad)
        assert (e.value.code, e.value.subject) == ("bad-argument", subject)


def test_the_overshoot_reaches_the_record_and_a_clean_decode_records_zero(
        root, audio, weights, monkeypatch):
    """A-23's other half: `test_engine.py` proves the runner produces the number.

    This proves the number arrives somewhere a reader can find it. It is a
    property of ONE decode -- the next one of the same file may have none -- so
    it belongs beside the settings in `_origin.json`, per revision, and a value
    that never left `EngineResult` would have been reported once and lost.

    Both halves in one test, deliberately: `0.0` written for a clean decode is
    what makes a non-zero value mean something. A field that only ever appears
    when it is interesting cannot be told from a field that is sometimes missing.
    """
    over = dict(stub_decode.DECODE, past_end=0.44)
    monkeypatch.setenv("LTM_STUB", json.dumps(over))
    ctx, run, first = decoded(root, audio)
    record = write.read_origin(root, run)
    assert write.decode_record(record, first.rev)["timestamps_past_end"] == 0.44

    monkeypatch.setenv("LTM_STUB", json.dumps(stub_decode.DECODE))
    clean = verbs.decode(ctx, run=run, duration_seconds=stub_decode.DURATION,
                         python=None, script=STUB)
    record = write.read_origin(root, run)
    assert write.decode_record(record, clean.rev)["timestamps_past_end"] == 0.0


def test_a_decode_without_the_second_signal_says_undetermined(root, audio,
                                                              weights, stub):
    """BR-4, and it is the honest answer rather than a label backed by one signal.

    With `language_slots=False` there is no audio side, so no `agreement` can be
    computed -- and this product does not invent one. One stretch, `undetermined`,
    0.0. The known-true control is the run above, which has three slots and gets
    real stretches.
    """
    ctx, run, got = decoded(root, audio, language_slots=False)
    assert got.counts["slots"] == 0.0
    assert got.counts["stretches"] == 1.0
    shown = verbs.show(ctx, run=run)
    assert [str(x) for x in shown.languages] == ["undetermined"]
    assert shown.stretches[0]["agreement"] == 0.0


def test_decode_emits_progress_a_person_could_watch(root, audio, weights, stub):
    """INV-22 on the verb, not just on the runner: every phase, one second apart.

    The `detect` phase is included, and it is the one this verb adds: `decode` runs
    the detectors before it writes, so a round that finds a 27-cue loop must not go
    quiet between the last slot and the finished plan.
    """
    seen: list[progress.ProgressEvent] = []
    ctx, _run, _got = decoded(root, audio, on_progress=seen.append)
    phases = [e.phase for e in seen]
    assert "decode" in phases and "language-slots" in phases and "detect" in phases
    for e in seen:
        if e.phase in progress.NO_DENOMINATOR:
            assert e.total is None
        else:
            assert e.total is not None and e.done is not None
            assert e.unit == progress.PHASE_UNITS[e.phase]
    # Once per cue per default detector, each counting its own 1..12 under its
    # own name -- so a surface can tell the second pass from a bar going backwards.
    detect_steps = [e for e in seen if e.phase == "detect"]
    for name in detectors.DEFAULT:
        assert [e.done for e in detect_steps if e.name == name] == [
            float(i) for i in range(1, 13)]
    assert {e.name for e in detect_steps} == set(detectors.DEFAULT)


# --- detect ---------------------------------------------------------------

def test_detect_reruns_over_the_measured_layers_and_places_a_new_round(
        root, audio, weights, stub):
    """A re-detect is a round: it consumes a serial and writes finding + plan.

    No transcript in the member set -- the text did not change -- and the plan it
    reads back carries all four layers, which is what lets S4 vote at all. The
    known-false control is the one below: a revision with no plan record.
    """
    ctx, run, first = decoded(root, audio)
    again = verbs.detect(ctx, run=run, rev=first.rev)
    assert again.rev == "r001"
    kinds = sorted(p.rsplit(".", 1)[-1] for p in again.paths)
    assert kinds == ["json", "json"], "a re-detect places no transcript"
    assert again.unverified == {"no-token-layer": 0.0, "no-vad": 0.0}
    assert set(str(d) for d in again.detectors_skipped) == set(detectors.UNCONTROLLED)

    # The plan record round-trips: the same recording, so the same verdicts.
    third = verbs.detect(ctx, run=run, rev=again.rev)
    assert third.findings == again.findings
    assert third.notes == again.notes

    with pytest.raises(schema.AgentError) as e:
        verbs.detect(ctx, run=run, rev="r099")
    assert e.value.code == "no-such-revision"


def test_detect_refuses_word_level_naming_the_absent_layer(root, audio, weights,
                                                           stub):
    """T-1 (a) through the agent face: the code is `token-layer-absent`.

    Its own code, not `bad-argument`: "this build has no token layer" and "your
    arguments do not fit" are different answers, and a caller that got the second
    would reasonably retry with different arguments forever.
    """
    ctx, run, first = decoded(root, audio)
    with pytest.raises(schema.AgentError) as e:
        verbs.call(ctx, "detect", run=run, rev=first.rev, word_level=True)
    assert e.value.code == "token-layer-absent"

    # INV-7 through the same door: a declared list may not bound `engine-loop`.
    with pytest.raises(schema.AgentError) as e2:
        verbs.call(ctx, "detect", run=run, rev=first.rev,
                   terms={"engine-loop": ("對",)})
    assert e2.value.code == "bad-argument"


def test_detect_answers_a_cancel_with_its_own_code(root, audio, weights, stub):
    """R13 reaches the verb, and a cancelled round writes NOTHING.

    §8.2: partial output is discarded. So the serial must not move either -- a
    cancelled round that consumed a serial would be indistinguishable on disk from
    a declined one, and only the second is a decision.
    """
    ctx, run, first = decoded(root, audio)
    before = write.read_origin(root, run)["high_water"]
    cancellation = progress.Cancellation()
    cancellation.request("detecting")
    live = verbs.Context(root=root, cancellation=cancellation)
    with pytest.raises(schema.AgentError) as e:
        verbs.detect(live, run=run, rev=first.rev)
    assert e.value.code == "cancelled"
    assert write.read_origin(root, run)["high_water"] == before


# --- plan / accept --------------------------------------------------------

def test_plan_reads_back_what_the_round_measured(root, audio, weights, stub):
    ctx, run, first = decoded(root, audio)
    got = verbs.plan(ctx, run=run, rev=first.rev)
    assert got.rev == first.rev
    assert got.unverified["no-token-layer"] == 0.0
    for ref in got.findings:
        assert ref.evidence, "a finding's evidence is numbers, and there are some"

    # The default is the run's high-water revision, not r000.
    verbs.detect(ctx, run=run, rev=first.rev)
    assert verbs.plan(ctx, run=run).rev == "r001"


def test_a_span_travels_as_a_cue_id_and_not_as_a_display_string(
        root, audio, weights, stub):
    """PIM v1.0 §7.1 ②. The position is a field, not something you parse out.

    **Positive control**: every ref's `cue_id` is the one on disk, and it is the
    key that reaches the cue -- so a caller can locate the span without touching
    `id`. **Negative control**: `id` is still the display string it always was
    (`engine-loop:58-66`), and the two are NOT the same value. If someone
    "simplifies" `cue_id` into a slice of `id` one day, this is the assertion that
    goes red, because a slice of `id` cannot be `c58`.
    """
    ctx, run, first = decoded(root, audio)
    got = verbs.plan(ctx, run=run, rev=first.rev)
    assert got.findings, "no finding, so this test would assert about nothing"

    on_disk = json.loads(
        (layout.run_dir(root, run) / layout.PLANS / f"{first.rev}.json"
         ).read_text("utf-8"))
    cue_text = {c["id"]: c["text"] for c in on_disk["recording"]["cues"]}

    for ref, row in zip(got.findings, on_disk["findings"]):
        assert str(ref.cue_id) == row["cue_id"]
        assert str(ref.cue_id) in cue_text, (
            "the id in the response must reach the cue; if it does not, the only "
            "locator left is the display string, which is the defect")
        # And it is not the display string, nor a prefix of it.
        assert str(ref.cue_id) != ref.id and not ref.id.startswith(str(ref.cue_id))


def test_plan_returns_the_proposals_accept_indexes_and_never_their_text(
        root, audio, weights, stub):
    """PIM v1.0 §5.5 / §7.1 ③, and INV-12 on the same object.

    **Positive control**: the list is in the same order as the on-disk `script[]`,
    which is the order `accept --op N` enumerates -- so index N here IS op N
    there. That equivalence is the reason the field exists at all.

    **Negative control**: the on-disk row has a `text` key and the response object
    has no such field. `delete` carries `None` today, so a copy would look
    harmless right up until milestone 2's `replace`, which is exactly when a
    silent INV-12 breach is worst.
    """
    ctx, run, first = decoded(root, audio)
    got = verbs.plan(ctx, run=run, rev=first.rev)
    assert got.ops, "the fixture proposes nothing, so this asserts about nothing"

    on_disk = json.loads(
        (layout.run_dir(root, run) / layout.PLANS / f"{first.rev}.json"
         ).read_text("utf-8"))
    assert len(got.ops) == len(on_disk["script"]) == int(on_disk["ops"])
    for ref, row in zip(got.ops, on_disk["script"]):
        assert (str(ref.op), str(ref.cue_id)) == (row["op"], row["cue_id"])
        assert (ref.char_start, ref.char_end) == (row["char_start"],
                                                  row["char_end"])
        assert str(ref.origin) == row["origin"]
    assert "text" in on_disk["script"][0], (
        "the negative control is vacuous unless the disk row HAS the field the "
        "response is asserted not to have")
    assert not hasattr(got.ops[0], "text")

    # Index N in the response is op N in `accept`: apply exactly one and get one.
    applied = verbs.accept(ctx, run=run, rev=first.rev, ops=(0,))
    assert applied.ops == 1.0


def test_a_second_filename_lands_on_the_run_that_holds_the_work(
        root, audio, weights, stub):
    """§9.2's whole point, stated in the currency the user cares about.

    A run with rounds in it and a bare re-intake under another name: the answer
    has to be the run holding the rounds, and it has to say how far that run got.
    Without the `high_water` half, 「this is already a run」 tells the reader
    nothing about whether re-decoding would waste seven minutes.
    """
    ctx, run, first = decoded(root, audio)
    twin = audio.parent / "copied off the recorder.m4a"
    twin.write_bytes(audio.read_bytes())

    again = verbs.intake(ctx, path=str(twin))
    assert again.run == run and again.recognised == 1.0
    assert again.high_water == 0.0, "r000 exists, so the answer is 0 and not -1"
    # And the folder was NOT renamed: presentation is the third question, and the
    # answer to it is "leave it alone" -- renaming buys no correctness (R17).
    assert layout.run_dir(root, run).is_dir()
    assert len(list((root / layout.STORE).iterdir())) == 1

    # The CLI turns the flag into the sentence; the agent never sees the prose.
    from ltm.ui import cli
    said = cli.render(again)
    assert "already this run" in said and "up to r000" in said
    assert "already this run" not in cli.render(first)


#: One language all the way through, both signals agreeing. The reduction rule has
#: something to write, so the filename may carry `zh`.
ALL_ONE_LANGUAGE = {"segments": 12, "gap": 0.01, "seg_seconds": 7.5,
                    "texts": [stub_decode.ZH] * 12,
                    "slot_langs": ["zh", "zh", "zh"]}


def test_a_decode_names_the_language_it_measured_or_names_none(
        root, audio, weights, monkeypatch):
    """I-18 end to end, through the verb, with both sides of §9.1's control.

    The default stub is bilingual, so its stretches do not agree on one label and
    the artifacts must claim nothing. `ALL_ONE_LANGUAGE` agrees, so they must
    claim `zh`. **Only having the second half is what let the defect ship**: the
    old hard-coded `"zh"` passed every Chinese recording and produced a `.zh.srt`
    for a discussion whose own record said `language_detected: "en"`.
    """
    monkeypatch.setenv("LTM_STUB", json.dumps(stub_decode.DECODE))
    ctx, run, mixed = decoded(root, audio)
    srt = [p for p in mixed.paths if p.endswith(".srt")]
    assert srt and not any(".zh." in p or ".en." in p for p in srt), srt
    plan_rec = json.loads(
        (layout.run_dir(root, run) / layout.PLANS / f"{mixed.rev}.json"
         ).read_text("utf-8"))
    labels = {s["language"] for s in plan_rec["recording"]["stretches"]}
    assert len(labels) > 1 or labels == {"undetermined"}, (
        f"this fixture measured {labels}, so it does not exercise the silent "
        f"side and the assertion above proves nothing")

    monkeypatch.setenv("LTM_STUB", json.dumps(ALL_ONE_LANGUAGE))
    ctx2, run2, single = decoded(root, audio)
    srt2 = [p for p in single.paths if p.endswith(".srt")]
    assert srt2 and all(".zh.srt" in p for p in srt2), srt2


def test_an_edit_round_inherits_the_language_claim_it_descends_from(
        root, audio, weights, monkeypatch):
    """An accept changes text, never the language determination.

    Positive control: a run whose stretches all say `zh` keeps `.zh.` across the
    accept. Negative control: the bilingual run keeps its silence -- a round that
    re-derived the label from the edited text could produce a different answer
    from the round it descends from, and the two files would sit in one folder
    disagreeing about what language the recording is.
    """
    monkeypatch.setenv("LTM_STUB", json.dumps(ALL_ONE_LANGUAGE))
    ctx, run, first = decoded(root, audio)
    if verbs.plan(ctx, run=run, rev=first.rev).ops:
        after = verbs.accept(ctx, run=run, rev=first.rev)
        assert all(".zh.srt" in p for p in after.paths if p.endswith(".srt"))

    monkeypatch.setenv("LTM_STUB", json.dumps(stub_decode.DECODE))
    ctx2, run2, mixed = decoded(root, audio)
    assert verbs.plan(ctx2, run=run2, rev=mixed.rev).ops, (
        "nothing to accept, so this half asserts about nothing")
    after2 = verbs.accept(ctx2, run=run2, rev=mixed.rev)
    srt = [p for p in after2.paths if p.endswith(".srt")]
    assert srt and not any(".zh." in p or ".en." in p for p in srt), srt


def test_decode_says_what_it_found_without_being_asked_again(
        root, audio, weights, stub):
    """PIM v1.0 §7.1 ④. `decode` detects; until now it reported none of it.

    **Positive control**: the summary `decode` returns is the same summary
    `detect` returns for the same round -- built from one `plan_obj.summary`, so
    the two verbs cannot describe one round two ways.

    **Negative control**: `ops` is the count and not the list. The list is
    `plan`'s (§5.5); duplicating it here would put the same fact in two responses,
    and the one nobody looks at is the one that goes stale.
    """
    ctx, run, first = decoded(root, audio)
    from_plan = verbs.plan(ctx, run=run, rev=first.rev)
    assert first.ops == float(len(from_plan.ops))
    assert isinstance(first.ops, float)
    assert sum(first.findings.values()) == float(len(from_plan.findings))
    assert sum(first.notes.values()) == float(len(from_plan.notes))
    # `detect` over the same revision measures the same text, so the two verbs'
    # summaries agree field by field.
    again = verbs.detect(ctx, run=run, rev=first.rev)
    assert (first.findings, first.notes, first.proposals) == (
        again.findings, again.notes, again.proposals)
    assert first.detectors_skipped == again.detectors_skipped


def test_decode_says_where_the_decoder_fell_back(root, audio, weights, stub):
    """Rung 0. I-25: 「the decoder fell back」 names the place, not 「flagged passages」.

    A temperature-1.0 fallback is not a detector trigger on its own (S4b is read
    only over a span some other signal already nominated), so the old sentence --
    「read the flagged passages yourself」 -- pointed at nothing on the 108-minute
    lecture that found it: one fallback, one unrelated `sparse-cue` finding. The
    response now carries the fallback windows' time ranges and the CLI says them.
    Control, by value: before the fix the response had no such field and the
    sentence carried no clock time.
    """
    from ltm.ui import cli
    _, _, got = decoded(root, audio)
    # The shared stub decode anneals some windows to 1.0; read which ones from
    # the plan rather than restating the fixture here.
    plan = json.loads(pathlib.Path(
        [p for p in got.paths if layout.PLANS in p][0]).read_text("utf-8"))
    hot = [(w["start"], w["end"]) for w in plan["recording"]["windows"]
           if w["temperature"] >= 1.0]
    assert hot, "the stub decode must anneal somewhere, or this proves nothing"
    assert [tuple(s) for s in got.degenerated_spans] == hot
    said = cli.render(got)
    assert "flagged passages" not in said
    m, s = divmod(int(hot[0][0]), 60)
    assert f"{m}:{s:02d}" in said


#: A decode holding one of each: four identical cues (S3's shoulder, A-20) and
#: then six (S3's bar). Both spans sit in windows the stub marks degenerate, so
#: **both score `n == 2`** -- S3 and S4, measured, not assumed -- and the only
#: thing that keeps the four-cue span at a note is the cap. A fixture where the
#: shoulder case scored 1 would exercise INV-6's ordinary `n == 1` clause and
#: prove nothing about A-20; the assertion below pins that, so the fixture cannot
#: drift into the easy case without saying so.
SHOULDER_AND_BAR = {"segments": 10, "gap": 0.01, "seg_seconds": 7.5,
                    "texts": [stub_decode.ZH] * 4 + [stub_decode.EN] * 6,
                    "slot_langs": ["zh", "zh", "en"]}


def test_a_note_carries_the_reason_it_is_a_note_all_the_way_to_the_reader(
        root, audio, weights, monkeypatch):
    """A-24. `near-threshold` existed in the detector and died at the store.

    A-20 created a note class whose whole purpose is an instruction to the reader
    -- go and look at this one -- and README says so. But the plan record's note
    rows carried id/code/unit/span/evidence and no reason, so the ONE verb that
    says where to look could not say which note it was looking at. The summary
    counted notes BY reason, which made the loss easy to miss: the aggregate was
    right and the rows were mute.

    Both halves are here. The positive: the shoulder span comes back saying
    `near-threshold`, and the same round's six-cue span is a finding, so the two
    thresholds are visibly different outcomes on one recording. The negative is
    the test below -- a row written before the field existed must read as
    "not on record", never as a guess.
    """
    monkeypatch.setenv("LTM_STUB", json.dumps(SHOULDER_AND_BAR))
    ctx, run, first = decoded(root, audio)
    got = verbs.plan(ctx, run=run, rev=first.rev)

    reasons = [str(n.reason) for n in got.notes]
    assert "near-threshold" in reasons, reasons
    assert got.findings, "the six-cue span is still a finding, or this proves nothing"

    # The fixture is doing the hard case: the note and the finding scored the
    # SAME `n`, so the only difference between them is the cap.
    _rev, data = verbs._read_plan(ctx, run, first.rev)
    rec = verbs._recording_of(data)
    thr = thresholds.of(rec)
    by_kind = {s.kind: s for s in score.score_all(rec, thr)}
    assert by_kind["near-run"].n == by_kind["run"].n == 2
    assert by_kind["near-run"].capped and not by_kind["run"].capped

    # And it is on DISK, not computed by the reader: a reason the store does not
    # hold is a reason the next session cannot see.
    stored = json.loads(verbs._plan_path(ctx, run, first.rev).read_text("utf-8"))
    assert [row.get("reason") for row in stored["notes"]] == reasons


def test_a_note_written_before_the_reason_existed_says_so_instead_of_guessing(
        root, audio, weights, monkeypatch):
    """The known-false half of A-24, and A-21's rule applied a second time.

    Absence is a THIRD value. Defaulting it to `single-signal` would be the one
    unrecoverable mistake here -- it reads as a measurement, it is indistinguishable
    from a real one, and it would be wrong precisely on the rounds this repository
    already wrote.
    """
    monkeypatch.setenv("LTM_STUB", json.dumps(SHOULDER_AND_BAR))
    ctx, run, first = decoded(root, audio)
    path = verbs._plan_path(ctx, run, first.rev)
    stored = json.loads(path.read_text("utf-8"))
    assert stored["notes"], "there is a note to strip, or this test is vacuous"
    for row in stored["notes"]:
        row.pop("reason")
    path.write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")

    got = verbs.plan(ctx, run=run, rev=first.rev)
    assert [n.reason for n in got.notes] == [None] * len(got.notes)


def test_accept_declined_consumes_a_serial_and_changes_nothing(root, audio,
                                                               weights, stub):
    """§7 case (3). 「我看過了，決定不改」 must not look like 「我沒跑過」.

    The declined round places the finding and the plan it declined, no transcript,
    and the serial moves. That is the whole difference between a decision and an
    absence, and it is the only way a later reader can tell them apart.
    """
    ctx, run, first = decoded(root, audio)
    got = verbs.accept(ctx, run=run, rev=first.rev, ops=(-1,))
    assert isinstance(got, schema.AcceptResponse)
    assert got.declined == 1.0 and got.ops == 0.0
    assert str(got.source_rev) == first.rev and got.rev == "r001"
    assert sorted(p.rsplit(".", 1)[-1] for p in got.paths) == ["json", "json"]
    assert write.read_origin(root, run)["high_water"] == 1


def test_accept_applies_proves_and_writes(root, audio, weights, stub):
    """INV-3/INV-4: the proof runs in memory before anything is written.

    The stub's first cue is a repeat, so there is something to accept. The new
    revision carries a transcript AND the edit script with its journal -- the
    journal is what makes `revert` total rather than best-effort, and it cannot be
    re-derived from the ops.
    """
    ctx, run, first = decoded(root, audio)
    plan_before = verbs.plan(ctx, run=run, rev=first.rev)
    assert len(plan_before.ops) > 0, (
        "the fixture decode proposes nothing, so this test would pass without "
        "applying anything. `DECODE` is built to contain two six-cue runs for "
        "exactly this reason -- a skip here is a gate that switched itself off")

    got = verbs.accept(ctx, run=run, rev=first.rev)
    assert got.declined == 0.0 and got.ops == float(len(plan_before.ops))
    edit = (layout.run_dir(root, run) / layout.EDITS / f"{got.rev}.json")
    assert edit.exists()
    body = json.loads(edit.read_text("utf-8"))
    assert body["source"] == first.rev
    assert len(body["journal"]) == len(body["ops"]) >= 1
    assert all(entry["removed"] for entry in body["journal"])


def test_an_agent_can_adopt_and_the_revision_says_an_agent_did(root, audio,
                                                               weights, stub):
    """A-5, and the record that replaced the refusal as the guarantee.

    While `accept` was human-only, "a person adopted this" was enforced. It is now
    only observable, so it is asserted here as a fact ON DISK rather than as the
    verb's return value -- a response is gone the moment it is read, and the
    question "who adopted this revision" gets asked months later.

    The control is the other face: the same verb through the CLI's entry point
    writes `human` on an otherwise identical round. One assertion cannot tell a
    working stamp from a constant.
    """
    from ltm.ui import cli
    assert cli.VERBS["accept"] is verbs.accept
    ctx, run, first = decoded(root, audio)

    by_agent = verbs.call(ctx, "accept", run=run, rev=first.rev)
    assert by_agent.verb.text == "accept"
    by_human = verbs.accept(ctx, run=run, rev=first.rev)

    faces = {r["serial"]: r.get("face")
             for r in write.read_origin(ctx.root, run)["revisions"]}
    assert faces[by_agent.rev] == "agent"
    assert faces[by_human.rev] == "human"


# --- cite -----------------------------------------------------------------

def test_cite_needs_an_authorisation_record_naming_this_revision(root, audio,
                                                                 weights, stub):
    """R14 / INV-20: no standing grants, and a missing record writes nothing.

    Both controls. A grant whose scope names the right revision succeeds; one
    naming the RUN rather than the revision is refused, and `_citable/` is left
    without an entry -- silence there would make 「not authorised」 and 「not cited」
    look identical on disk.
    """
    ctx, run, first = decoded(root, audio)
    with pytest.raises(schema.AgentError) as e:
        verbs.cite(ctx, run=run, rev=first.rev, granted_by="the user",
                   scope=run, method="cli-flag")          # a run, not a revision
    assert e.value.code == "grant-missing"
    assert not (root / layout.CITABLE).exists()

    got = verbs.cite(ctx, run=run, rev=first.rev, granted_by="the user",
                     scope=f"{run}/{first.rev}", method="cli-flag")
    assert got.paths and (root / layout.CITABLE).is_dir()
    grant = json.loads(
        (layout.citable_dir(root, run, first.rev) / layout.GRANT).read_text("utf-8"))
    assert grant["scope"] == f"{run}/{first.rev}"
    assert grant["method"] == "cli-flag" and grant["grantedBy"] == "the user"

    # A second citation of the same revision is refused: re-citing would overwrite
    # the grant already on record.
    with pytest.raises(schema.AgentError):
        verbs.cite(ctx, run=run, rev=first.rev, granted_by="the user",
                   scope=f"{run}/{first.rev}", method="cli-flag")


# --- cancel / runs / show / open -----------------------------------------

def test_cancel_answers_every_state_with_a_code_never_silence(root):
    """§8.1 limit (c): total over all twelve states, and the codes are the CLI's
    sentences' other projection.

    Derived from the edge list, so a state that gains or loses a cancel edge changes
    this answer in the same commit -- which is why this walks `STATES` rather than
    listing the four cancellable ones.
    """
    ctx = verbs.Context(root=root)
    for state in sorted(progress.STATES):
        got = verbs.cancel(ctx, state=state)
        accepted = state in progress.CANCELLABLE
        assert got.accepted == float(accepted)
        assert (got.reason_code is None) == accepted
        if not accepted:
            # ...and the human face has words for every one of those codes.
            from ltm.ui import cli
            assert cli.refusal_sentence(str(got.reason_code))


def test_runs_and_show_list_what_is_on_disk(root, audio, weights, stub):
    ctx, run, first = decoded(root, audio)
    listed = verbs.runs(ctx)
    assert [r.run for r in listed.runs] == [run]
    assert listed.runs[0].high_water == 0.0

    shown = verbs.show(ctx, run=run, rev=first.rev)
    assert shown.counts["cues"] == 12.0
    assert len(shown.stretches) == len(shown.languages)
    assert all(p.endswith((".srt", ".txt")) for p in shown.paths)

    with pytest.raises(schema.AgentError) as e:
        verbs.show(ctx, run="no-such-run-0badc0de")
    assert e.value.code == "no-such-run"
    assert e.value.subject is None, "a run id is not echoed into a refusal"


def test_open_hands_back_a_path_and_launches_nothing(root, audio):
    """§9: an agent asking for a window is a different act from a person clicking.

    So the agent face answers with the path and whether it exists. The launch lives
    in `ui/cli.py`, and this asserts the verb itself is inert.
    """
    ctx = verbs.Context(root=root)
    got = verbs.open_path(ctx, path=str(audio))
    assert got.path == str(audio) and got.exists == 1.0
    missing = verbs.open_path(ctx, path=str(audio.parent / "nope.txt"))
    assert missing.exists == 0.0
    import ltm.agentapi.verbs as module
    assert "startfile" not in module.__file__ or True
    src = open(module.__file__, encoding="utf-8").read()
    assert "startfile" not in src and "xdg-open" not in src


# --- I-28 / I-27 at the verb ------------------------------------------------

@pytest.mark.parametrize("kwargs,subject", [
    ({"language_set": ("en",), "language": "en", "multilingual": False},
     "language-set-with-pin"),
    ({"language_set": ("en",), "multilingual": False}, "language-set-with-pin"),
    ({"language_set": ("english",)}, "language-set"),
    ({"vad": False, "vad_threshold": 0.3}, "vad-knobs-without-vad"),
    ({"vad_threshold": 1.5}, "vad-threshold"),
    ({"vad_speech_pad_ms": -5}, "vad-milliseconds"),
])
def test_a_bad_language_set_or_vad_knob_is_refused_before_anything_starts(
        root, audio, weights, stub, tmp_path, kwargs, subject):
    """Same ORDER test as E-3(f): the interpreter does not exist, so a refusal
    after the spawn would read `engine-failed`. Control: the legal combination
    with the same missing interpreter surfaces as `engine-failed`."""
    ctx = verbs.Context(root=root)
    run = verbs.intake(ctx, path=str(audio)).run
    missing = str(tmp_path / "no-such-python.exe")
    with pytest.raises(schema.AgentError) as e:
        verbs.decode(ctx, run=run, python=missing, script=STUB,
                     duration_seconds=stub_decode.DURATION, **kwargs)
    assert (e.value.code, e.value.subject) == ("bad-argument", subject)
    with pytest.raises(schema.AgentError) as spawned:
        verbs.decode(ctx, run=run, python=missing, script=STUB,
                     duration_seconds=stub_decode.DURATION,
                     language_set=("en", "zh"), vad_threshold=0.3)
    assert spawned.value.code == "engine-failed"


def test_the_record_holds_the_childs_language_set_and_vad_overrides(
        root, audio, weights, stub, monkeypatch):
    """What was narrowed to and which VAD overrides ran are part of "what was
    this measured with". The record holds the CHILD's word: with
    `silent_settings` the child says nothing and the record must say `None`
    although the request carried both -- the known-false control."""
    _, run, _ = decoded(root, audio, language_set=("zh", "en"),
                        vad_threshold=0.35, vad_min_silence_ms=500)
    rec = write.decode_record(write.read_origin(root, run), "r000")
    assert rec["language_set"] == ["en", "zh"]
    assert rec["vad_parameters"] == {"threshold": 0.35,
                                     "min_silence_duration_ms": 500}
    plain = write.decode_record(write.read_origin(root, run), "r000")
    assert plain["language_set"] is not None

    monkeypatch.setenv("LTM_STUB", json.dumps(
        {**stub_decode.DECODE, "silent_settings": True}))
    ctx = verbs.Context(root=root)
    verbs.decode(ctx, run=run, duration_seconds=stub_decode.DURATION,
                 python=None, script=STUB, language_set=("en",),
                 vad_threshold=0.35)
    silent = write.decode_record(write.read_origin(root, run), "r001")
    assert silent["language_set"] is None and silent["vad_parameters"] is None
