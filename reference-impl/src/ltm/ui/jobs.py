"""One decode at a time, in a child process. PSM section 19, card GUI-1.

The browser face has been read-only since it was built (PSM section 16). This is
the first write path in it, and every decision here is about keeping that path
narrow enough to reason about.

**The engine does not run in the server.** `http.server` in the shape this
product uses it answers one request at a time, so an engine inside it would make
every other route wait out a four-minute decode -- including the cancel the
reader is about to need. The decode is a CHILD: the same `python -m ltm` the
command line runs, the same sidecar under it, talking back through the two flags
that exist for a caller with no terminal (`--progress-file`, `--cancel-file`).

**One job at a time, and a second start is refused rather than queued.** A queue
is a scheduler, and a scheduler is a component this product has not specified. The
refusal names the running job, because "busy" with no id is a dead end for whoever
is holding the other tab.

**The parent never writes the outcome.** `cancel` asks; the state becomes
`cancelling`; it becomes `cancelled` only when the CHILD's own answer says so --
the refusal `cancelled` that `verbs.decode` produces after discarding its partial
output. A child that ignores the request stays `cancelling` for as long as it
keeps running, which is the honest reading and the one R13 is about: the request
and the answer are separate, and a page that says "cancelled" while the engine is
still decoding is the failure that ruling was written against.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

#: File names inside a job's workspace. Both are read by the parent and written
#: by the child; neither is under the store, because a job leaves nothing behind
#: (the decode's own output goes through the verb, into the store, as always).
PROGRESS = "_progress.jsonl"
CANCEL = "_cancel"

#: States a job can be in. `cancelling` exists because the request and the answer
#: are two events -- see the module docstring.
STATES = ("running", "cancelling", "done", "cancelled", "failed")

#: What a finished job reports when the child said nothing a reader could use.
#: Not a guess about why: the exit code travels beside it.
NO_ANSWER = "no-answer"


class Busy(RuntimeError):
    """A second decode was asked for while one is running."""

    def __init__(self, job_id: str) -> None:
        super().__init__(f"a decode is already running ({job_id})")
        self.job_id = job_id


class NoSuchJob(KeyError):
    """An id that was never handed out, or belonged to a previous process."""


def decode_argv(*, root: pathlib.Path, run: str, options: Mapping[str, Any],
                progress: pathlib.Path, cancel: pathlib.Path,
                python: Optional[str] = None) -> list[str]:
    """The child's command line. The default, and the only one the product uses.

    `--json` so the child's ANSWER is a record rather than a sentence: the parent
    reads the outcome off it, which is what makes `cancelled` the child's word
    and not the parent's guess.
    """
    argv = [python or sys.executable, "-m", "ltm",
            "--root", str(root), "--json",
            "--progress-file", str(progress), "--cancel-file", str(cancel),
            "decode", run]
    if options.get("language"):
        argv += ["--language", str(options["language"])]
    if options.get("multilingual") is False:
        argv.append("--no-multilingual")
    if options.get("vad") is False:
        argv.append("--no-vad")
    if options.get("compute_type"):
        argv += ["--compute-type", str(options["compute_type"])]
    if options.get("device"):
        argv += ["--device", str(options["device"])]
    # Never `--if-no-gpu ask`: there is no terminal on the other end of this
    # child, and a prompt nobody can answer is a decode that hangs until the
    # server is killed. `fail` gives the page a code it can show.
    argv += ["--if-no-gpu", "fail"]
    return argv


def child_env() -> dict[str, str]:
    """This process's environment, plus wherever `ltm` was imported from.

    `sys.executable -m ltm` finds the package the way any interpreter would --
    which is not necessarily the way THIS one found it. A checkout puts `src` on
    `sys.path` in process (`conftest.py` does exactly that, and so does anyone
    running from a clone), and `sys.path` is not inherited by a child. The child
    would then exit 2 with "No module named ltm" while the parent that spawned it
    is running that very module.

    So the directory holding the package is prepended to `PYTHONPATH`: if the
    parent can run, the child can. On an installed product this adds a path that
    is already redundant, which costs nothing.
    """
    env = dict(os.environ)
    package_parent = str(pathlib.Path(__file__).resolve().parents[2])
    existing = env.get("PYTHONPATH", "")
    if package_parent not in existing.split(os.pathsep):
        env["PYTHONPATH"] = (package_parent + os.pathsep + existing
                             if existing else package_parent)
    return env


@dataclass
class Job:
    """One decode. Lives in memory and dies with this process, by design."""

    id: str
    run: str
    workspace: pathlib.Path
    proc: subprocess.Popen
    started: float
    options: Mapping[str, Any] = field(default_factory=dict)
    cancel_requested: bool = False
    outcome: Optional[dict[str, Any]] = None      # filled once, on first reap

    @property
    def progress_path(self) -> pathlib.Path:
        return self.workspace / PROGRESS

    @property
    def cancel_path(self) -> pathlib.Path:
        return self.workspace / CANCEL


def last_event(path: pathlib.Path) -> Optional[dict[str, Any]]:
    """The newest COMPLETE line of a progress stream.

    A partial trailing line is ignored rather than repaired: the child appends
    whole lines, so a fragment means the read landed mid-write, and the next poll
    a second later will have it. Guessing at half a record would put a number on
    the page that nothing ever wrote.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None
    lines = [line for line in text.split("\n") if line.strip()]
    if not lines:
        return None
    if not text.endswith("\n"):
        lines = lines[:-1]                        # the fragment
    for line in reversed(lines):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def _answer_in(out: Optional[str]) -> Optional[dict[str, Any]]:
    """The child's response, out of whatever shape it printed it in.

    The WHOLE document first, then the last parseable line. The order matters and
    it was found by running the real product rather than reasoning about it: this
    CLI prints its JSON **pretty**, across several lines, so a reader that took
    the last line got `}` -- and reported a clean refusal as "the child said
    nothing". The stub these tests were written against printed one line per
    answer, which is the shape that suited the parent and hid the defect
    (`test_the_real_child_accepts_this_command_line` is what caught it).

    The line fallback stays, for a child that streams answers rather than
    printing one.
    """
    text = (out or "").strip()
    if not text:
        return None
    try:
        whole = json.loads(text)
        return whole if isinstance(whole, dict) else None
    except json.JSONDecodeError:
        pass
    for line in reversed([l for l in text.split("\n") if l.strip()]):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


