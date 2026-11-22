"""The engine: both channels, E-1, E-2, INV-14, and INV-22's three limits.

PSM sections 8.1/8.2/8.3. Everything here runs against `fixtures/stub_sidecar.py`
under the SAME interpreter as the tests -- deliberately, because a real decode
takes a 3 GB model load and ~6 s per call, and neither belongs in an assertion
about a one-second gap ceiling. The one test that uses the real sidecar is marked
`real_sidecar` and skips with the missing interpreter named (PSM §12).

**Every limit is asserted against BOTH channels** (`parametrize` over them), which
is the point of §8.3: the alternative exists because of a stall that is not
explained, and an alternative nobody tests is a plan rather than a fallback.

**Every limit ships its negative control**, and the controls are what the stub's
misbehaviour flags exist for. One shared control would not do: any single way for a
gate to be broken-but-green will eventually happen.
"""
from __future__ import annotations

import ast
import json
import os
import pathlib
import time

import pytest

from ltm.core.progress import Cancellation, MAX_GAP_SECONDS, ProgressError
from ltm.engine import contract, runner
from ltm.engine.channel import FileChannel, StdoutChannel
from ltm.engine.contract import EngineFailure, EngineRequest

STUB = pathlib.Path(__file__).parent / "fixtures" / "stub_sidecar.py"
HERE = pathlib.Path(__file__).resolve().parents[1]
SIDECAR_SRC = HERE / "src" / "ltm" / "engine" / "sidecar.py"

CHANNELS = ["stdout", "file"]


@pytest.fixture()
def audio(tmp_path):
    p = tmp_path / "talk.aac"
    p.write_bytes(b"not really audio; the stub never opens it")
    return p


@pytest.fixture()
def weights(tmp_path):
    """A folder holding every file `check_model_is_local` requires -- see the test."""
    d = tmp_path / "weights"
    d.mkdir()
    for alts in runner.REQUIRED_MODEL_FILES:
        (d / alts[0]).write_text("{}", encoding="utf-8")
    return d


def request_for(channel: str, audio, weights, tmp_path, **over) -> EngineRequest:
    kwargs = dict(audio=str(audio), model_path=str(weights),
                  duration_seconds=3.0, heartbeat_seconds=0.1)
    if channel == "file":
        kwargs["progress_path"] = str(tmp_path / "run" / "_progress.jsonl")
        kwargs["cancel_path"] = str(tmp_path / "run" / "_cancel")
    kwargs.update(over)
    return EngineRequest(**kwargs)


def drive(request, *, stub: dict | None = None, cancellation=None,
          cancel_after: int | None = None, **kw):
    """Run the stub and collect every `ProgressEvent` with its arrival time."""
    events: list[tuple[float, object]] = []

    def sink(ev):
        events.append((time.monotonic(), ev))
        if cancel_after is not None and cancellation is not None:
            if len(events) == cancel_after:
                cancellation.request("decoding")

    env_before = os.environ.get("LTM_STUB")
    os.environ["LTM_STUB"] = json.dumps(stub or {})
    try:
        result = runner.run(request, on_progress=sink, cancellation=cancellation,
                            python=None, script=STUB, **kw)
    finally:
        if env_before is None:
            os.environ.pop("LTM_STUB", None)
        else:
            os.environ["LTM_STUB"] = env_before
    return result, events


def gaps(events) -> list[float]:
    return [b[0] - a[0] for a, b in zip(events, events[1:])]


# --- the stub is a stub, and the interpreter is this one -----------------

@pytest.fixture(autouse=True)
def _stub_interpreter(request, monkeypatch):
    """The stub runs under the test interpreter, so `sidecar_python()` is `sys.executable`.

    Patched rather than passed, so that `run()` is exercised through the same code
    path the product uses -- including the `interpreter-missing` check.

    **Except for the `real_sidecar` test**, which exists precisely to use the real
    interpreter: an autouse fixture that redirected it would have left the suite
    looking green while the one test that speaks to the real engine quietly spoke
    to this one instead. That is the same class of defect as RH-3 -- an acceptance
    step that cannot fail -- reintroduced through a fixture.
    """
    if request.node.get_closest_marker("real_sidecar"):
        return
    import sys
    monkeypatch.setenv(contract.SIDECAR_PYTHON_VAR, sys.executable)


# --- limit (a): consecutive events at most one second apart -------------

@pytest.mark.parametrize("channel", CHANNELS)
def test_gap_ceiling(channel, audio, weights, tmp_path):
    """INV-22 limit (a), including the phases that have no denominator.

    The positive control is a conforming implementation: the stub beats, and the
    runner also beats on a timer, so no two consecutive events are more than a
    second apart even while the "model" is "loading".
    """
    req = request_for(channel, audio, weights, tmp_path)
    result, events = drive(req, stub={"segments": 3, "gap": 0.15})
    assert len(result.segments) == 3
    assert events, "no progress at all is the failure this limit exists for"
    worst = max(gaps(events), default=0.0)
    assert worst <= MAX_GAP_SECONDS, f"worst gap {worst:.2f}s on the {channel} channel"


@pytest.mark.parametrize("channel", CHANNELS)
def test_negative_control_no_heartbeat_would_breach_the_ceiling(
        channel, audio, weights, tmp_path):
    """§8.1's control for (a): emit only on segment arrival.

    On a short file that implementation passes trivially -- three events, no long
    gap -- which is exactly why the control has to make the wait long. With the
    stub silent for 1.5 s and its own heartbeat switched off, the only thing that
    can keep the gap under a second is the runner's own beat. So the assertion is
    two-sided: the CHILD's events alone breach the ceiling, and the runner's beats
    repair it.
    """
    req = request_for(channel, audio, weights, tmp_path, heartbeat_seconds=0.1)
    result, events = drive(req, stub={"segments": 2, "no_heartbeat": True,
                                      "gap": 1.5})
    beats = [e for _, e in events if e.phase in ("load-model", "transcribe-call")]
    assert len(beats) >= 2, "the runner must beat while the child says nothing"
    assert max(gaps(events)) <= MAX_GAP_SECONDS
    # and the control really was silent: the child sent far fewer events than we saw
    child_segments = len(result.segments)
    assert len(events) > child_segments + 2


# --- limit (b): a denominator where one exists --------------------------

@pytest.mark.parametrize("channel", CHANNELS)
def test_denominator_reported(channel, audio, weights, tmp_path):
    req = request_for(channel, audio, weights, tmp_path)
    _, events = drive(req, stub={"segments": 3, "gap": 0.1})
    decode = [e for _, e in events if e.phase == "decode"]
    assert decode, "no decode events"
    assert all(e.total == 3.0 and e.unit == "audio-seconds" for e in decode)
    load = [e for _, e in events if e.phase in ("load-model", "transcribe-call")]
    assert all(e.total is None for e in load), (
        "a phase that can have no denominator must not carry an invented one")


