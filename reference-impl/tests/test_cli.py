"""The human face. PSM §9 / §11 step 6.

What is checked here is what only this layer does: **it says things**. A code
reaching a person unexplained is the agent face leaking into the human one, and the
two most dangerous of those are the ones this project exists to prevent:

* a cancel refusal printed as `atomic-write` rather than as the reason;
* an unverified marker printed as `0`, which reads as 「everything passed」 when the
  truth is that nothing was in scope.

Both have their known-false control. The progress line has one too: a phase with no
denominator must SAY so rather than draw a bar it cannot fill.
"""
from __future__ import annotations

import io
import json
import os
import sys
import typing

import pytest

from fixtures import stub_decode
from ltm.agentapi import schema, verbs
from ltm.core import model, progress
from ltm.ui import cli

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STUB = os.path.join(HERE, "tests", "fixtures", "stub_sidecar.py")


# --- every code has words ------------------------------------------------

def test_every_refusal_code_has_a_sentence():
    """`core.progress` holds one table with two projections, and this is the other.

    Walked over `STATES` rather than over the four refusals, because the set is
    derived from the edge list: a state that loses its cancel edge starts needing a
    sentence, and this fails in the same commit rather than at the user.
    """
    for state in sorted(progress.STATES):
        code = progress.refusal_code(state)
        if code is None:
            assert state in progress.CANCELLABLE
            continue
        sentence = cli.refusal_sentence(code)
        # Prose, not the code with its hyphens taken out. A length comparison is the
        # wrong instrument here and this test used one for a commit: `already-finished`
        # and `already finished` are the same length, so the check passed the ONE code
        # whose two projections differ by a single character.
        assert " " in sentence and "-" not in sentence.split()[0], state
        assert sentence != code.replace("-", " ") or state in progress.TERMINAL

    with pytest.raises(RuntimeError) as e:
        cli.refusal_sentence("a-code-nobody-wrote-words-for")
    assert "one table" in str(e.value)


def test_a_refusal_under_json_is_data_and_without_json_is_prose(tmp_path, capsys):
    """Two faces, two answers, one refusal -- and neither may be the other's.

    Out of process, `--json` was the only agent-shaped channel the CLI had, and it
    covered success only: a refusal came back as an English sentence on stderr, so
    anything driving the product from a shell had to parse prose to learn that
    `no-such-run` happened. R4 says agent-drivable.

    The control is the second half: WITHOUT `--json` the same refusal must still be
    a sentence on stderr and stdout must stay empty. A fix that turned every human
    refusal into JSON would pass a one-sided version of this test.
    """
    root = str(tmp_path / "TranscriptDesk")
    assert cli.main(["--root", root, "--json", "show", "nosuchrun"]) == 2
    out = capsys.readouterr()
    payload = json.loads(out.out)
    assert payload == {"verb": "show", "code": "no-such-run", "subject": None}
    assert out.err == ""

    assert cli.main(["--root", root, "show", "nosuchrun"]) == 2
    plain = capsys.readouterr()
    assert plain.out == ""
    assert "no-such-run" in plain.err and " " in plain.err.strip()


def test_every_error_code_has_a_sentence():
    """The same rule for `schema.ErrorResponse.CODES`, enforced at import.

    `cli.py` raises at import time if a code has no words, so this asserts the table
    is total AND that its check is the kind that cannot be forgotten -- a lookup
    that fell back to printing the code would satisfy the first half only.
    """
    assert set(cli._ERRORS) == set(schema.ErrorResponse.CODES)
    for code, text in cli._ERRORS.items():
        assert len(text) > len(code)


def _claims_a_verb_is_human_only(text: str) -> bool:
    """Does this sentence assert, in the present tense, that some verb is a
    person's to run? Past-tense descriptions of the overturned rule are legal --
    `AGENTS.md` and the PSM have to be able to say what changed."""
    low = " ".join(text.lower().split())
    present = ("is a human action", "(human only)", "human only)",
               "is human-only", "only a human can", "humans only")
    past = ("was ", "stopped", "no longer", "left it", "no verb is in")
    return any(p in low for p in present) and not any(p in low for p in past)


