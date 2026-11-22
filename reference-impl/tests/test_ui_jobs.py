"""One decode at a time, in a child. PSM section 19, card GUI-1.

The child here is a FAKE -- a `python -c` that writes a progress stream, watches
for a cancel flag, and can be told to obey it, ignore it, crash, or say nothing.
That is deliberate: what this card builds is the parent's half, and a test that
needed the real engine would take four minutes and prove the engine rather than
the job table.

The assertion the whole card rests on is
`test_a_child_that_ignores_the_request_stays_cancelling`. A parent that set
`cancelled` when it wrote the flag would pass every other test in this file and
be exactly the defect R13 was written against -- a page saying "cancelled" while
the engine keeps decoding. So the ignoring child is the control, and it must
leave the state at `cancelling` for as long as it keeps running.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

import pytest

from ltm.ui import jobs

#: A child, in one string. Its argv is the four things it needs to know, so that
#: no Windows path is ever embedded in the source it is handed.
STUB = r'''
import json, pathlib, sys, time
progress, cancel, mode, steps = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
p = pathlib.Path(progress)
p.parent.mkdir(parents=True, exist_ok=True)
c = pathlib.Path(cancel)
for i in range(steps):
    with p.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({
            "state": "decoding", "phase": "decode", "elapsed": round(i * 0.05, 3),
            "done": float(i), "total": float(steps), "unit": "audio-seconds",
            "name": None, "detail": {"cues": float(i)}}) + "\n")
    if mode == "crash" and i * 2 >= steps:
        sys.stderr.write("the child fell over\n")
        sys.exit(3)
    if c.exists() and mode == "obeys":
        print(json.dumps({"verb": "decode", "code": "cancelled",
                          "subject": "decoding"}))
        sys.exit(2)
    if c.exists() and mode == "refuses":
        print(json.dumps({"verb": "decode", "code": "bad-argument",
                          "subject": "applying"}))
        sys.exit(2)
    time.sleep(0.05)
if mode == "mute":
    sys.exit(4)
print(json.dumps({"verb": "decode", "run": "talk-0badc0de", "cues": 3}))
'''


def argv_for(mode: str, steps: int = 40):
    def build(*, root, run, options, progress, cancel, python=None):  # noqa: ARG001
        return [sys.executable, "-c", STUB, str(progress), str(cancel),
                mode, str(steps)]
    return build


def table(tmp_path: pathlib.Path, mode: str, steps: int = 40) -> jobs.Jobs:
    return jobs.Jobs(tmp_path / "store", argv_for=argv_for(mode, steps),
                     workspace_root=tmp_path / "work")


def until(predicate, timeout: float = 20.0, step: float = 0.05):
    """Poll, the way the page will. Returns the last value seen."""
    deadline = time.monotonic() + timeout
    value = predicate()
    while not value and time.monotonic() < deadline:
        time.sleep(step)
        value = predicate()
    return value


# --------------------------------------------------------------------------
# the ordinary life of a job
# --------------------------------------------------------------------------
def test_a_job_runs_reports_progress_and_finishes(tmp_path):
    t = table(tmp_path, "finishes", steps=10)
    job_id = t.start("talk-0badc0de")

    moved = until(lambda: (t.status(job_id)["done"] or 0) > 0)
    assert moved, "no progress reached the parent"
    mid = t.status(job_id)
    assert mid["state"] in ("running", "done")
    assert mid["total"] == 10.0 and mid["unit"] == "audio-seconds"

    assert until(lambda: t.status(job_id)["state"] == "done")
    final = t.status(job_id)
    assert final["exitCode"] == 0 and final["code"] is None
    assert final["jobId"] == job_id and final["run"] == "talk-0badc0de"


def test_the_numerator_actually_moves(tmp_path):
    """R13's other half: progress must be REAL, not a spinner.

    Two reads a moment apart have to differ, or the page is showing a number
    that never changes and the reader learns to ignore it. The control is the
    same pair taken from a finished job, where standing still is correct.
    """
    t = table(tmp_path, "finishes", steps=40)
    job_id = t.start("talk-0badc0de")

    first = until(lambda: t.status(job_id)["done"])
    assert first is not None
    later = until(lambda: (t.status(job_id)["done"] or 0) > first)
    assert later, f"the numerator did not move past {first}"

    assert until(lambda: t.status(job_id)["state"] == "done")
    settled = t.status(job_id)["done"]
    time.sleep(0.2)
    assert t.status(job_id)["done"] == settled


def test_a_second_start_is_refused_and_names_the_running_job(tmp_path):
    """Refused, not queued: a queue is a scheduler and there is no such component.

    The id is in the refusal because "busy" alone is a dead end for whoever is
    holding the other tab -- they cannot look at it, cancel it, or wait for it.
    """
    t = table(tmp_path, "obeys", steps=60)
    first = t.start("talk-0badc0de")
    with pytest.raises(jobs.Busy) as e:
        t.start("other-run")
    assert e.value.job_id == first
    assert first in str(e.value)

    # The known-false side: once nothing is RUNNING, a start is allowed again.
    # Waiting for a state that is merely "not running" would not do -- the first
    # version of this test waited for `cancelling`, which the child reaches
    # while it is still very much alive.
    t.cancel(first)
    assert until(lambda: t.status(first)["state"] in ("cancelled", "done",
                                                      "failed"))
    second = t.start("talk-0badc0de")
    assert second != first


# --------------------------------------------------------------------------
# cancel: the request and the answer are two events
# --------------------------------------------------------------------------
def test_cancel_is_answered_by_the_child(tmp_path):
    t = table(tmp_path, "obeys", steps=60)
    job_id = t.start("talk-0badc0de")
    until(lambda: (t.status(job_id)["done"] or 0) > 0)

    asked = t.cancel(job_id)
    assert asked["cancelRequested"] is True
    assert asked["state"] in ("cancelling", "cancelled")

    assert until(lambda: t.status(job_id)["state"] == "cancelled")
    final = t.status(job_id)
    assert final["code"] == "cancelled", (
        "the state came from somewhere other than the child's own answer")
    assert final["exitCode"] == 2


def test_a_child_that_ignores_the_request_stays_cancelling(tmp_path):
    """The control the whole card rests on.

    A parent that set `cancelled` when it wrote the flag would pass every other
    test here. This child never looks at the flag, so for as long as it runs the
    honest answer is `cancelling` -- the request was made and not yet answered.
    """
    t = table(tmp_path, "finishes", steps=60)
    job_id = t.start("talk-0badc0de")
    until(lambda: (t.status(job_id)["done"] or 0) > 0)

    t.cancel(job_id)
    for _ in range(6):
        state = t.status(job_id)["state"]
        if state != "cancelling":
            assert state in ("done",), (
                f"a child that never read the flag reported {state!r}")
            break
        time.sleep(0.05)
    else:
        assert t.status(job_id)["state"] == "cancelling"

    # and when it finishes on its own terms, it finishes -- not "cancelled"
    assert until(lambda: t.status(job_id)["state"] == "done")
    assert t.status(job_id)["code"] is None


def test_a_refusal_that_is_not_a_cancel_is_not_read_as_one(tmp_path):
    """The other side of the same discrimination.

    The child answers a code after the flag appears, and the code is NOT
    `cancelled`. If the parent treated "cancel was requested and the child then
    stopped" as cancellation, this would come back `cancelled` and the page would
    tell the reader they stopped a run that actually refused.
    """
    t = table(tmp_path, "refuses", steps=60)
    job_id = t.start("talk-0badc0de")
    until(lambda: (t.status(job_id)["done"] or 0) > 0)
    t.cancel(job_id)

    assert until(lambda: t.status(job_id)["state"] in ("failed", "cancelled"))
    final = t.status(job_id)
    assert final["state"] == "failed" and final["code"] == "bad-argument"
    assert final["cancelRequested"] is True, (
        "the request is still recorded; it is the ANSWER that was not a cancel")


def test_cancel_writes_a_flag_and_does_not_kill_anything(tmp_path):
    """Section 8.2 belongs to the child: it discards its own partial output.

    A parent that terminated the child would take that away and leave whatever
    was half-written on disk, so `cancel` writes a file and returns.
    """
    t = table(tmp_path, "finishes", steps=40)
    job_id = t.start("talk-0badc0de")
    until(lambda: (t.status(job_id)["done"] or 0) > 0)

    t.cancel(job_id)
    job = t._job(job_id)                           # noqa: SLF001
    assert job.cancel_path.is_file()
    assert job.proc.poll() is None, "the parent killed the child"

    t.cancel(job_id)                               # idempotent
    assert job.cancel_path.is_file()


# --------------------------------------------------------------------------
# failure is a state, not a blank
# --------------------------------------------------------------------------
def test_a_child_that_dies_surfaces_its_exit_code(tmp_path):
    t = table(tmp_path, "crash", steps=10)
    job_id = t.start("talk-0badc0de")

    assert until(lambda: t.status(job_id)["state"] == "failed")
    final = t.status(job_id)
    assert final["exitCode"] == 3
    assert final["code"] == jobs.NO_ANSWER
    assert "fell over" in final.get("stderrTail", ""), (
        "the child said why on stderr and the parent dropped it")


def test_a_child_that_exits_saying_nothing_is_failed_not_done(tmp_path):
    """Exit 4 with no answer. `done` is reserved for exit 0."""
    t = table(tmp_path, "mute", steps=4)
    job_id = t.start("talk-0badc0de")
    assert until(lambda: t.status(job_id)["state"] == "failed")
    assert t.status(job_id)["exitCode"] == 4


def test_an_id_from_another_process_is_refused_not_blank(tmp_path):
    t = table(tmp_path, "finishes", steps=2)
    with pytest.raises(jobs.NoSuchJob):
        t.status("deadbeefcafe")


# --------------------------------------------------------------------------
# the progress reader
# --------------------------------------------------------------------------
@pytest.mark.parametrize("printed,expected", [
    ('{\n  "verb": "decode",\n  "code": "no-such-run"\n}\n', "no-such-run"),
    ('{"verb": "decode", "code": "cancelled"}\n', "cancelled"),
    ('{"a": 1}\n{"verb": "decode", "code": "engine-failed"}\n', "engine-failed"),
])
def test_the_answer_is_found_whichever_shape_the_child_printed_it_in(printed,
                                                                    expected):
    """Pretty JSON across lines, one line, or several documents.

    The first case is the one that shipped broken for an afternoon: this CLI
    pretty-prints, a reader that took the last line got `}`, and a clean refusal
    was reported as "the child said nothing". The stub in this file printed one
    line per answer -- the shape that suited the parent -- so only the real child
    found it.
    """
    assert jobs._answer_in(printed)["code"] == expected      # noqa: SLF001


def test_no_answer_is_distinguishable_from_a_bad_one():
    """The known-false side: silence really does come back as nothing."""
    assert jobs._answer_in("") is None                       # noqa: SLF001
    assert jobs._answer_in("usage: ltm [-h] ...\n") is None  # noqa: SLF001
    assert jobs._answer_in("[1, 2, 3]\n") is None            # noqa: SLF001


def test_a_half_written_line_is_ignored_rather_than_repaired(tmp_path):
    """The read can land mid-write; the next poll will have the whole line.

    Guessing at half a record would put a number on the page that nothing ever
    wrote, which is worse than one second of staleness.
    """
    p = tmp_path / "_progress.jsonl"
    whole = json.dumps({"phase": "decode", "done": 7.0, "total": 9.0})
    p.write_text(whole + "\n" + '{"phase": "dec', encoding="utf-8")
    assert jobs.last_event(p) == {"phase": "decode", "done": 7.0, "total": 9.0}

    # controls: a complete stream returns its LAST line, and no file is None
    p.write_text(whole + "\n" + json.dumps({"phase": "detect", "done": 1.0})
                 + "\n", encoding="utf-8")
    assert jobs.last_event(p) == {"phase": "detect", "done": 1.0}
    assert jobs.last_event(tmp_path / "nothing.jsonl") is None


def test_the_default_child_is_this_product_and_asks_no_questions(tmp_path):
    """`decode_argv` is what ships; the stub above is only for these tests.

    Two things it must carry, both of which are silent failures if missed:
    `--json`, or the parent has an English sentence to parse instead of the
    child's answer; and `--if-no-gpu fail`, or a machine with no usable GPU
    leaves the child waiting at a prompt nobody can see, forever.
    """
    argv = jobs.decode_argv(
        root=tmp_path, run="talk-0badc0de",
        options={"language": "zh", "multilingual": False},
        progress=tmp_path / PROGRESS_NAME, cancel=tmp_path / "_cancel")
    assert argv[1:3] == ["-m", "ltm"]
    assert "--json" in argv
    assert argv[argv.index("--if-no-gpu") + 1] == "fail"
    assert argv[argv.index("--language") + 1] == "zh"
    assert "--no-multilingual" in argv

    # Order is part of the contract, not decoration: argparse takes the
    # top-level flags BEFORE the sub-command and the decode flags AFTER it, so a
    # command line that carries every right word in the wrong place exits 2 with
    # a usage message the parent would read as "the child said nothing".
    verb = argv.index("decode")
    assert argv[verb + 1] == "talk-0badc0de"
    assert argv.index("--json") < verb < argv.index("--if-no-gpu")
    assert verb < argv.index("--language")


PROGRESS_NAME = jobs.PROGRESS


# --------------------------------------------------------------------------
# the real seam: the default child is a command line this product accepts
# --------------------------------------------------------------------------
def test_the_real_child_accepts_this_command_line(tmp_path):
    """No stub, no engine: a real `python -m ltm` that refuses and says why.

    Everything above proves the PARENT against a child written to suit it. What
    nothing above touches is whether `decode_argv` produces a command line the
    actual product parses -- a misplaced flag exits 2 with a usage message, which
    the parent would faithfully report as "the child said nothing".

    Decoding a run that does not exist costs milliseconds and exercises the whole
    seam: the two new flags, the `--json` answer, the exit code, and the parent
    reading the child's own word for what happened.
    """
    t = jobs.Jobs(tmp_path / "store", workspace_root=tmp_path / "work")
    job_id = t.start("no-such-run-0badc0de")

    assert until(lambda: t.status(job_id)["state"] in ("failed", "done"),
                 timeout=60)
    final = t.status(job_id)
    assert final["state"] == "failed"
    assert final["code"] == "no-such-run", (
        f"the child's answer did not come back as a code: {final}")
    assert final["exitCode"] == 2


def test_the_real_child_writes_a_progress_file_the_parent_can_read(tmp_path):
    """The other half of the seam, without an engine.

    `intake` is instant and emits no progress, so this asserts the weaker but
    still load-bearing thing: the flag is accepted, the file is created, and the
    parent's reader survives an empty stream rather than raising. A reader that
    threw on "no events yet" would make the page fail for the first second of
    every decode.
    """
    source = tmp_path / "talk.m4a"
    source.write_bytes(b"not audio; intake only hashes it")

    def intake_argv(*, root, run, options, progress, cancel, python=None):  # noqa: ARG001
        return [sys.executable, "-m", "ltm", "--root", str(root), "--json",
                "--progress-file", str(progress), "--cancel-file", str(cancel),
                "intake", str(source)]

    t = jobs.Jobs(tmp_path / "store", argv_for=intake_argv,
                  workspace_root=tmp_path / "work")
    job_id = t.start("ignored")
    assert until(lambda: t.status(job_id)["state"] != "running", timeout=60)

    status = t.status(job_id)
    assert status["state"] == "done", status
    assert status["phase"] is None and status["done"] is None
    assert t._job(job_id).progress_path.is_file()   # noqa: SLF001