@pytest.mark.parametrize("channel", CHANNELS)
def test_negative_control_decode_without_a_total_goes_red(
        channel, audio, weights, tmp_path):
    """§8.1's control for (b). The stub sends `decode` with no total.

    The runner does not paper over it: `ProgressEvent.__post_init__` refuses, and
    the refusal surfaces rather than being swallowed into a bar with no bar.
    """
    req = request_for(channel, audio, weights, tmp_path)
    with pytest.raises(ProgressError, match="has a denominator"):
        drive(req, stub={"segments": 2, "no_denominator": True, "gap": 0.1})


@pytest.mark.parametrize("channel", CHANNELS)
def test_the_engines_timestamps_may_run_past_the_end_and_the_run_survives(
        channel, audio, weights, tmp_path):
    """A-23, found by decoding a 51-minute recording and losing it at the end.

    The denominator is MEASURED (`ffprobe`) and the numerator is the ENGINE's
    segment end, so the two can disagree -- and on real audio they did, by 0.44 s
    on 3065.216. `ProgressEvent` refused the reading, correctly, and the refusal
    took a quarter of an hour of decoding with it. **A bar that cannot reach 103%
    is the rule; ending the run is not what that rule asks for.**

    So the bar stops at the end and the overshoot is carried out on the result,
    where a caller that seeks with the last cue's `end` can see it. Both halves
    are asserted: no event exceeds its total (the bar), and the number is not
    zero (it was not swallowed).

    The control is `test_negative_control_decode_without_a_total_goes_red` above:
    an impossible reading the parent CANNOT reconcile still stops the run, so this
    is a reconciliation and not the guard being switched off.
    """
    req = request_for(channel, audio, weights, tmp_path)
    result, events = drive(req, stub={"segments": 3, "gap": 0.1,
                                      "past_end": 0.44})
    decode = [e for _, e in events if e.phase == "decode"]
    assert decode, "no decode events"
    assert all(e.done <= e.total for e in decode), (
        "the bar reached past its own denominator")
    assert max(e.done for e in decode) == 3.0, "and it did reach the end"
    assert result.timestamps_past_end == 0.44
    assert result.segments[-1].end > 3.0, (
        "the SEGMENT keeps its own timestamp -- only the progress reading was "
        "reconciled, and a cue that ends past the file is still what happened")


@pytest.mark.parametrize("channel", CHANNELS)
def test_a_decode_that_ends_on_time_reports_no_overshoot(
        channel, audio, weights, tmp_path):
    """The known-false half. A field that is never zero measures nothing."""
    req = request_for(channel, audio, weights, tmp_path)
    result, _ = drive(req, stub={"segments": 3, "gap": 0.1})
    assert result.timestamps_past_end == 0.0


# --- limit (c): a cancel is answered, and answered in time --------------

@pytest.mark.parametrize("channel", CHANNELS)
def test_cancel_answered(channel, audio, weights, tmp_path):
    """INV-22 limit (c). The reply comes back before the work stops.

    The ack is produced from the state by `Cancellation.request()`, so its latency
    is not the child's problem at all -- which is the design: "pressed it, nothing
    happened, then it vanished" is caused by systems that act first and report
    later. The child then stops between segments and the run raises rather than
    returning a partial result (§8.2: partial output is discarded, nothing written).
    """
    req = request_for(channel, audio, weights, tmp_path)
    c = Cancellation()
    t0 = time.monotonic()
    with pytest.raises(runner.EngineError) as e:
        drive(req, stub={"segments": 40, "gap": 0.05}, cancellation=c,
              cancel_after=3)
    assert c.requested and c.requested_at == "decoding"
    assert time.monotonic() - t0 < runner.CANCEL_GRACE + 5
    # `cancelled`, not `exited` (2026-09-16). The two are the same event seen
    # from the process table and different events seen by the person who pressed
    # the button; while they shared a code, a cancelled decode reached the user
    # as "your run crashed". The discrimination -- that a child dying on its own
    # still says something else -- is asserted in
    # `test_a_cancel_and_a_crash_do_not_share_a_code`.
    assert e.value.failure.code == "cancelled"
    assert e.value.failure.channel == channel


@pytest.mark.parametrize("channel", CHANNELS)
def test_negative_control_a_child_that_answers_nothing_still_ends(
        channel, audio, weights, tmp_path):
    """§8.1's control for (c): receive the cancel, answer nothing, exit quietly.

    A test that only checked the final state would pass here -- the run does end --
    and the user would have seen "pressed it, nothing happened, then it vanished".
    What must survive is the ACK: it exists, it was accepted, and it was produced
    without waiting for the child.
    """
    req = request_for(channel, audio, weights, tmp_path)
    c = Cancellation()
    ack = c.request("decoding")
    assert ack.accepted and ack.reason is None      # the reply, before any work
    with pytest.raises(runner.EngineError) as e:
        drive(req, stub={"segments": 40, "gap": 0.05, "ignore_cancel": True},
              cancellation=c)
    # Named, not just raised (I-22, 2026-09-27): this used to arrive as
    # `no-segments`, and once an empty decode became a legal answer the only thing
    # left telling this child apart from a silent recording is the missing report.
    assert e.value.failure.code == "no-report"


@pytest.mark.parametrize("channel", CHANNELS)
def test_a_cancel_and_a_crash_do_not_share_a_code(channel, audio, weights,
                                                  tmp_path):
    """The discrimination, which is the whole content of the 2026-09-16 change.

    Renaming one code proves nothing on its own: a `run` that returned
    `cancelled` for every ending would pass the cancel test above and be worse
    than what it replaced. So both endings are produced in one test and the
    codes must differ -- a cancel the parent asked for, and a child that died
    without being asked.

    Why it matters beyond the label: `verbs.decode` maps `cancelled` to
    `AgentError("cancelled", phase)` and everything else to `engine-failed`, so
    a surface that reads the code decides between "you stopped this" and
    "something broke". While the two shared a spelling, the only way to tell
    them apart was to remember having sent a cancel -- the parent deciding an
    outcome that is the child's to report, which is what R13 separates.

    **Each run gets its own directory**, which is not tidiness: on the `file`
    channel the progress stream and the cancel flag are FILES under the request's
    path, so two runs sharing a `tmp_path` share them. Written the obvious way,
    the second run re-read the first one's `cancelled` event out of the leftover
    stream and this assertion failed against a correct product -- the control
    firing on the test rather than on the code, which is the cheapest kind of
    false positive to believe.
    """
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()

    c = Cancellation()
    with pytest.raises(runner.EngineError) as cancelled:
        drive(request_for(channel, audio, weights, first),
              stub={"segments": 40, "gap": 0.05}, cancellation=c, cancel_after=3)

    with pytest.raises(runner.EngineError) as died:
        drive(request_for(channel, audio, weights, second),
              stub={"segments": 3, "crash": 2, "gap": 0.05})

    assert cancelled.value.failure.code == "cancelled"
    assert died.value.failure.code != "cancelled", (
        "a child that was never asked to stop reports a cancel, so the code "
        "says nothing about which of the two happened")