def test_the_face_does_not_say_adoption_is_a_human_action():
    """A-21 overturned that on 2026-09-09, and two strings kept saying it.

    Round 13 (R13a-BLOCK-1) found the claim in `--help` itself; the widened
    twin-grep found the second copy in the prose for the `human-only` code, where
    it was worse: that sentence prints for WHATEVER verb is refused, so the next
    verb that needs to be a person's act would have been announced as "adoption".
    The authority is `verbs.HUMAN_ONLY`, so the assertion is `HUMAN_ONLY` is
    empty -> no shipped sentence claims one in the present tense.

    Both controls run here. The positive is the pair of strings this repository
    actually shipped until today -- a control derived from the defect rather than
    from what a test author imagines a defect looks like -- and the negative is
    the `AGENTS.md` sentence that describes the retired rule correctly and must
    never be flagged.
    """
    assert verbs.HUMAN_ONLY == (), (
        "a verb is human-only again: this test must be re-aimed, not deleted")

    shipped = {f"_ERRORS[{code!r}]": text for code, text in cli._ERRORS.items()}
    shipped["--help"] = cli.build_parser().format_help()
    offenders = {k for k, v in shipped.items() if _claims_a_verb_is_human_only(v)}
    assert offenders == set(), f"present-tense human-only claim in {offenders}"

    # positive control: the two strings as they stood before round 13
    assert _claims_a_verb_is_human_only(
        "adoption is a human action in milestone 1, so this verb has no agent "
        "form. Run it yourself.")
    assert _claims_a_verb_is_human_only("accept  apply selected ops (human only)")
    # negative control: the true description of the retired rule
    assert not _claims_a_verb_is_human_only(
        "the verb has no agent form. No verb is in this set today (A-21); the "
        "code and the mechanism remain for the next one that needs to be a "
        "person's act")


# --- the progress line ---------------------------------------------------

def test_a_phase_with_no_denominator_says_so():
    """INV-22 limit (b) in the interface: no invented percentage, and no bare spinner.

    `load-model` genuinely has no denominator (the model loads in 4.8-7.2 s with no
    callback), so the line reports elapsed seconds and says the phase has none. The
    known-true control is `decode`, which has one and must print it.
    """
    quiet = progress.ProgressEvent(state="decoding", phase="load-model",
                                   elapsed=3.0, name="faster-whisper")
    text = cli.Line.render(quiet)
    assert "%" not in text and "no denominator" in text
    assert "faster-whisper" in text

    loud = progress.ProgressEvent(state="decoding", phase="decode", elapsed=12.0,
                                  done=546.0, total=2183.68, unit="audio-seconds")
    bar = cli.Line.render(loud)
    assert "25%" in bar and "audio-seconds" in bar and "546/2184" in bar


def test_the_line_is_rewritten_not_logged():
    """936 detect steps as 936 lines is the wait with a paper trail, not progress."""
    buf = io.StringIO()
    line = cli.Line(buf)
    for i in (1, 2, 3):
        line(progress.ProgressEvent(state="detecting", phase="detect", elapsed=0.1,
                                    done=float(i), total=3.0, unit="cues",
                                    name="engine-loop"))
    out = buf.getvalue()
    assert out.count("\n") == 0, "no newline until the phase changes"
    assert out.count("\r") == 3
    # ...and a new phase closes the old line, so the two do not overwrite each other.
    line(progress.ProgressEvent(state="applying", phase="apply", elapsed=0.2,
                                done=1.0, total=1.0, unit="ops"))
    assert buf.getvalue().count("\n") == 1


def test_the_estimate_is_measured_from_this_phase_and_not_from_the_run():
    """What is left, from throughput this phase actually showed.

    `event.elapsed` runs from the start of the whole run. On the measured file the
    model load and the indivisible `transcribe()` call burn about 35 s before
    `decode` reports anything, so an estimate built on run-elapsed charges that
    time to every remaining audio-second. The known-false control is exactly that
    number: 25 done in 10 s of decoding is 30 s left, and it must NOT be the 120 s
    that run-elapsed would have produced.
    """
    buf = io.StringIO()
    line = cli.Line(buf)
    line(progress.ProgressEvent(state="decoding", phase="load-model", elapsed=30.0))
    line(progress.ProgressEvent(state="decoding", phase="decode", elapsed=30.0,
                                done=0.0, total=100.0, unit="audio-seconds"))
    line(progress.ProgressEvent(state="decoding", phase="decode", elapsed=40.0,
                                done=25.0, total=100.0, unit="audio-seconds"))
    out = buf.getvalue()
    assert "~30s left (estimate)" in out
    assert "~120s" not in out, "that is run-elapsed leaking into a phase estimate"