class Jobs:
    """The whole job table: at most one entry that matters, plus its history.

    Finished jobs are kept so that the page can still ask what happened to the
    one it was watching; they are not persisted, and an id from a previous
    process is `NoSuchJob` rather than a blank page.
    """

    def __init__(self, root: pathlib.Path, *,
                 argv_for: Callable[..., Sequence[str]] = decode_argv,
                 workspace_root: Optional[pathlib.Path] = None) -> None:
        self.root = pathlib.Path(root)
        self._argv_for = argv_for
        self._workspace_root = workspace_root
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    # --- starting ---------------------------------------------------------
    def running(self) -> Optional[str]:
        for job in self._jobs.values():
            if job.proc.poll() is None:
                return job.id
        return None

    def start(self, run: str, options: Optional[Mapping[str, Any]] = None) -> str:
        options = dict(options or {})
        with self._lock:
            busy = self.running()
            if busy is not None:
                raise Busy(busy)

            job_id = uuid.uuid4().hex[:12]
            base = self._workspace_root or pathlib.Path(tempfile.mkdtemp(
                prefix="ltm-job-"))
            workspace = base / job_id
            workspace.mkdir(parents=True, exist_ok=True)

            argv = list(self._argv_for(
                root=self.root, run=run, options=options,
                progress=workspace / PROGRESS, cancel=workspace / CANCEL))
            proc = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", env=child_env())
            job = Job(id=job_id, run=run, workspace=workspace, proc=proc,
                      started=time.monotonic(), options=options)
            self._jobs[job_id] = job
            return job_id

    # --- watching ---------------------------------------------------------
    def _job(self, job_id: str) -> Job:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise NoSuchJob(job_id) from None

    def _reap(self, job: Job) -> None:
        """Read the child's answer, once, when it has exited.

        The outcome is cached because `communicate` may be called only once and
        because the answer must not change under a reader who polls twice.

        **Nothing drains the pipes while the child runs**, and that is safe only
        because of what this child prints: under `--json` the progress line is
        off and the progress STREAM goes to a file, so stdout carries one answer
        at the end and stderr carries almost nothing. A child that became chatty
        would fill a 64 KB pipe and block mid-decode with the parent reporting
        `running` forever. If that ever changes, the reader moves to a thread --
        it is not a property of this design, it is a property of that child.
        """
        if job.outcome is not None:
            return
        out, err = job.proc.communicate()
        code = job.proc.returncode
        answer = _answer_in(out)

        if code == 0:
            job.outcome = {"state": "done", "code": None, "exitCode": code}
        elif isinstance(answer, dict) and answer.get("code"):
            # The CHILD's word. `cancelled` reaches here only because
            # `verbs.decode` produced it after discarding its partial output --
            # the parent asked and does not get to decide.
            state = "cancelled" if answer["code"] == "cancelled" else "failed"
            job.outcome = {"state": state, "code": answer["code"],
                           "subject": answer.get("subject"), "exitCode": code}
        else:
            job.outcome = {"state": "failed", "code": NO_ANSWER, "exitCode": code,
                           "stderrTail": (err or "").strip()[-2000:]}

    def status(self, job_id: str) -> dict[str, Any]:
        job = self._job(job_id)
        alive = job.proc.poll() is None
        event = last_event(job.progress_path) or {}

        if alive:
            state = "cancelling" if job.cancel_requested else "running"
            outcome: dict[str, Any] = {"state": state, "code": None,
                                       "exitCode": None}
        else:
            self._reap(job)
            outcome = dict(job.outcome or {})

        return {
            "jobId": job.id,
            "run": job.run,
            "state": outcome.pop("state"),
            "phase": event.get("phase"),
            "done": event.get("done"),
            "total": event.get("total"),
            "unit": event.get("unit"),
            "elapsed": event.get("elapsed"),
            "cancelRequested": job.cancel_requested,
            **outcome,
        }

    # --- stopping ---------------------------------------------------------
    def cancel(self, job_id: str) -> dict[str, Any]:
        """Ask. The answer arrives through `status`, from the child.

        Writing the flag is idempotent, and it is all this method does: no
        `terminate`, no state change beyond `cancelRequested`. A parent that
        killed the child would take away the one thing section 8.2 requires --
        the child discarding its own partial output.
        """
        job = self._job(job_id)
        if job.proc.poll() is None:
            job.cancel_requested = True
            job.cancel_path.parent.mkdir(parents=True, exist_ok=True)
            job.cancel_path.write_text("stop\n", encoding="utf-8")
        return self.status(job_id)

    def forget(self, job_id: str) -> None:
        """Drop a finished job and its workspace. Never called on a live one."""
        job = self._job(job_id)
        if job.proc.poll() is None:
            raise Busy(job.id)
        shutil.rmtree(job.workspace, ignore_errors=True)
        self._jobs.pop(job_id, None)

    def shutdown(self) -> None:
        """The server is going away, so the jobs go with it (section 19.0 (6))."""
        for job in list(self._jobs.values()):
            if job.proc.poll() is None:
                job.cancel_requested = True
                try:
                    job.cancel_path.write_text("stop\n", encoding="utf-8")
                except OSError:
                    pass