def test_a_refused_cancel_does_not_stop_the_run(audio, weights, tmp_path):
    """INV-13. `applying` refuses, and the flag must not be set anyway."""
    req = request_for("stdout", audio, weights, tmp_path)
    c = Cancellation()
    assert not c.request("applying").accepted
    result, _ = drive(req, stub={"segments": 2, "gap": 0.05}, cancellation=c)
    assert len(result.segments) == 2, "a refused cancel must leave the run alone"


# --- INV-14: a failure names only what it determined --------------------

def test_failure_names_only_what_it_determined(audio, weights, tmp_path):
    """INV-14, on all four observable failures this test can produce.

    The point is as much what the failure does NOT contain: no cause, and no
    captured text. `EngineFailure` accepts only codes for things the parent can
    see, and the child's stderr goes to a file whose PATH is carried instead.
    """
    req = request_for("stdout", audio, weights, tmp_path)

    # (1) the child crashes mid-decode
    with pytest.raises(runner.EngineError) as e:
        drive(req, stub={"segments": 3, "crash": 2, "gap": 0.05})
    assert e.value.failure.code == "crashed"
    assert e.value.failure.stderr_path, "detail belongs in a file, named here"
    assert e.value.failure.channel == "stdout"

    # (2) the child says nothing at all -- the unexplained stall's shape. The code
    #     says what was SEEN (no output before the deadline), never why.
    with pytest.raises(runner.EngineError) as e:
        drive(req, stub={"silent": 5}, first_event_timeout=1.0)
    assert e.value.failure.code == "silent"
    assert e.value.failure.detail["waited_seconds"] >= 1.0

    # (3) the audio is not there
    missing = request_for("stdout", tmp_path / "nope.aac", weights, tmp_path)
    with pytest.raises(runner.EngineError) as e:
        runner.run(missing, script=STUB)
    assert e.value.failure.code == "audio-missing"

    # (4) an invented failure code is refused, so a theory cannot be reported as
    #     an observation
    with pytest.raises(contract.ContractError, match="not an observable failure"):
        EngineFailure(code="probably-cuda")


def test_the_file_a_failure_names_has_something_in_it(audio, weights, tmp_path):
    """INV-14's other half: the path is only worth carrying if the file is not empty.

    `EngineFailure` carries a `stderr_path` instead of the text, so the path is the
    ONE place a reader is sent. On this machine, when the GPU became unavailable
    mid-decode, the product refused correctly -- `crashed`, during `decode`, exit 1 --
    and named a stderr file of **zero bytes**. The child was announcing the failure
    on stdout before recording it on stderr, and the parent's response to that event
    is `proc.terminate()`, so the child lost the race it should never have been in.
    Running the same sidecar by hand printed the cause immediately:
    `ValueError: Requested float16 compute type…`.

    The positive control is `stderr_late`, which restores the old order with a sleep
    so the race is decided rather than gambled: the assertion below must FAIL on it.
    """
    req = request_for("stdout", audio, weights, tmp_path)

    with pytest.raises(runner.EngineError) as e:
        drive(req, stub={"segments": 3, "crash": 2, "gap": 0.05})
    named = pathlib.Path(e.value.failure.stderr_path)
    assert named.is_file(), named
    assert named.read_text("utf-8").strip(), (
        "the failure sent the reader to an empty file; the child must write stderr "
        "and flush BEFORE it emits the event that makes the parent terminate it")

    # The control: the same crash with the writes in the old order writes nothing,
    # which is what the assertion above is there to catch.
    with pytest.raises(runner.EngineError) as late:
        drive(req, stub={"segments": 3, "crash": 2, "gap": 0.05,
                         "stderr_late": True})
    assert not pathlib.Path(late.value.failure.stderr_path).read_text("utf-8").strip()


def test_the_weights_must_be_a_local_directory(audio, tmp_path):
    """The one route by which the core path could reach the network (INV-18, N-5).

    `WhisperModel("large-v3")` is a download and the string is indistinguishable
    from a path to any static analysis, so this is checked where the value is used.
    Refusing before the spawn also means the failure names the missing weights
    rather than surfacing as a silent child quietly fetching gigabytes.
    """
    req = request_for("stdout", audio, tmp_path / "large-v3", tmp_path)
    with pytest.raises(runner.EngineError) as e:
        runner.run(req, script=STUB)
    assert e.value.failure.code == "model-missing"


@pytest.mark.parametrize("required", runner.REQUIRED_MODEL_FILES,
                         ids=lambda alts: alts[0])
def test_an_incomplete_model_folder_is_refused_before_the_spawn(
        required, audio, weights, tmp_path):
    """Rung 1: every requirement in the table, each removed alone.

    Found 2026-10-27: a Hugging Face snapshot of large-v3 without its config.json
    was a directory, passed the old check, and the child died inside ctranslate2
    with a JSON type_error naming no file. Without tokenizer.json faster-whisper
    would fetch one from the hub instead -- the network route INV-18 closes.
    The failure must say WHICH requirement is missing, by flag.
    """
    for name in required:
        (weights / name).unlink(missing_ok=True)
    req = request_for("stdout", audio, weights, tmp_path)
    with pytest.raises(runner.EngineError) as e:
        runner.run(req, script=STUB)
    assert e.value.failure.code == "model-missing"
    flag = f"missing_{required[0].replace('.', '_')}"
    assert e.value.failure.detail == {"is_absolute": 1.0, flag: 1.0}


def test_a_complete_model_folder_passes_the_preflight(weights):
    """The negative control: the fixture folder itself, and the `.txt` vocabulary
    older conversions use (faster-whisper medium's snapshot has vocabulary.txt)."""
    assert runner.check_model_is_local(str(weights)) is None
    (weights / "vocabulary.json").unlink()
    (weights / "vocabulary.txt").write_text("", encoding="utf-8")
    assert runner.check_model_is_local(str(weights)) is None


def test_a_missing_interpreter_is_named_not_guessed(audio, weights, tmp_path,
                                                    monkeypatch):
    monkeypatch.setenv(contract.SIDECAR_PYTHON_VAR, str(tmp_path / "no-python.exe"))
    req = request_for("stdout", audio, weights, tmp_path)
    with pytest.raises(runner.EngineError) as e:
        runner.run(req, script=STUB)
    assert e.value.failure.code == "interpreter-missing"


# --- E-1: the flag is passed explicitly ---------------------------------

def test_condition_flag_is_passed_explicitly():
    """E-1, in three places, because the defect could hide in any one of them.

    The upstream product never passed this parameter and therefore ran at
    faster-whisper's default -- on -- while the documentation argued about a value
    nobody was sending. So: the field cannot be omitted from the request, the
    request always serialises it, and the sidecar passes it as a KEYWORD to
    `transcribe()`. The third is checked by reading the AST, because the only
    interpreter that can execute that call is not the one running these tests --
    and "we cannot run it" is not a reason to leave it unasserted.
    """
    req = EngineRequest(audio="x.aac")
    assert req.condition_on_previous_text is False        # default off
    assert '"condition_on_previous_text": false' in req.to_line()

    with pytest.raises(contract.ContractError):
        EngineRequest(audio="x.aac", condition_on_previous_text=None)  # type: ignore[arg-type]

    tree = ast.parse(SIDECAR_SRC.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "transcribe"]
    assert len(calls) == 1, "exactly one transcribe call, or this check is guessing"
    kwargs = {k.arg for k in calls[0].keywords}
    assert "condition_on_previous_text" in kwargs, (
        "the sidecar must pass it explicitly, never rely on the engine's default")
    assert "vad_filter" in kwargs and "language" in kwargs


