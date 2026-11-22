"""Two channels, one interface. PSM section 8.3.

The primary channel is stdout for progress and stdin for cancel. The alternative
writes progress to a JSONL file the parent tails and cancels through a sentinel
file. **Both ship**, and `tests/test_engine.py` runs the same three assertions
against each, because the reason the alternative exists is a measured stall that
is **not explained**: the same sidecar script runs fine from a shell and emits
nothing when spawned from a Python parent, with the child sitting at 5 MB RSS
before the interpreter has finished starting
(`docs/evidence/sidecar_streaming_2026-09-09.md` records what was excluded by
measurement -- GPU contention, VAD, network, the parent's read code, the
interpreter under a pipe, stdin as PIPE, the watcher thread, swallowed stderr).

The alternative removes the stdout pipe entirely, which is the path the stall
lives on. **The primary runs first**: if it reproduces the stall in the build
environment, the failure names which channel broke instead of reporting a timeout.

**One thing the alternative makes no better, said here rather than in a
footnote**: a sentinel-file cancel is checked between segments, exactly like the
stdin check, so *neither* channel can answer a cancel inside the `transcribe()`
call. The worst-case latency in §8.1 limit (c) is a property of faster-whisper,
not of the channel.
"""
from __future__ import annotations

import abc
import json
import os
import pathlib
import time
from typing import IO, Iterator, Optional

from .contract import CONTROL_CANCEL, ContractError, EngineRequest, parse_event


class Channel(abc.ABC):
    """How events come back and how a cancel goes out.

    Deliberately not "how the process is started": the runner owns the
    subprocess, so a channel can be exercised against a stub child, a real
    sidecar, or a hand-written file, and the three assertions in §8.1 mean the
    same thing in all three cases.
    """

    name: str

    #: There is deliberately no `child_args()`. The child learns where to write
    #: and what to poll from the REQUEST (`progress_path` / `cancel_path`), which
    #: is one place; an argv copy of the same two paths would be a second, and two
    #: sources of one fact is the drift this project has been blocked for twice.

    @abc.abstractmethod
    def events(self, proc, deadline: Optional[float] = None) -> Iterator[dict]:
        """Yield decoded events until the child is done or the deadline passes."""

    @abc.abstractmethod
    def send_cancel(self, proc) -> None:
        """Ask the child to stop. Must not block and must not raise on a dead child."""

    def close(self) -> None:
        return None


class StdoutChannel(Channel):
    """The primary. Progress on the child's stdout, cancel on its stdin.

    Child death is stdin EOF: closing the pipe is the signal, so a parent that
    dies takes the child with it without a watchdog.
    """

    name = "stdout"

    def events(self, proc, deadline: Optional[float] = None) -> Iterator[dict]:
        assert proc.stdout is not None, "the primary channel needs a stdout pipe"
        for line in proc.stdout:
            if not line.strip():
                continue
            yield parse_event(line)
            if deadline is not None and time.monotonic() > deadline:
                return

    def send_cancel(self, proc) -> None:
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.write(CONTROL_CANCEL + "\n")
                proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass          # the child is already gone; terminate() is the fallback


class FileChannel(Channel):
    """The alternative. Progress tailed from a JSONL file, cancel by sentinel.

    The two paths are handed in by the caller (which may import `store`) because
    `engine/` may import `core` only and must not know the disk vocabulary.

    Tailing costs an extra file and a poll, and it buys the removal of the pipe the
    stall lives on. The poll interval is the caller's heartbeat interval, so the
    channel cannot be the reason limit (a) is missed.
    """

    name = "file"

    def __init__(self, progress_path: str | os.PathLike[str],
                 cancel_path: str | os.PathLike[str],
                 poll_seconds: float = 0.1) -> None:
        self.progress_path = pathlib.Path(progress_path)
        self.cancel_path = pathlib.Path(cancel_path)
        self.poll_seconds = poll_seconds
        self._handle: Optional[IO[str]] = None

    def events(self, proc, deadline: Optional[float] = None) -> Iterator[dict]:
        self.progress_path.parent.mkdir(parents=True, exist_ok=True)
        pending = ""
        while True:
            if self._handle is None and self.progress_path.exists():
                self._handle = open(self.progress_path, "r", encoding="utf-8")
            if self._handle is not None:
                chunk = self._handle.read()
                if chunk:
                    pending += chunk
                    # Only whole lines: a tail can read a line the child has not
                    # finished writing, and half a JSON object is not an error to
                    # report -- it is a line to wait for.
                    *lines, pending = pending.split("\n")
                    for line in lines:
                        if line.strip():
                            yield parse_event(line)
                    continue
            if proc.poll() is not None:
                # The child exited. Drain once more: the last events may have been
                # written between the read above and the exit.
                if self._handle is not None:
                    rest = self._handle.read()
                    for line in (pending + rest).split("\n"):
                        if line.strip():
                            yield parse_event(line)
                return
            if deadline is not None and time.monotonic() > deadline:
                return
            time.sleep(self.poll_seconds)

    def send_cancel(self, proc) -> None:
        try:
            self.cancel_path.parent.mkdir(parents=True, exist_ok=True)
            self.cancel_path.write_text("cancel\n", encoding="utf-8")
        except OSError:
            pass

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


def channel_for(request: EngineRequest) -> Channel:
    """The request decides, so the caller cannot pair a channel with the wrong
    request -- a file channel whose child was never told where to write is a
    channel that reports a stall the product created itself."""
    if request.uses_files:
        assert request.progress_path and request.cancel_path
        return FileChannel(request.progress_path, request.cancel_path,
                           poll_seconds=min(0.1, request.heartbeat_seconds))
    return StdoutChannel()


__all__ = ["Channel", "StdoutChannel", "FileChannel", "channel_for",
           "ContractError"]