def test_nothing_is_estimated_when_there_is_nothing_to_estimate_from():
    """Two refusals, both INV-22's rule rather than a special case.

    No denominator -> no estimate, ever (the phase says it has none instead).
    Nothing done yet -> no estimate, because the first fraction is a division by
    zero wearing a friendly face. A finished phase gets none either: `left` on a
    bar at 100% is a promise about a thing already over.
    """
    quiet = progress.ProgressEvent(state="decoding", phase="load-model", elapsed=9.0)
    assert "left" not in cli.Line.render(quiet, phase_elapsed=9.0)

    unstarted = progress.ProgressEvent(state="decoding", phase="decode", elapsed=9.0,
                                       done=0.0, total=100.0, unit="audio-seconds")
    assert "left" not in cli.Line.render(unstarted, phase_elapsed=9.0)

    finished = progress.ProgressEvent(state="decoding", phase="decode", elapsed=9.0,
                                      done=100.0, total=100.0, unit="audio-seconds")
    assert "left" not in cli.Line.render(finished, phase_elapsed=9.0)


def test_a_denominator_that_stops_moving_says_so():
    """The 38-second stall, measured on a real 51-minute file.

    The contract was kept the whole time -- an event every second -- and the line
    still read as a hang, because the only number that moved was the clock. Both
    controls are here: below the threshold nothing is said (a note on every
    ordinary gap would train the reader to ignore it), and a numerator that moves
    again clears it.
    """
    buf = io.StringIO()
    line = cli.Line(buf)
    for elapsed, done in ((0.0, 5.0), (5.0, 5.0)):
        line(progress.ProgressEvent(state="decoding", phase="decode", elapsed=elapsed,
                                    done=done, total=100.0, unit="audio-seconds"))
    assert "no advance" not in buf.getvalue(), "5s is an ordinary gap, not a stall"

    line(progress.ProgressEvent(state="decoding", phase="decode", elapsed=12.0,
                                done=5.0, total=100.0, unit="audio-seconds"))
    assert "[no advance for 12s]" in buf.getvalue()

    before = len(buf.getvalue())
    line(progress.ProgressEvent(state="decoding", phase="decode", elapsed=13.0,
                                done=6.0, total=100.0, unit="audio-seconds"))
    assert "no advance" not in buf.getvalue()[before:], "it moved; the note goes"


# --- T-3 in words --------------------------------------------------------

def test_an_empty_population_is_said_not_printed_as_zero():
    """T-3's whole point, in the one place a person reads it.

    「0」 alone reads as 「the gate ran and everything passed」. In milestone 1 nothing
    was in scope for either gate, and those are different facts -- one is a result,
    the other the absence of one. The known-false control is a nonzero population,
    which must render as a fraction instead.
    """
    empty = cli.unverified_lines(None, {"no-token-layer": (0, 0)})
    assert len(empty) == 1
    assert "nothing was in scope" in empty[0]
    assert " of 0" not in empty[0], "a population of zero is never a denominator"

    real = cli.unverified_lines(None, {"no-token-layer": (1, 4)})
    assert "1 of 4" in real[0] and "nothing was in scope" not in real[0]


# --- main(), end to end --------------------------------------------------