def test_multilingual_is_passed_explicitly_and_on_by_default():
    """E-3(a)/(b), in the same three places as E-1 and for the same reason.

    The library default is `False`, and `False` is the decode that wrote zero
    Chinese characters over 70 audibly Mandarin cells. So the field cannot be
    `None`, the request always serialises it, and the sidecar's single
    `transcribe()` call carries it as a keyword -- read from the AST, because the
    interpreter that can execute that call is not the one running this suite.
    """
    req = EngineRequest(audio="x.aac")
    assert req.multilingual is True
    assert '"multilingual": true' in req.to_line()
    assert EngineRequest.from_line(req.to_line()).multilingual is True

    with pytest.raises(contract.ContractError):
        EngineRequest(audio="x.aac", multilingual=None)  # type: ignore[arg-type]

    tree = ast.parse(SIDECAR_SRC.read_text(encoding="utf-8"))
    (call,) = [n for n in ast.walk(tree)
               if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Attribute) and n.func.attr == "transcribe"]
    assert "multilingual" in {k.arg for k in call.keywords}


def test_a_pinned_language_and_multilingual_cannot_be_built_together():
    """E-3(f) at the wire: no caller can construct the combination.

    Both halves, because a check that only refuses would also pass if it refused
    every pinned language: pinning with the flag OFF must still be buildable --
    that is A-15, the route section 6.3's numbers are re-run through.
    """
    with pytest.raises(contract.ContractError, match="E-3"):
        EngineRequest(audio="x.aac", language="zh")
    pinned = EngineRequest(audio="x.aac", language="zh", multilingual=False)
    assert (pinned.language, pinned.multilingual) == ("zh", False)
    assert EngineRequest(audio="x.aac").language is None


def test_the_no_gpu_code_is_a_capability_answer_not_a_message_match():
    """A-22 as a property of the source, because the interpreter here cannot run it.

    `EngineFailure.CODES` is a set of things the parent OBSERVED, and the rule that
    keeps it honest is that no code may be a theory about what went wrong. A
    classifier that greps the exception's wording is exactly such a theory: it
    passes today and mislabels the day the library rewrites a sentence, and it
    would put a specific cause in front of a person on no evidence.

    So the assertion is structural. The classifier must ASK ctranslate2 which
    compute types this machine supports, and must not touch the exception's text
    at all -- and the failure handler must actually call it, or the whole thing is
    a function nothing reaches. The dynamic control is
    `test_the_real_child_says_which_failure_this_is_rather_than_crashed`.
    """
    src = SIDECAR_SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fns = [n for n in tree.body
           if isinstance(n, ast.FunctionDef) and n.name == "compute_unsupported"]
    assert len(fns) == 1, "one classifier, or this check is guessing"

    # The DOCSTRING is excluded, and for R8's reason: prose that explains a
    # boundary must be allowed to name it. The rule binds what executes.
    statements = [s for s in fns[0].body
                  if not (isinstance(s, ast.Expr)
                          and isinstance(s.value, ast.Constant)
                          and isinstance(s.value.value, str))]
    body = "\n".join(ast.get_source_segment(src, s) or "" for s in statements)

    assert "get_supported_compute_types" in body, (
        "the child must ask what this machine supports; that is what makes the "
        "code an observation")
    for tell in ("str(exc", ".args", "message", "startswith", ".lower()"):
        assert tell not in body, (
            f"{tell!r} reads the exception's wording. A code derived from a "
            f"message is a theory about what went wrong (INV-14)")

    called = [n for h in ast.walk(tree) if isinstance(h, ast.ExceptHandler)
              for n in ast.walk(h)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and n.func.id == "compute_unsupported"]
    assert called, "the failure handler must call it, or the code is never emitted"


def test_the_stdin_watcher_starts_after_the_model_is_loaded():
    """The regression guard for the stall, and it is a source-order assertion.

    Measured, reproducible, and it was recorded as *unexplained* for a whole design
    round: `WhisperModel(...)` never returns if a thread in the child is blocked
    reading `sys.stdin` while the parent still holds that pipe open
    (`docs/evidence/sidecar_stdin_deadlock_2026-09-09.txt`, six cells, two-sided).
    Every other combination loads in ~3.2 s, including the two the earlier evidence
    suspected: a pipe on stdout, and a heartbeat thread writing during the load.

    The dynamic control is `test_one_real_decode_through_the_real_sidecar`, which
    hung for 190 s before this was fixed. This assertion is the fast one, and it is
    static because moving one line back above the load would reintroduce a hang
    that only the ASR interpreter can demonstrate.
    """
    src = SIDECAR_SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)

    def line_of(pred) -> int:
        return min(n.lineno for n in ast.walk(tree) if pred(n))

    model_built = line_of(lambda n: isinstance(n, ast.Call)
                          and isinstance(n.func, ast.Name)
                          and n.func.id == "WhisperModel")
    # `threading.Thread(target=watch_stdin, ...).start()` -- the keyword is on the
    # Thread call, not on `.start()`, so this looks for the constructor.
    watcher_started = [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and getattr(n.func, "attr", getattr(n.func, "id", None)) == "Thread"
        and any(k.arg == "target" and isinstance(k.value, ast.Name)
                and k.value.id == "watch_stdin" for k in n.keywords)]
    assert watcher_started, "the sidecar must read stdin at some point (cancel)"
    assert min(watcher_started) > model_built, (
        f"the stdin watcher starts on line {min(watcher_started)}, before the model "
        f"is built on line {model_built}. That deadlocks the load: see "
        f"docs/evidence/sidecar_stdin_deadlock_2026-09-09.txt")
    # and something must cover the load window, or an orphaned child decodes for
    # nobody -- during the load there is no stdin EOF to notice
    assert "watch_parent" in src and "getppid" in src

    # Known-false control: the same predicate over the code as it was, which hung.
    # A source-order check that has only ever seen correct order is not known to be
    # an order check.
    was = ast.parse("threading.Thread(target=watch_stdin, daemon=True).start()\n"
                    "m = WhisperModel(path, device='auto')\n")
    built = min(n.lineno for n in ast.walk(was) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name) and n.func.id == "WhisperModel")
    started = min(n.lineno for n in ast.walk(was) if isinstance(n, ast.Call)
                  and getattr(n.func, "attr", getattr(n.func, "id", None)) == "Thread"
                  and any(k.arg == "target" and isinstance(k.value, ast.Name)
                          and k.value.id == "watch_stdin" for k in n.keywords))
    assert started < built, "the control must reproduce the defective order"


def test_the_sidecars_slot_grid_equals_the_products():
    """Two copies of one arithmetic, pinned. The docstrings on both promise this.

    `sidecar.py` re-declares the 30 s grid because it runs under an interpreter that
    does not have the product on its path -- the same reason it re-declares the event
    names. A re-declaration no test pins is not a copy, it is a second definition,
    and this pair is the one that matters most: the audio side arriving with a
    different slot count is exactly the partial pass `langmap.slots()` refuses, so a
    drift here does not produce a wrong label, it produces a refused file.

    Exhaustive over the tail (rung 1: the interesting domain is one slot wide and the
    rest is multiplication), with the two ends of `MIN_SLOT` and the baseline's own
    2183.68 s / 73 among the cases. The known-false control is the rule this was
    written as first: plain `ceil`, which agrees on every whole multiple and on every
    tail over a second, and disagrees exactly where the probe skipped a short tail.
    """
    import math
    from ltm.engine import sidecar
    from ltm.detect import langmap

    assert (sidecar.SLOT_SECONDS, sidecar.MIN_SLOT_SECONDS) == (
        langmap.SLOT, langmap.MIN_SLOT)

    def samples(seconds: float) -> int:
        return int(round(seconds * sidecar.SAMPLE_RATE))

    durations = [2183.68, 30.0, 60.0, 1.0, 29.9, 30.5, 31.0, 90.0 + 0.999]
    durations += [60.0 + t for t in (0.0, 0.01, 0.5, 0.999, 1.0, 1.5, 29.9)]
    disagreed_with_ceil = 0
    for seconds in durations:
        want = langmap.slot_count(seconds)
        assert sidecar.slot_count(samples(seconds)) == want, seconds
        if math.ceil(seconds / langmap.SLOT) != want:
            disagreed_with_ceil += 1
    assert langmap.slot_count(2183.68) == 73
    assert disagreed_with_ceil >= 3, (
        "the control found no case separating this rule from plain `ceil`, so the "
        "test above would pass against the version that broke the probe's grid")


def test_negative_control_the_ast_check_can_fail():
    """The known-false control for the check above.

    The real file passes, so the check would look identical if the predicate were
    broken. It is therefore run against a call that omits the keyword.
    """
    bad = ast.parse("wm.transcribe(path, language='zh', beam_size=5)\n")
    call = next(n for n in ast.walk(bad) if isinstance(n, ast.Call))
    assert "condition_on_previous_text" not in {k.arg for k in call.keywords}
    assert "multilingual" not in {k.arg for k in call.keywords}


def test_the_request_records_what_was_sent(audio, weights, tmp_path):
    """"Not passed" and "passed False" stay distinguishable end to end."""
    req = request_for("stdout", audio, weights, tmp_path,
                      condition_on_previous_text=True)
    result, _ = drive(req, stub={"segments": 2, "gap": 0.05})
    assert result.condition_on_previous_text is True
    req2 = request_for("stdout", audio, weights, tmp_path)
    result2, _ = drive(req2, stub={"segments": 2, "gap": 0.05})
    assert result2.condition_on_previous_text is False


def test_the_result_carries_the_childs_multilingual_not_the_requests(
        audio, weights, tmp_path):
    """E-3(c): both values cross the wire, and a silent child reads as `None`.

    The third arm is the known-false control: the request still says `True`, the
    child says nothing, and the result must NOT borrow the request's value -- a
    runner that copied the request would pass the first two arms unchanged.
    """
    on = request_for("stdout", audio, weights, tmp_path)
    off = request_for("stdout", audio, weights, tmp_path, multilingual=False)
    result_on, _ = drive(on, stub={"segments": 2, "gap": 0.05})
    result_off, _ = drive(off, stub={"segments": 2, "gap": 0.05})
    assert result_on.multilingual is True
    assert result_off.multilingual is False
    silent, _ = drive(on, stub={"segments": 2, "gap": 0.05,
                                "silent_multilingual": True})
    assert on.multilingual is True and silent.multilingual is None


# --- E-2: vad_kept's three branches -------------------------------------

def test_vad_kept_contract(audio, weights, tmp_path):
    """E-2, and the restraint is the contract.

    (a) VAD ran and reported more seconds than the audio has -> the run fails and
    names the excess, **never rounded down to something plausible**; that shape
    reached a user in the upstream product and was believed for a while.
    (b) VAD did not run -> `vad_kept` is not consumed at all, so the same absurd
    number is not a defect here. The measured case was exactly 10.00 s over, on a
    `--no-vad` run: a value something fills in, not a measurement.
    (c) the engine said nothing -> also not consumed, and not the same fact as (b).
    """
    # (a)
    req = request_for("stdout", audio, weights, tmp_path)
    with pytest.raises(runner.EngineError) as e:
        drive(req, stub={"segments": 2, "gap": 0.05,
                         "vad_enabled": True, "vad_kept": 13.0})
    assert e.value.failure.code == "vad-kept-impossible"
    assert e.value.failure.detail["excess"] == 10.0

    # (b) the same impossible number, with VAD off: not a defect
    result, _ = drive(request_for("stdout", audio, weights, tmp_path, vad=False),
                      stub={"segments": 2, "gap": 0.05,
                            "vad_enabled": False, "vad_kept": 13.0})
    assert result.vad_enabled is False and result.vad_kept == 13.0

    # (c) nothing reported
    result, _ = drive(request_for("stdout", audio, weights, tmp_path),
                      stub={"segments": 2, "gap": 0.05,
                            "vad_enabled": None, "vad_kept": None})
    assert result.vad_enabled is None
    assert runner.check_vad(result, 3.0) is None


def test_vad_kept_is_compared_at_the_resolution_it_is_measured_at(
        audio, weights, tmp_path):
    """E-2(a) without its false positive, both sides on the same gate.

    Measured 2026-10-27 on a 24.6-minute recording of continuous speech: VAD kept
    the whole file as one chunk, the engine counted 23,577,728 samples at 16 kHz =
    1473.608 s, ffprobe read 1473.607982 s, and the run failed on an 18 us
    "excess". Scaled onto the stub's 3.0 s: the same rounding must pass, and
    anything past `VAD_KEPT_RESOLUTION` -- including E-2's 10.00 s -- must not.
    """
    rounding = 1473.608 - 1473.607982
    assert rounding < runner.VAD_KEPT_RESOLUTION
    result, _ = drive(request_for("stdout", audio, weights, tmp_path),
                      stub={"segments": 2, "gap": 0.05, "vad_enabled": True,
                            "vad_kept": 3.0 + rounding})
    assert result.vad_kept == pytest.approx(3.0 + rounding)

    for excess in (runner.VAD_KEPT_RESOLUTION * 2, 1.0, 10.0):
        with pytest.raises(runner.EngineError) as e:
            drive(request_for("stdout", audio, weights, tmp_path),
                  stub={"segments": 2, "gap": 0.05, "vad_enabled": True,
                        "vad_kept": 3.0 + excess})
        assert e.value.failure.code == "vad-kept-impossible"
        assert e.value.failure.detail["excess"] == round(excess, 2)