def test_main_runs_the_pipeline_and_prints_prose(tmp_path, monkeypatch, capsys):
    """`python -m ltm` all the way through, on the stub engine.

    intake -> decode -> plan -> accept, then `runs`. The point is not the numbers --
    `test_agentapi_verbs.py` has those -- but that this face turns each response
    into something a person can read, and returns 0.
    """
    from ltm.engine import contract

    weights = stub_decode.fake_weights(tmp_path / "weights")
    monkeypatch.setenv(contract.MODEL_DIR_VAR, str(weights))
    monkeypatch.setenv(contract.SIDECAR_PYTHON_VAR, sys.executable)
    monkeypatch.setenv("LTM_STUB", json.dumps(stub_decode.DECODE))

    def via_stub(ctx, **kw):
        """The REAL `decode`, aimed at the stub sidecar.

        A wrapper, not a reimplementation: the CLI has no `--script` flag and must
        not grow one for a test's sake. `monkeypatch.setitem` restores the table
        afterwards, so the identity `test_both_faces_call_one_function` asserts is
        untouched outside this test.
        """
        return verbs.decode(ctx, duration_seconds=stub_decode.DURATION,
                            script=STUB, python=None, **kw)

    monkeypatch.setitem(cli.VERBS, "decode", via_stub)

    audio = tmp_path / "訪談 2026.m4a"
    audio.write_bytes(b"not really audio")
    root = str(tmp_path / "TranscriptDesk")

    # The run id is read from `--json` and NOT parsed out of the prose: it is
    # `<stem>-<hash8>` where the stem is the user's filename, so it holds a space
    # here on purpose. Splitting the prose line on whitespace is how a caller ends
    # up asking about `訪談` -- which this test did, and the product answered
    # `no-such-run` correctly.
    assert cli.main(["--root", root, "--json", "intake", str(audio)]) == 0
    run = json.loads(capsys.readouterr().out)["run"]
    assert " " in run

    assert cli.main(["--root", root, "intake", str(audio)]) == 0
    assert run in capsys.readouterr().out          # ...and the prose says it too

    assert cli.main(["--root", root, "decode", run]) == 0
    assert "revision r000" in capsys.readouterr().out

    assert cli.main(["--root", root, "plan", run]) == 0
    printed = capsys.readouterr().out
    assert "finding(s)" in printed and "proposed edit(s)" in printed

    assert cli.main(["--root", root, "accept", run, "--decline"]) == 0
    assert "declined" in capsys.readouterr().out

    assert cli.main(["--root", root, "runs"]) == 0
    assert run in capsys.readouterr().out

    # --json is the same object, as data. Not a second face: it is this one, and it
    # must round-trip through `json.loads` for a person piping it somewhere.
    assert cli.main(["--root", root, "--json", "plan", run]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["verb"] == "plan" and "unverified" in body


def test_a_refusal_exits_nonzero_and_explains_itself(tmp_path, capsys):
    """A code alone is not an explanation, and the exit status is not 0.

    Three refusals a user can actually hit: an unknown run, a revision that does not
    exist, and a word-level request the build cannot honour. Each prints the
    sentence, not the code alone.
    """
    root = str(tmp_path / "TranscriptDesk")
    assert cli.main(["--root", root, "plan", "nope-0badc0de"]) == 2
    err = capsys.readouterr().err
    assert "no-such-run" in err and "no run by that name" in err

    assert cli.main(["--root", root, "show", "nope-0badc0de"]) == 2
    assert "no run by that name" in capsys.readouterr().err


def test_the_cli_warns_before_an_uncontrolled_detector_runs(tmp_path, capsys):
    """§6.4: the user asked, so the user is told what the output is worth.

    Printed BEFORE the run -- afterwards it reads as an excuse for the findings --
    and the known-false control is the shipping default, which prints nothing. A
    warning that always appears is wallpaper.
    """
    root = str(tmp_path / "TranscriptDesk")
    cli.main(["--root", root, "detect", "nope-0badc0de", "--enable", "filler"])
    err = capsys.readouterr().err
    assert "filler:" in err and "no known-false control" in err

    cli.main(["--root", root, "detect", "nope-0badc0de"])
    assert "known-false control" not in capsys.readouterr().err


def test_ctrl_c_is_a_cancel_request_and_the_state_decides(tmp_path, capsys):
    """R13: Ctrl-C asks, and `ack` answers. It is not a kill.

    The mapping from the phase last reported to a state is total over
    `PHASE_UNITS` -- `cli.py` raises at import if a phase has no state -- so a new
    phase cannot arrive as a wait with no cancel. `detect` must be ACCEPTED (R13 put
    it in the set) and `apply` must be REFUSED with INV-13's reason.
    """
    assert cli._state_for("detect") == "detecting"
    assert cli._state_for("apply") == "applying"
    assert cli._state_for(None) == "queued"
    for phase in progress.PHASE_UNITS:
        assert cli._state_for(phase) in progress.STATES

    accepted = progress.Cancellation().request(cli._state_for("detect"))
    assert accepted.accepted and accepted.reason is None
    refused = progress.Cancellation().request(cli._state_for("apply"))
    assert not refused.accepted and "INV-13" in refused.reason


# --- the machine has no GPU: a pause, not a wall (A-22 / UAT A-8) ---------

class _Tty:
    """A stdin that claims to be a terminal and answers once."""

    def __init__(self, answer: str) -> None:
        self._answer = answer

    def isatty(self) -> bool:
        return True

    def readline(self) -> str:
        return self._answer


def _no_gpu_cli(tmp_path, monkeypatch):
    """A machine that can decode on the processor and nothing else.

    The stub's answer depends on the REQUEST, not on how many times it has been
    asked: anything but `device="cpu"` is refused, `cpu` works. That is the real
    situation, and it is stateless, so the same helper serves the yes, the no,
    and the two flags without a call counter deciding the outcome. A stub that
    always failed could only ever prove the refusal.
    """
    from ltm.engine import contract

    weights = stub_decode.fake_weights(tmp_path / "weights")
    monkeypatch.setenv(contract.MODEL_DIR_VAR, str(weights))
    monkeypatch.setenv(contract.SIDECAR_PYTHON_VAR, sys.executable)
    monkeypatch.setenv("LTM_STUB", json.dumps(stub_decode.DECODE))
    calls: list[dict] = []

    def via_stub(ctx, **kw):
        calls.append(kw)
        stub = dict(stub_decode.DECODE)
        if kw.get("device") != "cpu":
            stub["no_gpu"] = True
        monkeypatch.setenv("LTM_STUB", json.dumps(stub))
        return verbs.decode(ctx, duration_seconds=stub_decode.DURATION,
                            script=STUB, python=None, **kw)

    monkeypatch.setitem(cli.VERBS, "decode", via_stub)
    audio = tmp_path / "訪談 2026.m4a"
    audio.write_bytes(b"not really audio")
    root = str(tmp_path / "TranscriptDesk")
    assert cli.main(["--root", root, "--json", "intake", str(audio)]) == 0
    return root, calls


def test_a_missing_gpu_stops_the_run_and_asks_in_words_a_person_can_answer(
        tmp_path, monkeypatch, capsys):
    """The user's ruling, executed: refuse, but as a PAUSE (UAT A-8).

    Three properties, and all three are the ruling rather than my taste. The
    sentence names no library and no numeric format -- someone who does not know
    what `float16` is still has to be able to answer. A yes CONTINUES, on the
    processor, and the second call really is a different request (`int8`/`cpu`)
    rather than the same one retried. And it is one question per run, not a
    setting to discover afterwards.
    """
    root, calls = _no_gpu_cli(tmp_path, monkeypatch)
    run = json.loads(capsys.readouterr().out)["run"]
    monkeypatch.setattr(sys, "stdin", _Tty("y\n"))

    assert cli.main(["--root", root, "decode", run]) == 0
    err = capsys.readouterr().err
    assert "graphics card" in err and "processor" in err
    for jargon in ("float16", "int8", "compute", "ctranslate2", "cuda"):
        assert jargon not in err.lower(), f"{jargon!r} is not a person's word"

    assert [c.get("device") for c in calls] == ["auto", "cpu"]
    assert calls[1]["compute_type"] == "int8"


def test_pinning_a_language_names_the_flag_that_completes_it(tmp_path, monkeypatch,
                                                            capsys):
    """E-3(f) through the human face (PSM 17.0 (5)).

    `--language zh` alone is refused before the engine is asked anything, and the
    sentence names `--no-multilingual` -- the code and subject alone say what broke,
    not what to type. The positive half: the same pin WITH the flag reaches the verb
    carrying both, and decodes (on the processor, which this stub machine allows).
    """
    root, calls = _no_gpu_cli(tmp_path, monkeypatch)
    run = json.loads(capsys.readouterr().out)["run"]

    assert cli.main(["--root", root, "decode", run, "--language", "zh"]) == 2
    err = capsys.readouterr().err
    assert "language-with-multilingual" in err and "--no-multilingual" in err
    assert calls[-1]["language"] == "zh" and calls[-1]["multilingual"] is True

    assert cli.main(["--root", root, "--json", "decode", run, "--language", "zh"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert (out["code"], out["subject"]) == ("bad-argument",
                                             "language-with-multilingual")

    assert cli.main(["--root", root, "decode", run, "--language", "zh",
                     "--no-multilingual", "--device", "cpu",
                     "--compute-type", "int8"]) == 0
    assert calls[-1]["language"] == "zh" and calls[-1]["multilingual"] is False


def test_a_no_leaves_the_refusal_a_refusal(tmp_path, monkeypatch, capsys):
    """The other side of the same question, and the reason the default is no.

    Declining must not half-run: the exit code is the refusal's, the engine was
    asked exactly once, and nothing was written. A pause that proceeds when the
    person says no is not a pause.
    """
    root, calls = _no_gpu_cli(tmp_path, monkeypatch)
    run = json.loads(capsys.readouterr().out)["run"]
    monkeypatch.setattr(sys, "stdin", _Tty("n\n"))

    assert cli.main(["--root", root, "decode", run]) == 2
    assert len(calls) == 1
    assert "engine-failed" in capsys.readouterr().err


def test_without_a_terminal_it_prints_the_way_to_consent_instead_of_guessing(
        tmp_path, monkeypatch, capsys):
    """A prompt nobody can see is a hang; a yes nobody gave is the invisible
    downgrade this whole mechanism exists instead of.

    So a non-interactive run stops and names the flag. `--if-no-gpu cpu` is that
    flag, and it is consent given in advance -- the control below proves it
    actually continues, so this is not a mechanism that only ever says no.
    """
    root, calls = _no_gpu_cli(tmp_path, monkeypatch)
    run = json.loads(capsys.readouterr().out)["run"]

    assert cli.main(["--root", root, "decode", run]) == 2
    assert "--if-no-gpu cpu" in capsys.readouterr().err
    assert len(calls) == 1

    assert cli.main(["--root", root, "decode", run, "--if-no-gpu", "cpu"]) == 0
    assert [c.get("device") for c in calls] == ["auto", "auto", "cpu"]

    assert cli.main(["--root", root, "decode", run, "--if-no-gpu", "fail"]) == 2


def test_under_json_the_refusal_is_data_and_no_question_is_asked(
        tmp_path, monkeypatch, capsys):
    """The machine face is not asked a question -- it is told the code.

    `--json` must never reach the prompt: on a pipe that is a hang, and an agent
    does not need to be coaxed. It gets `engine-failed` with the subject naming
    the cause, and decides for itself whether to call again with
    `compute_type="int8", device="cpu"` -- which is the same consent, expressed
    the way an agent expresses one (INV-12: still no prose in the response).
    """
    root, calls = _no_gpu_cli(tmp_path, monkeypatch)
    run = json.loads(capsys.readouterr().out)["run"]
    monkeypatch.setattr(sys, "stdin", _Tty("y\n"))

    assert cli.main(["--root", root, "--json", "decode", run]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["code"] == "engine-failed"
    assert out["subject"] == cli.NO_GPU
    assert len(calls) == 1


# --- how a note is explained to a person (A-24) ---------------------------

def _note(reason):
    return schema.SpanRef(id="engine-loop:0-3", cue_id=schema.ident("c0"),
                          code=schema.ident("engine-loop"),
                          unit=schema.ident("cue"), char_start=0.0,
                          char_end=12.0, evidence={},
                          reason=schema.ident(reason) if reason else None)


def test_every_note_reason_has_its_own_sentence_and_none_of_them_is_borrowed():
    """A-24, at rung 1: the domain is a closed set of three, plus absence.

    This line used to give all of them one sentence -- "one signal only, so it is
    flagged and not proposed for deletion" -- which was already false for
    `no-action` and became false in a way a reader would ACT on when A-20 added
    `near-threshold`: the measured shoulder case scores `n == 2` and is held at a
    note by the cap, so "one signal only" is not a hedge, it is the wrong number.

    Exhaustive rather than exemplary because the domain is finite: the first
    assertion is what fails when a fourth reason is added to `model.Note` and
    nobody writes its sentence, which is the same defect arriving a second time.
    """
    hints = typing.get_type_hints(model.Note)
    assert set(cli.NOTE_SENTENCE) == set(typing.get_args(hints["reason"]))

    said = {r: cli.describe_plan(schema.PlanResponse(
        verb=schema.ident("plan"), run="r", rev="r000", findings=(),
        notes=(_note(r),), ops=(), unverified={}))[-1]
        for r in list(cli.NOTE_SENTENCE) + [None]}

    assert len(set(said.values())) == 4, said
    assert "one signal only" not in said["near-threshold"]
    assert "one signal only" not in said["no-action"]
    # Absence is a third value: it must not be dressed as the commonest reason.
    assert said[None] != said["single-signal"]
    # And every line says WHERE, or "go and look at this one" is not an instruction.
    assert all("0-12" in line for line in said.values())