# --- §5.5.1: the language-slot pass, on the wire ------------------------

def test_the_audio_side_arrives_in_order_with_the_engines_own_codes(
        audio, weights, tmp_path):
    """The second signal reaches the parent as DATA, one `RawSlot` per slot.

    `detect` may import `core` only, so the audio side cannot be measured there --
    the engine measures it and hands it over. The codes stay the ENGINE's: `ms` for
    English speech is a measured fact about this recording, and `detect.langmap`
    owns the alias in one place. A runner that normalised `ms` here would put the
    alias in two places, and the second one would be the one nobody tests.
    """
    req = request_for("stdout", audio, weights, tmp_path)
    result, _ = drive(req, stub={"segments": 2, "gap": 0.05,
                                 "slot_langs": ["zh", "ms", "zh"]})
    assert [s.language for s in result.slots] == ["zh", "ms", "zh"]
    assert [s.index for s in result.slots] == [0, 1, 2]
    assert [s.start for s in result.slots] == [0.0, 30.0, 60.0]
    assert all(0.0 <= s.probability <= 1.0 for s in result.slots)
    assert result.language_slots is True


def test_the_slot_pass_reports_a_denominator(audio, weights, tmp_path):
    """R13 / A-7: 11% of the run may not be a spinner.

    Measured: the pass costs 21.7 s against a 189.8 s decode on the baseline, and
    the user's complaint about the upstream product was 「更多時間是空等」. It has a
    denominator the moment it starts -- the slot count is arithmetic on the
    duration -- so a phase with `total=None` here would be a choice, not a limit.

    The known-false control is `load-model`, which legitimately has none: a phase
    list where every phase reports a total would prove nothing about this one.
    """
    req = request_for("stdout", audio, weights, tmp_path)
    _result, events = drive(req, stub={"segments": 2, "gap": 0.05, "slots": 3})
    slots = [ev for _t, ev in events if ev.phase == "language-slots"]
    assert slots, "the stub must run the pass, or this asserts nothing"
    assert all(ev.total == 3.0 for ev in slots)
    assert all(ev.unit == "slots" for ev in slots)
    assert [ev.done for ev in slots] == [0.0, 1.0, 2.0, 3.0]
    assert max(gaps([e for e in events if e[1].phase == "language-slots"])
               or [0.0]) < MAX_GAP_SECONDS
    assert any(ev.total is None for _t, ev in events if ev.phase == "load-model")


def test_asked_and_delivered_nothing_is_not_never_asked(audio, weights, tmp_path):
    """E-1's shape applied to this pass: an empty tuple has two causes.

    「沒問」 and 「問了但它什麼都沒給」 are different facts, and an empty
    `EngineResult.slots` is both of them. So the CHILD reports what it did, exactly
    as it reports `condition_on_previous_text` -- the parent's own request is not
    evidence that the child honoured it, which is precisely the assumption E-1 was.
    A caller must also not read either case as 「這段分不出來」: that is BR-4's
    `undetermined` and it is a third fact again.
    """
    common = {"segments": 2, "gap": 0.05}

    # asked, delivered
    got, _ = drive(request_for("stdout", audio, weights, tmp_path), stub=common)
    assert got.language_slots is True and got.slots != ()

    # asked, delivered nothing -- the child says it ran
    empty, _ = drive(request_for("stdout", audio, weights, tmp_path),
                     stub={**common, "no_slots": True})
    assert empty.language_slots is True and empty.slots == ()

    # never asked -- the same empty tuple, a different report
    off, _ = drive(request_for("stdout", audio, weights, tmp_path,
                               language_slots=False), stub=common)
    assert off.language_slots is False and off.slots == ()
    assert '"language_slots": false' in request_for(
        "stdout", audio, weights, tmp_path, language_slots=False).to_line()


def test_cancel_between_slots_is_answered(audio, weights, tmp_path):
    """R13: `language-slots` is inside the cancellable window, and it is polled.

    §8.2 names two poll points -- between segments and between slots -- because the
    pass runs after the decode loop and a cancel arriving during it would otherwise
    wait out every remaining slot. The stub polls in the same two places the real
    sidecar does.
    """
    # The stub's event order is fixed: 3 for `load-model`, 2 for `transcribe-call`,
    # then `phase decode` + 2 segments, then `phase language-slots` + one event per
    # slot. So the tenth event is the FIRST slot, and asking to cancel there lands
    # the request inside the pass rather than in the decode loop.
    stub = {"segments": 2, "gap": 0.05, "slots": 8}
    cancellation = Cancellation()
    with pytest.raises(runner.EngineError) as e:
        drive(request_for("stdout", audio, weights, tmp_path), stub=stub,
              cancellation=cancellation, cancel_after=10)
    assert e.value.failure.code == "cancelled"
    assert e.value.failure.phase == "language-slots", (
        "the cancel was answered somewhere else, so this test is not about the "
        "poll point it names")

    # The known-false control for that assertion: the same call cancelling at event
    # six, which is `phase decode`. A phase field that reported one value whatever
    # happened would pass the check above and mean nothing.
    early = Cancellation()
    with pytest.raises(runner.EngineError) as e2:
        drive(request_for("stdout", audio, weights, tmp_path), stub=stub,
              cancellation=early, cancel_after=6)
    assert e2.value.failure.phase == "decode"


# --- the denominator comes from the probe, never from the engine ---------

def test_the_decode_denominator_comes_from_ffprobe(audio, weights, tmp_path,
                                                   monkeypatch):
    """§8.1 and `core.model.Recording`'s own note, and it was a paragraph until now.

    Nothing in the product ran `ffprobe`, so a request with no `duration_seconds`
    -- which is every CLI invocation -- let the child fall back to `info.duration`,
    the engine's own number. E-2 has that number on record **10.00 s longer than the
    audio** on a `--no-vad` run, so the bar it feeds can pass 100%.

    The known-true side needs a real media file, so it uses the 60-second clip when
    it is there and skips NAMING the variable when it is not (INV-17: a smaller
    green would hide the missing check). The rest of the test needs no media at all.
    """
    clip = pathlib.Path(os.environ.get("LTM_CLIP60", r"D:\output\clip60-0907.aac"))
    if clip.is_file():
        seconds = runner.probe_duration(str(clip))
        assert 59.0 < seconds < 61.0, seconds
    else:
        pytest.skip("LTM_CLIP60 is not set and the default clip is absent, so the "
                    "known-true side of the probe was not exercised")

    # Known-false (a): the probe is not installed. A fallback here would be the
    # silent downgrade this whole function exists to refuse.
    monkeypatch.setattr(runner, "FFPROBE", "ffprobe-that-is-not-installed")
    with pytest.raises(runner.EngineError) as missing:
        runner.probe_duration(str(clip))
    assert missing.value.failure.code == "probe-missing"

    # Known-false (b): the probe runs and the file is not media.
    monkeypatch.undo()
    with pytest.raises(runner.EngineError) as bad:
        runner.probe_duration(str(audio))          # 41 bytes of ASCII
    assert bad.value.failure.code == "probe-failed"

    # ...and `run` fills the request in rather than leaving the child to guess. The
    # child is the stub, which reports `duration` 3.0; the probe says 60, and the
    # result must carry the probe's number.
    req = request_for("stdout", clip, weights, tmp_path, duration_seconds=None)
    assert req.duration_seconds is None
    result, _ = drive(req, stub={"segments": 2, "gap": 0.05, "duration": 3.0})
    assert result.duration is not None and 59.0 < result.duration < 61.0


# --- normalisation ------------------------------------------------------

def test_windows_are_runs_in_time_order(audio, weights, tmp_path):
    """PIM §4.1.4: runs, not distinct tuples.

    The baseline has 79 runs and 77 distinct tuples; counting distinct silently
    shrinks every denominator by 2. The stub emits two cues with one metric tuple
    and one with another, so a distinct-tuple count would agree here -- the
    assertion that separates them is the third cue repeating the first tuple.
    """
    req = request_for("stdout", audio, weights, tmp_path, duration_seconds=4.0)
    result, _ = drive(req, stub={"segments": 4, "gap": 0.05})
    windows = runner.windows_of(result)
    assert [w.id for w in windows] == ["w0", "w1"]
    assert windows[0].start == 0.0 and windows[0].end == 2.0
    assert windows[1].temperature == 1.0


def test_build_recording_needs_stretches(audio, weights, tmp_path):
    """A `Cue` cannot exist without a stretch id (INV-1), and a `Stretch` cannot be
    invented here without inventing its `agreement`."""
    from ltm.core import model
    req = request_for("stdout", audio, weights, tmp_path, duration_seconds=4.0)
    result, _ = drive(req, stub={"segments": 4, "gap": 0.05})

    with pytest.raises(contract.ContractError, match="no stretches"):
        runner.build_recording(result, id="r", path="p", sha256="0" * 64,
                              stretches=())

    stretches = (model.Stretch(id="s0", start=0.0, end=4.0, language="zh",
                               agreement=0.9),)
    rec = runner.build_recording(result, id="r", path="p", sha256="0" * 64,
                                 stretches=stretches, duration=4.0)
    assert len(rec.cues) == 4 and all(c.stretch_id == "s0" for c in rec.cues)
    assert rec.report.degenerated is True          # the stub anneals to 1.0
    assert {c.window_id for c in rec.cues} == {"w0", "w1"}


def test_a_cue_belongs_to_the_window_its_segment_opened(audio, weights, tmp_path):
    """Rung 0 (one scenario). A cue's window is the run its OWN segment is in.

    Real segments touch: one ends at the instant the next begins. A lookup by
    start time that is inclusive at both ends hands the first cue of every run to
    the previous run, so S4 reads the neighbour's temperature and log-probability
    for exactly the cue where the decoder's state changed. Measured on the seven
    stored runs (2026-10-14): 9 to 124 cues per run misassigned -- every window
    boundary but one -- and on a 108-minute lecture the only temperature-1.0
    segment sat in a window that owned zero cues. The set-equality assertion
    above cannot see it: the stub's fourth cue still lands in w1.
    Control, by value: before the fix this list read ["w0", "w0", "w0", "w1"].
    """
    from ltm.core import model
    req = request_for("stdout", audio, weights, tmp_path, duration_seconds=4.0)
    result, _ = drive(req, stub={"segments": 4, "gap": 0.05})
    stretches = (model.Stretch(id="s0", start=0.0, end=4.0, language="zh",
                               agreement=0.9),)
    rec = runner.build_recording(result, id="r", path="p", sha256="0" * 64,
                                 stretches=stretches, duration=4.0)
    # The stub's segments are contiguous (i, i+1) and anneal from the third on.
    assert [c.window_id for c in rec.cues] == ["w0", "w0", "w1", "w1"]
    temps = {w.id: w.temperature for w in rec.windows}
    assert [temps[c.window_id] for c in rec.cues] == [0.0, 0.0, 1.0, 1.0]
    # Every window owns at least the segment that opened it.
    assert {c.window_id for c in rec.cues} == {w.id for w in rec.windows}


# --- the real sidecar, PSM section 12 -----------------------------------

@pytest.mark.real_sidecar
def test_one_real_decode_through_the_real_sidecar():
    """PSM §12 / round 8 RH-3. A stub cannot reproduce a stall in a real interpreter.

    Every other test in this file speaks to `stub_sidecar.py` under the test
    interpreter -- right, because that is what makes the channel assertions
    deterministic. But the one failure this project has on record as *unexplained*
    is a real child that emits nothing when spawned from a Python parent, and no
    stub can reproduce it. Without this test a fully green suite could sit beside a
    product that does not decode at all.

    It skips when the interpreter, the weights or the clip are missing, and the
    skip NAMES what was missing (INV-17): the acceptance is then incomplete, not
    green.
    """
    exe = pathlib.Path(contract.sidecar_python())
    if not exe.exists():
        pytest.skip(f"the ASR interpreter is absent: {exe} "
                    f"(set {contract.SIDECAR_PYTHON_VAR}); no real decode ran, so "
                    f"neither channel was exercised end to end")
    weights = pathlib.Path(contract.model_dir())
    if not weights.is_dir():
        pytest.skip(f"the model weights are absent: {weights} "
                    f"(set {contract.MODEL_DIR_VAR})")
    clip = pathlib.Path(os.environ.get("LTM_CLIP60")
                        or r"D:\output\clip60-0907.aac")
    if not clip.exists():
        pytest.skip(f"the 60-second clip is absent: {clip} (set LTM_CLIP60; the "
                    f"ffmpeg line that makes it is in "
                    f"docs/evidence/transcribe_call_cost_2026-09-09.py)")

    # The default arithmetic first. If this machine cannot do it, fall back rather
    # than go red: **the compute type is incidental to what this test is for.** Its
    # point is that a REAL child decodes end to end -- the stall on record is a real
    # interpreter emitting nothing, and no stub reproduces it. A hardware absence is
    # the same class of thing as the missing weights above, and the skips there are
    # what INV-17's shape asks for: a gate that cannot run says so instead of
    # passing, and here it can still run, just not in float16.
    #
    # Deciding that from the child's STDERR is a liberty a test may take and the
    # product may not: `runner` refuses to guess a cause, which is why the file it
    # names has to carry one -- and, until the same day this fallback was written,
    # that file was empty (see `test_the_file_a_failure_names_has_something_in_it`).
    seen: list = []
    arithmetic = "float16"
    try:
        # `multilingual=False`: a pinned language requires it (E-3(f)), and it is
        # the configuration every recorded run of this test used.
        result = runner.run(EngineRequest(audio=str(clip), language="zh",
                                          multilingual=False,
                                          duration_seconds=60.0,
                                          heartbeat_seconds=0.4),
                            on_progress=seen.append)
    except runner.EngineError as e:
        # Decided on the CODE now, not by reading the child's stderr for a phrase
        # (A-22). The product itself gained that answer, so a test that still
        # matched on prose would be exercising a path nothing else takes.
        if e.failure.code != "compute-unsupported":
            raise
        seen.clear()
        arithmetic = "int8"
        result = runner.run(EngineRequest(audio=str(clip), language="zh",
                                          multilingual=False,
                                          duration_seconds=60.0,
                                          heartbeat_seconds=0.4,
                                          compute_type="int8", device="cpu"),
                            on_progress=seen.append)

    assert result.segments, "the real engine produced no segments"
    assert result.condition_on_previous_text is False, "E-1, end to end"
    phases = {e.phase for e in seen}
    assert {"load-model", "transcribe-call", "decode"} <= phases
    worst = max((b.elapsed - a.elapsed for a, b in zip(seen, seen[1:])), default=0.0)
    assert worst <= MAX_GAP_SECONDS, f"worst real gap {worst:.2f}s"
    print(f"\n[real sidecar] {arithmetic}: {len(result.segments)} segments, "
          f"load {result.model_load_seconds}s, call {result.call_seconds}s, "
          f"{len(seen)} progress events, worst gap {worst:.2f}s"
          + ("" if arithmetic == "float16" else
             "  -- float16 was refused by this machine, so the DEFAULT "
             "configuration is NOT what ran"))


@pytest.mark.real_sidecar
def test_the_real_child_says_which_failure_this_is_rather_than_crashed():
    """A-22's positive control, and it can be PRODUCED rather than waited for.

    The defect that created this code appeared when the GPU vanished mid-session
    -- not something a test can arrange. `float16` on `device="cpu"` is: every CPU
    backend refuses it, on every machine, so the same failure is available on
    demand and the classifier is exercised for real rather than through a stub
    that was told the answer.

    The claim is not that it fails. It is that the child names WHICH failure with
    a code the parent may act on, so the CLI can ask a person a question instead
    of sending them to a file. The known-false control is the test above: the same
    child on the same machine, an arithmetic it can do, and no such code.
    """
    exe = pathlib.Path(contract.sidecar_python())
    if not exe.exists():
        pytest.skip(f"the ASR interpreter is absent: {exe} "
                    f"(set {contract.SIDECAR_PYTHON_VAR})")
    weights = pathlib.Path(contract.model_dir())
    if not weights.is_dir():
        pytest.skip(f"the model weights are absent: {weights} "
                    f"(set {contract.MODEL_DIR_VAR})")
    clip = pathlib.Path(os.environ.get("LTM_CLIP60")
                        or r"D:\output\clip60-0907.aac")
    if not clip.exists():
        pytest.skip(f"the 60-second clip is absent: {clip} (set LTM_CLIP60)")

    with pytest.raises(runner.EngineError) as e:
        runner.run(EngineRequest(audio=str(clip), language="zh",
                                 multilingual=False,
                                 duration_seconds=60.0, heartbeat_seconds=0.4,
                                 compute_type="float16", device="cpu"))
    assert e.value.failure.code == "compute-unsupported"
    # It failed while LOADING, and says so -- this used to report `during decode`
    # for every failure, which sends the reader to the wrong half of the run.
    assert e.value.failure.phase == "load-model"
    named = pathlib.Path(e.value.failure.stderr_path)
    assert named.read_text("utf-8").strip(), "and the file it names still has the detail"


# --- I-28: a language set; I-27: VAD knobs ---------------------------------

def test_a_language_set_crosses_the_wire_and_refuses_a_pin():
    """I-28 at the wire. The set round-trips (JSON list -> tuple), and it is
    refused beside a pin or with per-window detection off -- the positive half is
    the plain set, which must build, so the refusal is not "refuse every set"."""
    req = EngineRequest(audio="x.aac", language_set=("en", "zh"))
    assert EngineRequest.from_line(req.to_line()).language_set == ("en", "zh")
    assert EngineRequest(audio="x.aac").language_set is None
    with pytest.raises(contract.ContractError, match="I-28"):
        EngineRequest(audio="x.aac", language_set=("en",), language="en",
                      multilingual=False)
    with pytest.raises(contract.ContractError, match="I-28"):
        EngineRequest(audio="x.aac", language_set=("en",), multilingual=False)
    with pytest.raises(contract.ContractError):
        EngineRequest(audio="x.aac", language_set=())


def test_vad_knobs_are_overrides_only_and_need_vad():
    """I-27. No knob set -> `None`, the library's own VadOptions, so every
    earlier decode is the same request it was. A knob with VAD off is refused:
    one of the two asks would be silently ignored."""
    assert EngineRequest(audio="x.aac").vad_parameters() is None
    req = EngineRequest(audio="x.aac", vad_threshold=0.3, vad_speech_pad_ms=800)
    assert req.vad_parameters() == {"threshold": 0.3, "speech_pad_ms": 800}
    assert EngineRequest.from_line(req.to_line()).vad_threshold == 0.3
    with pytest.raises(contract.ContractError):
        EngineRequest(audio="x.aac", vad=False, vad_threshold=0.3)
    for bad in (0.0, 1.0, -0.1):
        with pytest.raises(contract.ContractError):
            EngineRequest(audio="x.aac", vad_threshold=bad)
    with pytest.raises(contract.ContractError):
        EngineRequest(audio="x.aac", vad_min_silence_ms=-1)


def test_the_sidecar_passes_vad_parameters_to_transcribe():
    """Read from the AST, like E-1: the interpreter that can run the call is not
    this one. Control: the keyword set this asserts on is the one call's."""
    tree = ast.parse(SIDECAR_SRC.read_text(encoding="utf-8"))
    (call,) = [n for n in ast.walk(tree)
               if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Attribute) and n.func.attr == "transcribe"]
    kwargs = {k.arg for k in call.keywords}
    assert "vad_parameters" in kwargs and "vad_filter" in kwargs


class _FakeWhisper:
    """The shape of ctranslate2's Whisper.detect_language: per batch item, a list
    of (`<|code|>`, probability) sorted best-first."""
    is_multilingual = True

    def detect_language(self, encoder_output):
        return [[("<|ms|>", 0.6), ("<|en|>", 0.3), ("<|zh|>", 0.1)],
                [("<|ja|>", 0.5), ("<|zh|>", 0.4), ("<|en|>", 0.1)]]


def test_the_language_set_narrows_the_models_own_answer():
    """I-28's mechanism, rung 0 with both controls on the asserted value.

    I-29's measured defect is English lecture written as Malay (`ms`). Unwrapped,
    the top language of item 0 is `ms` (known-false: the defect); narrowed to
    {en, zh} it is `en` and item 1's is `zh`. Probabilities are the model's own,
    not renormalised, and anything else passes through."""
    from ltm.engine import sidecar
    inner = _FakeWhisper()
    assert inner.detect_language(None)[0][0][0] == "<|ms|>"
    narrowed = sidecar.LanguageSetModel(inner, ("en", "zh"))
    got = narrowed.detect_language(None)
    assert [r[0] for r in got] == [("<|en|>", 0.3), ("<|zh|>", 0.4)]
    assert narrowed.is_multilingual is True
