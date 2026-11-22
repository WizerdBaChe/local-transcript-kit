"""`python -m ltm`. The human face of the same verbs the agent calls.

PSM §9 / §11 step 6. **`VERBS` is `verbs.IMPLEMENTATIONS`, not a copy of it** —
`tests/test_agentapi_schema.py::test_both_faces_call_one_function` asserts the
identity, so this module cannot grow its own `decode` that agrees with the API
today and diverges at the first bug fix.

What this face adds, and it is the whole of its job:

* **Sentences.** `core.progress` holds a cancel refusal as `(code, sentence)` and
  `agentapi` returns only the code. Here the sentence is printed. Same for
  `detectors.warnings_for` and for an unverified marker whose population is empty
  — 「0」 alone reads as 「everything passed」, which is the opposite of what an
  empty population means.
* **Progress a person can watch.** One line, rewritten in place, at least once a
  second (INV-22 / R13), and it always carries the phase's own denominator when
  the phase has one. A phase with no denominator says so instead of drawing a bar
  it cannot fill.
* **Ctrl-C is a cancel request, not a kill.** `KeyboardInterrupt` becomes
  `Cancellation.request(state)`, so the run answers within one event interval and
  the reply says whether it was accepted — the user's own complaint was
  「單純反灰然後等待很久」, and a `SIGINT` that leaves a half-written revision would
  be the same defect with a different cause.

Nothing here decides anything. Every refusal, every code and every number comes
from the verb; this module chooses words for them.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
from typing import Any, Mapping, Optional, Sequence

from ..agentapi import schema, verbs
from ..agentapi.schema import ident
from ..core import progress
from ..detect import detectors
from ..store import came_from, layout
from . import agent, read

#: The same objects the agent face dispatches to. Not a copy — see the module
#: docstring and the identity test.
VERBS = verbs.IMPLEMENTATIONS

#: Where the sentence for a refusal code comes from. Built from `core.progress`'s
#: one table rather than restated, so a new refusal cannot reach a person as a bare
#: code: `refusal_sentence` raises on a code this face has no words for.
_SENTENCES: Mapping[str, str] = {
    **{code: text for code, text in progress._REFUSALS.values()},
    progress.FINISHED[0]: progress.FINISHED[1],
}

#: The prose for `schema.ErrorResponse.CODES`. Every code, checked at import time
#: by the loop below: a face that prints "error: grant-missing" has not explained
#: anything, and the codes are a closed set precisely so this table can be total.
_ERRORS: Mapping[str, str] = {
    # Not "adoption is a human action": adoption stopped being one on 2026-09-09
    # (A-21) and this sentence went on saying it did until round 13 read the face
    # instead of the table. The mechanism is still here and still prints, so the
    # words describe whatever verb is in `HUMAN_ONLY` -- which today is none.
    "human-only": "this verb has no agent form; a person must run it. No verb is "
                  "in that set today -- adoption left it on 2026-09-09 (A-21).",
    "unknown-verb": "no such verb.",
    "bad-argument": "the arguments do not fit this verb.",
    "no-such-run": "no run by that name under this root.",
    "no-such-revision": "that revision does not exist, or left no plan behind.",
    "token-layer-absent": "word-level alignment needs a token layer, which "
                          "milestone 1 does not build. Nothing was written.",
    "grant-missing": "cite needs a human authorisation record naming this exact "
                     "revision (R14). Nothing was written.",
    "engine-failed": "the speech engine did not finish. See the stderr file it "
                     "names.",
    "cancelled": "cancelled. Partial output was discarded, so nothing was written.",
}

#: The few refusal SUBJECTS whose code sentence is not enough for a person to act
#: on. Human face only: under `--json` the subject is the whole answer. A subject
#: that is the other half of a flag the person just typed gets named here, because
#: `bad-argument (language-with-multilingual)` tells them what broke and not what to
#: type (PSM section 17.0 (5)).
_SUBJECT_SENTENCES: Mapping[str, str] = {
    "language-with-multilingual":
        "--language pins one language for the whole file, and the default "
        "per-window language detection would silently override it. Add "
        "--no-multilingual to pin, or drop --language.",
    "language-set-with-pin":
        "--languages narrows the per-window detection to a set, so it needs that "
        "detection on and nothing pinned. Drop --language/--no-multilingual, or "
        "drop --languages.",
    "vad-knobs-without-vad":
        "--vad-threshold/--vad-min-silence-ms/--vad-speech-pad-ms tune VAD, and "
        "--no-vad turns it off. Pick one.",
}

#: What the product falls back to when the machine has no usable GPU. `int8` on
#: the processor decoded the same 60-second clip to the same 26 cues as `float16`
#: on the GPU -- slower, same words.
CPU_FALLBACK = ("int8", "cpu")

#: The engine failure a person can answer. It is a PAUSE, not a dead end (A-22,
#: user ruling UAT A-8): the run stopped, the reason is one sentence of ordinary
#: words, and the way forward is a yes.
NO_GPU = "compute-unsupported"

_MISSING = [c for c in schema.ErrorResponse.CODES if c not in _ERRORS]
if _MISSING:                              # at import, not at first failure
    raise RuntimeError(
        f"the CLI has no sentence for error code(s) {_MISSING}. Every code in "
        f"`ErrorResponse.CODES` is something a person can be told; a bare code "
        f"printed at a user is the agent face leaking into the human one.")


def refusal_sentence(code: str) -> str:
    try:
        return _SENTENCES[code]
    except KeyError:                      # a new refusal with no words yet
        raise RuntimeError(
            f"no sentence for refusal code {code!r}. `core.progress` holds one "
            f"table with two projections; a code that reaches here without a "
            f"sentence means the table grew and this face did not.") from None


# --- progress a person can watch -----------------------------------------

#: How long a denominator may sit still before the line says so. Measured: a real
#: 51-minute file held 2712/3065 for 38 seconds while the decoder worked through
#: one long segment. Ten seconds is longer than any ordinary gap between events
#: (the contract is one per second) and shorter than the wait that makes a person
#: reach for Ctrl-C.
STALL_SECONDS = 10.0


class Line:
    """One line, rewritten in place. Not a log.

    A progress bar that scrolls is a log, and a log of 936 detect steps is not
    progress -- it is the wait with a paper trail. `\\r` and no newline until the
    phase changes.
    """

    def __init__(self, stream=None) -> None:
        self.stream = stream or sys.stderr
        self.phase: Optional[str] = None
        self.width = 0
        self.started = 0.0
        self.last_done: Optional[float] = None
        self.last_move = 0.0

    def __call__(self, event: progress.ProgressEvent) -> None:
        if event.phase != self.phase:
            if self.phase is not None:
                self.stream.write("\n")
                self.width = 0
            # `event.elapsed` is measured from the START OF THE RUN, not of the
            # phase: the 36-minute file spends 4 s in `load-model` and ~30 s in
            # `transcribe-call` before `decode` reports its first fraction. An
            # estimate that divided run-elapsed by decode-progress would charge
            # that half minute to every remaining audio-second.
            self.started = event.elapsed
            self.last_done = None
            self.last_move = event.elapsed
        if event.done != self.last_done:
            self.last_done = event.done
            self.last_move = event.elapsed
        self.phase = event.phase
        text = self.render(event, phase_elapsed=event.elapsed - self.started,
                           stalled_for=event.elapsed - self.last_move)
        pad = max(0, self.width - len(text))
        self.stream.write("\r" + text + " " * pad)
        self.stream.flush()
        self.width = len(text)

    @staticmethod
    def render(event: progress.ProgressEvent, phase_elapsed: Optional[float] = None,
               stalled_for: Optional[float] = None) -> str:
        """The denominator when there is one, and the reason when there is not.

        A phase in `NO_DENOMINATOR` prints elapsed seconds and the phase name --
        never a percentage and never a spinner with no number, because the two look
        the same to a user and only one of them is honest about not knowing.

        Two additions, both measured on the 2026-09-10 runs and both refusing to
        appear when they would be invented:

        * **an estimate of what is left**, from this phase's own throughput. The
          numbers were always there -- `1049/3933 audio-seconds 27% 137s` -- and the
          person had to do the division. It says `estimate` in the text because
          that is what it is: throughput measured so far, projected flat. No
          denominator, no estimate, which is INV-22 unchanged.
        * **a stall note.** A 38-second stretch where the numerator did not move
          while the seconds kept ticking was measured on a real file (the decoder
          was inside one long segment). The contract was kept -- an event every
          second -- and it still reads as a hang. Saying "no advance for 38s" is
          the difference between a slow answer and no answer.
        """
        head = f"{event.phase}"
        if event.name:
            head += f" [{event.name}]"
        if event.total is None:
            return f"{head}  {event.elapsed:.0f}s  (no denominator for this phase)"
        done = event.done or 0.0
        pct = 100.0 * done / event.total
        text = (f"{head}  {done:.0f}/{event.total:.0f} {event.unit}"
                f"  {pct:.0f}%  {event.elapsed:.0f}s")
        if phase_elapsed and done > 0 and done < event.total:
            left = (event.total - done) * (phase_elapsed / done)
            text += f"  ~{left:.0f}s left (estimate)"
        if stalled_for is not None and stalled_for >= STALL_SECONDS:
            text += f"  [no advance for {stalled_for:.0f}s]"
        return text

    def done(self) -> None:
        if self.phase is not None:
            self.stream.write("\n")
            self.stream.flush()
            self.phase = None


# --- progress and cancellation for a caller that is not a person ----------
#
# A person watching `Line` gets both halves of R13: the numbers move, and Ctrl-C
# is a cancel request that is answered. An out-of-process caller got NEITHER --
# `--json` turns the progress sink off entirely, and there is no keyboard to
# interrupt. So R4's 「代理可驅動」 stopped at the door of the one verb that takes
# four minutes. These two flags are that door, and the page that starts decodes
# (PSM section 19) is their first caller, not their reason.

#: Fields of a `ProgressEvent` that go into the stream. Everything on that type
#: is already safe to write down -- `name` is an `Identifier` and `detail` holds
#: numbers only (INV-12, enforced where the value is created) -- so this file can
#: never carry a line of transcript. `tests/test_progress_stream.py` asserts it
#: against the type rather than against this tuple.
STREAM_FIELDS = ("state", "phase", "elapsed", "done", "total", "unit", "name")

#: How often the cancel flag is looked for. R13 answers a cancel within one event
#: interval and the contract is one event a second, so the flag must be noticed
#: well inside that -- and the run's own poll points do the rest.
CANCEL_POLL = 0.25


class ProgressStream:
    """One JSON object per line, appended and flushed.

    Opened per event rather than held open, which costs nothing at one event a
    second and means there is no handle to close on any of `main`'s five exits --
    including the two that are refusals. A stream truncated because a `finally`
    was missed would be indistinguishable from a run that produced nothing.
    """

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n"):
            pass                                  # truncate: one run, one stream

    def __call__(self, event: progress.ProgressEvent) -> None:
        row = {f: getattr(event, f) for f in STREAM_FIELDS}
        row["detail"] = dict(event.detail)
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row) + "\n")


class Fan:
    """Every sink, and the last state seen.

    The state is kept because the cancel watcher needs one: `Cancellation.request`
    takes the state the request arrived in, and `ack` decides from it whether the
    cancel is even allowed (INV-13 refuses one during `applying`). Reading it off
    the last event is the same answer the Ctrl-C path gets from `Line.phase`, one
    step closer to the source.
    """

    def __init__(self, sinks: Sequence[Any] = ()) -> None:
        self.sinks = list(sinks)
        self.state = "queued"

    def __bool__(self) -> bool:
        return bool(self.sinks)

    def __call__(self, event: progress.ProgressEvent) -> None:
        self.state = event.state
        for sink in self.sinks:
            sink(event)


def watch_for_cancel(path: pathlib.Path, cancellation: progress.Cancellation,
                     fan: Fan) -> None:
    """Ask the run to stop when `path` appears. Runs on a daemon thread.

    A FILE and not a signal, deliberately. `CTRL_BREAK_EVENT` is the only console
    signal a Windows parent can send a child it did not create as a group, and
    Python's default handler for it terminates rather than raising
    `KeyboardInterrupt` -- so on the platform this product runs on, the signal
    route would kill the child mid-write instead of letting it discard its
    partial output (section 8.2). A file is the same request on both platforms,
    it is observable by anyone debugging, and the child answers it at the poll
    points it already has.
    """
    while True:
        if path.exists():
            cancellation.request(fan.state)
            return
        time.sleep(CANCEL_POLL)


# --- what a person is told about a plan ----------------------------------

def unverified_lines(plan_obj, marker_scope) -> list[str]:
    """T-3 in words: a zero over an empty population SAYS the population is empty.

    「0」 alone reads as 「the gate ran and everything passed」. The truth in
    milestone 1 is that nothing was in scope, and the two sentences are not
    interchangeable -- one is a result and the other is the absence of one. This is
    the human projection of `detectors.unverified_scope`; the agent gets the pair of
    numbers and forms its own opinion.
    """
    out = []
    for marker, (count, population) in sorted(marker_scope.items()):
        if population == 0:
            out.append(f"  {marker}: {count}, and nothing was in scope for this "
                       f"gate in this round (not 0 out of something)")
        else:
            out.append(f"  {marker}: {count} of {population} cue-level ops went "
                       f"unverified")
    return out


#: One sentence per `Note.reason`, because they are three different instructions
#: to the reader and this line used to give all of them the same one: "one signal
#: only, so it is flagged and not proposed for deletion". That was already wrong
#: for `no-action` (a finding that had two signals and nothing to propose) and
#: A-20 made it wrong in a way a user would act on -- a `near-threshold` note is
#: capped BY THE CAP, and the measured case has n == 2. A human-facing sentence
#: that states the wrong reason is worse than no sentence: it answers the
#: question the reader was about to ask.
#:
#: The prose lives here and only here (INV-12): the agent face carries the code.
NOTE_SENTENCE = {
    "single-signal": "one signal only, so it is flagged and not proposed for "
                     "deletion",
    "no-action": "two signals held, but there is no edit to propose -- flagged "
                 "so you can look",
    "near-threshold": "one repetition short of the bar, so this is deliberately "
                      "NOT a finding -- of everything here this is the one most "
                      "worth your own eyes",
}

#: What a finding CODE asks of the reader, for the codes whose evidence numbers do
#: not say it on their own. `engine-loop`'s do (`cues=6` next to six identical
#: lines); `sparse-cue`'s do not, because `seconds=127.9, units=9` reads as a
#: slow speaker unless someone says which two things it actually means (I-23).
FINDING_SENTENCE = {
    "sparse-cue": "a long subtitle with very few words: either its timing "
                  "stretches over a pause (the words are right and the time is "
                  "not) or the engine dropped words here -- listen to the span "
                  "before trusting its timecode or its text",
    "uncovered-span": "a minute or more with no subtitle at all: VAD may have "
                      "dropped speech here (I-27), or it is a real silence -- "
                      "listen, and if someone is talking re-decode that span "
                      "with --no-vad or a lower --vad-threshold",
}


def no_words_sentence(vad_enabled: Optional[bool],
                      vad_kept: Optional[float]) -> str:
    """Why a round has no words at all (I-22), in the only three ways it can.

    A silent recording decoded with VAD on comes back empty; until 2026-09-27 that
    reached the user as `engine-failed: no-segments` -- a correct answer reported
    as a crash. The fix is not only to stop failing but to SAY why, and "why"
    rests on `vad_kept`, which means something only when VAD is known to have run
    (E-2(a)). So the three branches are E-2's, and no branch guesses past them.
    One wording for the CLI and the page, like `NOTE_SENTENCE`.

    `--no-vad` is named with its price, because it is not a cure: measured in M1,
    VAD off wrote invented text on three of three clips with nobody speaking.
    """
    if vad_enabled is True and vad_kept is not None:
        if vad_kept <= 0.0:
            return ("no words: voice activity detection (VAD) found no speech "
                    "anywhere in this recording. If someone does speak, they are "
                    "below VAD's threshold -- `--no-vad` makes the engine write "
                    "something for every stretch, and over silence that "
                    "something is invented")
        return (f"no words: VAD kept {vad_kept:.1f}s of this recording as "
                f"speech, and the engine wrote nothing for any of it")
    return ("no words: the engine wrote nothing for this recording, and VAD "
            "did not run (or did not report), so nothing here says whether "
            "anyone speaks in it")


#: A note from a round written before the reason was recorded. Absence is a third
#: value and is never rendered as `single-signal` -- the whole point of writing
#: the field was that the reader could not tell them apart.
NOTE_UNSAID = ("flagged; this round is older than the field that says why, so "
               "the reason is not on record")


#: Printed instead of the words when the round's record cannot produce them. It is
#: a SENTENCE and not an empty string, for `read.excerpt`'s reason: a reader has to
#: be able to tell 「it said nothing」 from 「this cannot say」.
NO_TEXT = "(this round's record cannot show the words here)"


def describe_plan(response: schema.PlanResponse,
                  root: Optional[pathlib.Path] = None) -> list[str]:
    """The plan, with the words in it -- which is the whole point of the verb.

    Until 2026-09-11 this printed positions and evidence numbers and no text at
    all, so the two questions a reader actually has -- 「哪一句？」 and 「要刪掉什
    麼？」 -- were answerable only by opening `全文/*.txt` and counting lines
    (`docs/first-run-2026-09-10.md` §4, items 4 and 5). The text comes from
    `ui/read.py` and from nowhere else: INV-12 keeps it out of the response, so
    the human face fetches it from the round's own record, in source coordinates.

    `root` is optional because the numbers are still worth printing when the
    record cannot be read -- and when that happens it is SAID, never left as a
    blank where a sentence used to be.
    """
    lines = [f"run {response.run} revision {response.rev}",
             f"  {len(response.findings)} finding(s), {len(response.notes)} "
             f"note(s), {len(response.ops)} proposed edit(s)"]
    cues: Mapping[str, dict] = {}
    if root is not None:
        try:
            cues = read.cues_of(root, response.run, response.rev)
        except (read.ReadError, OSError, ValueError) as e:
            # BR-10's shape: a failure says so. A silent fall-through to the old
            # numbers-only output would look identical to a round that genuinely
            # had nothing to show.
            lines.append(f"  (cannot read this round's text: {e})")

    def words(ref) -> str:
        piece = read.excerpt(cues, str(ref.cue_id), ref.char_start, ref.char_end)
        if piece is None:
            return NO_TEXT
        return f"{read.clock(piece.start)}  {piece.one_line}"

    for ref in response.findings:
        lines.append(f"  [{ref.code}] {ref.cue_id} chars "
                     f"{ref.char_start:.0f}-{ref.char_end:.0f}  "
                     + ", ".join(f"{k}={v:g}" for k, v
                                 in sorted(ref.evidence.items())))
        if str(ref.code) in FINDING_SENTENCE:
            lines.append(f"      {FINDING_SENTENCE[str(ref.code)]}")
        if cues:
            lines.append(f"      {words(ref)}")
    for ref in response.notes:
        lines.append(f"  (note) {ref.cue_id} chars "
                     f"{ref.char_start:.0f}-{ref.char_end:.0f}  "
                     + NOTE_SENTENCE.get(str(ref.reason), NOTE_UNSAID))
        if cues:
            lines.append(f"      {words(ref)}")
    if response.ops:
        # The list `accept --op N` indexes, with N shown, because the index IS the
        # interface: 21 proposals and no way to see what the third one was is the
        # defect this section exists to close (PIM §5.5).
        lines.append(f"  proposed edits -- `accept --op N` takes these numbers:")
        for i, op in enumerate(response.ops):
            lines.append(f"    --op {i}  {op.op} {op.cue_id} chars "
                         f"{op.char_start:.0f}-{op.char_end:.0f}  "
                         f"[{op.code}, {op.origin}]")
            if cues:
                lines.append(f"      {words(op)}")
    if response.candidates:
        # Guesses (sound-alikes). `accept` never applies these unless named.
        lines.append(f"  candidates -- applied only by `accept --candidate N`:")
        for i, op in enumerate(response.candidates):
            lines.append(f"    --candidate {i}  {op.op} {op.cue_id} chars "
                         f"{op.char_start:.0f}-{op.char_end:.0f}  [{op.code}]")
            if cues:
                lines.append(f"      {words(op)}")
    return lines


# --- the parser -----------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ltm",
        description="Make a machine transcript's uncertainty visible. "
                    "Everything runs on this machine; nothing is uploaded.")
    # Resolved each time the parser is built (PSM P-1): `--root` > `LTM_ROOT` >
    # `<home>/TranscriptDesk`.
    p.add_argument("--root", default=str(layout.default_root()),
                   help="output root (default: $LTM_ROOT, else "
                        "<home>/TranscriptDesk; the store vocabulary under it "
                        "is fixed)")
    p.add_argument("--json", action="store_true",
                   help="print the verb's response as JSON instead of prose")
    p.add_argument("--progress-file", default=None, metavar="PATH",
                   help="append one JSON object per progress event to this file. "
                        "For a caller with no terminal to watch: --json turns the "
                        "printed line off, and this is what replaces it")
    p.add_argument("--cancel-file", default=None, metavar="PATH",
                   help="stop when this file appears. For a caller with no "
                        "keyboard: it is the same request Ctrl-C makes, and it "
                        "gets the same answer (R13)")
    sub = p.add_subparsers(dest="verb", required=True)

    intake = sub.add_parser("intake", help="register an audio or subtitle file")
    intake.add_argument("path")
    intake.add_argument("--from-url", default=None, metavar="URL",
                        help="where this file came from (http/https). Recorded on "
                             "the run; never fetched, never checked online")
    intake.add_argument("--from-note", default=None, metavar="TEXT",
                        help="a short note about the source (one line, at most "
                             "200 characters), e.g. which tool and version "
                             "fetched it")

    decode = sub.add_parser("decode", help="run the engine and detect")
    decode.add_argument("run")
    decode.add_argument("--audio", default=None)
    decode.add_argument("--condition-on-previous-text", action="store_true",
                        help="E-1: off by default, and the record says which it was")
    decode.add_argument("--no-vad", dest="vad", action="store_false",
                        help="decode the silence too. §6.3's measured cue counts "
                             "were made this way; the default is not")
    decode.add_argument("--language", default=None, metavar="CODE",
                        help="pin ONE language for the whole file (zh, en, yue); "
                             "needs --no-multilingual. Default: unpinned, and the "
                             "engine re-detects the language for every window")
    decode.add_argument("--no-multilingual", dest="multilingual",
                        action="store_false",
                        help="E-3: stop re-detecting the language per window. On by "
                             "default: without it a Mandarin/English recording was "
                             "written with zero Chinese characters. Required when "
                             "pinning --language, which it would otherwise override")
    decode.add_argument("--compute-type", default="float16",
                        choices=verbs.COMPUTE_TYPES,
                        help="float16 needs a working GPU and FAILS on CPU; use "
                             "int8 if there is no GPU or it has gone away")
    decode.add_argument("--device", default="auto", choices=verbs.DEVICES)
    decode.add_argument("--if-no-gpu", default="ask",
                        choices=("ask", "cpu", "fail"),
                        help="what to do when this machine cannot do the "
                             "arithmetic asked for: ask and continue on a yes "
                             "(default), switch to the processor without asking, "
                             "or stop")
    decode.add_argument("--no-language-slots", dest="language_slots",
                        action="store_false",
                        help="skip the second language signal (agreement then has "
                             "nothing to compare and every stretch is undetermined)")
    decode.add_argument("--languages", default=None, metavar="CODES",
                        type=lambda s: tuple(c.strip() for c in s.split(",")
                                             if c.strip()),
                        help="I-28: the languages this recording is expected to "
                             "contain, comma-separated (en,zh). Per-window "
                             "detection then chooses only among them; the "
                             "language slots still vote over every language")
    decode.add_argument("--vad-threshold", type=float, default=None,
                        metavar="P",
                        help="I-27: Silero speech threshold (library default "
                             "0.5); lower keeps quieter speech")
    decode.add_argument("--vad-min-silence-ms", type=int, default=None,
                        metavar="MS",
                        help="I-27: silence needed to end a speech chunk "
                             "(library default 2000)")
    decode.add_argument("--vad-speech-pad-ms", type=int, default=None,
                        metavar="MS",
                        help="I-27: padding kept on each side of a speech chunk "
                             "(library default 400)")

    detect = sub.add_parser("detect", help="re-run detection over a revision")
    detect.add_argument("run")
    detect.add_argument("--rev", default=None)
    detect.add_argument("--enable", action="append", default=None,
                        help="add a detector; ones with no controls print a warning")
    detect.add_argument("--term", action="append", default=None,
                        metavar="DETECTOR=TERM",
                        help="a declared term (filler/glossary only, INV-7)")
    detect.add_argument("--lexicon", action="append", default=None,
                        metavar="PATH",
                        help="a correction lexicon (ltm-lexicon/1 JSON) on top of "
                             "the ones in <root>/_詞表/; turns glossary on")
    detect.add_argument("--word-level", action="store_true",
                        help="fails in milestone 1, naming the absent layer (T-1)")

    plan_p = sub.add_parser("plan", help="show a revision's plan")
    plan_p.add_argument("run")
    plan_p.add_argument("--rev", default=None)

    accept = sub.add_parser("accept", help="apply selected ops")
    accept.add_argument("run")
    accept.add_argument("--rev", default=None)
    accept.add_argument("--op", action="append", type=int, default=None,
                        help="op index; omit to accept all, pass none to decline")
    accept.add_argument("--candidate", action="append", type=int, default=None,
                        help="dormant candidate index from `plan`; applied only "
                             "when named, and naming any without --op applies "
                             "candidates only")
    accept.add_argument("--picked-by", default=None,
                        help="who chose the candidates: human (default) or "
                             "advisor:<model>@<prompt-version>")
    accept.add_argument("--decline", action="store_true",
                        help="write a declined round: a conclusion, and it consumes "
                             "a serial")

    sub.add_parser("runs", help="list runs")

    show = sub.add_parser("show", help="a revision's structured facts")
    show.add_argument("run")
    show.add_argument("--rev", default=None)

    open_p = sub.add_parser("open", help="open a file for a human to look at")
    open_p.add_argument("path")

    cite = sub.add_parser("cite", help="copy a revision into _citable/ (R14)")
    cite.add_argument("run")
    cite.add_argument("--rev", required=True)
    cite.add_argument("--granted-by", required=True)
    cite.add_argument("--scope", default=None,
                      help="defaults to <run>/<rev>, which is the only legal scope")
    cite.add_argument("--method", default="cli-flag",
                      choices=("ui-dialog", "cli-flag", "agent-relayed"))

    cancel = sub.add_parser("cancel", help="ask a running job to stop")
    cancel.add_argument("--state", required=True, choices=sorted(progress.STATES))

    # NOT a verb: a namespace for questions about the tool itself. It is the one
    # name in `dest="verb"` that `IMPLEMENTATIONS` does not answer, and
    # `test_two_faces.py` pays for that exception with a property test rather
    # than with an exemption list -- see `agent.py`'s docstring.
    agent.add_parser(sub)

    return p


def _terms(pairs: Optional[Sequence[str]]) -> Optional[dict[str, tuple[str, ...]]]:
    if not pairs:
        return None
    out: dict[str, list[str]] = {}
    for pair in pairs:
        name, _, term = pair.partition("=")
        if not term:
            raise SystemExit(f"--term takes DETECTOR=TERM, not {pair!r}")
        out.setdefault(name, []).append(term)
    return {k: tuple(v) for k, v in out.items()}


#: How a human face says 「I looked at the proposals and ticked none of them」.
#:
#: `accept` selects by index and an EMPTY `ops` means 「all of them」, so 「none」
#: has to be an index that selects nothing. It is named here rather than spelled
#: twice because there are two human faces now (this one and `ui/serve.py`), and
#: a convention written out once per face is a convention that can disagree with
#: itself -- the drift ADR §10 is about, in one tuple.
DECLINE: tuple[int, ...] = (-1,)


def _args_for(ns: argparse.Namespace) -> dict[str, Any]:
    """Namespace -> the verb's keyword arguments. One place, so the two faces get
    the same call."""
    verb = ns.verb
    if verb == "intake":
        return {"path": ns.path, "from_url": ns.from_url,
                "from_note": ns.from_note}
    if verb == "decode":
        return {"run": ns.run, "audio": ns.audio, "vad": ns.vad,
                "language": ns.language, "multilingual": ns.multilingual,
                "compute_type": ns.compute_type, "device": ns.device,
                "condition_on_previous_text": ns.condition_on_previous_text,
                "language_slots": ns.language_slots,
                "language_set": ns.languages,
                "vad_threshold": ns.vad_threshold,
                "vad_min_silence_ms": ns.vad_min_silence_ms,
                "vad_speech_pad_ms": ns.vad_speech_pad_ms}
    if verb == "detect":
        enabled = tuple(ns.enable) if ns.enable else detectors.DEFAULT
        return {"run": ns.run, "rev": ns.rev, "enabled": enabled,
                "terms": _terms(ns.term), "word_level": ns.word_level,
                "lexicons": tuple(ns.lexicon or ())}
    if verb in ("plan", "show"):
        return {"run": ns.run, "rev": ns.rev}
    if verb == "accept":
        # `--decline` and "no --op" are DIFFERENT requests, and the difference is
        # §7 case (3): declining writes a round that changed nothing, while an
        # omitted `--op` accepts every op the plan proposed.
        ops = DECLINE if ns.decline else tuple(ns.op or ())
        out = {"run": ns.run, "rev": ns.rev, "ops": ops}
        if ns.candidate:
            if ns.decline:
                raise SystemExit("--decline writes a round that changes nothing; "
                                 "it cannot also apply --candidate")
            out["candidates"] = tuple(ns.candidate)
        if ns.picked_by:
            out["picked_by"] = ns.picked_by
        return out
    if verb == "runs":
        return {}
    if verb == "open":
        return {"path": ns.path}
    if verb == "cite":
        return {"run": ns.run, "rev": ns.rev, "granted_by": ns.granted_by,
                "scope": ns.scope or f"{ns.run}/{ns.rev}", "method": ns.method}
    if verb == "cancel":
        return {"state": ns.state}
    raise SystemExit(f"no such verb: {verb!r}")


# --- rendering a response ------------------------------------------------

def _clock(seconds: float) -> str:
    """`m:ss`, or `h:mm:ss` past an hour -- how a person scrubs a player."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def render(response: schema.Response, *, extra: Sequence[str] = (),
           root: Optional[pathlib.Path] = None) -> str:
    """`root` is here so `plan` can show the words; every other verb ignores it."""
    lines: list[str] = []
    if isinstance(response, schema.PlanResponse):
        lines += describe_plan(response, root)
    elif isinstance(response, schema.IntakeResponse):
        lines.append(f"run {response.run}  ({response.bytes:.0f} bytes, "
                     f"sha256 {response.sha256[:8]})")
        if response.recognised >= 1.0:
            # §9.2: 「not an error, and not silence」. The user handed over a file
            # under a name this store has not seen; what it HAS seen is the audio,
            # and the work already done on it is the thing they would otherwise
            # lose without noticing.
            how_far = ("no round yet" if response.high_water < 0
                       else f"up to r{int(response.high_water):03d}")
            lines.append(f"  this audio is already this run ({how_far}); "
                         f"nothing new was created, and the folder keeps the name "
                         f"it had")
        lines.append(f"  {response.path}")
        # PSM P-3: the text is on the marker (INV-12 keeps it out of the response).
        lines += [f"  {s}" for s in
                  came_from.describe(came_from.on_marker(pathlib.Path(response.path)))]
    elif isinstance(response, schema.DecodeResponse):
        lines.append(f"run {response.run} revision {response.rev}  "
                     f"{response.duration:.1f}s")
        lines.append("  " + ", ".join(f"{k} {v:.0f}" for k, v
                                      in sorted(response.counts.items())))
        if response.degenerated_spans:
            # I-25. Said with the times: a fallback is not a detector trigger on
            # its own, so 「the flagged passages」 used to point at nothing.
            spans = response.degenerated_spans
            shown = ", ".join(f"{_clock(a)}-{_clock(b)}" for a, b in spans[:5])
            more = f" and {len(spans) - 5} more" if len(spans) > 5 else ""
            lines.append(f"  the decoder struggled at {shown}{more} (it fell back "
                         f"to temperature 1.0 there); nothing is flagged for that "
                         f"alone, so listen to those moments yourself")
        elif response.health.get("degenerated", 0.0) >= 1.0:
            lines.append("  the decoder fell back to temperature 1.0 somewhere in "
                         "this file; this response does not say where")
        if response.counts.get("cues", 0.0) == 0.0:
            # I-22. `health["vad_kept"]` is -1.0 exactly when E-2(a) says it
            # means nothing, which is the third branch of the sentence.
            kept = response.health.get("vad_kept", -1.0)
            lines.append("  " + no_words_sentence(
                response.health.get("vad_enabled") == 1.0,
                kept if kept >= 0.0 else None))
        # **What it found, said here rather than made into a second command.**
        # `decode` detects before it returns; measured, the wait before this line
        # was 7m41s, and until 2026-09-11 the answer to 「有沒有問題？」 cost
        # another verb (`docs/first-run-2026-09-10.md` §4 item 1).
        total = sum(response.findings.values())
        if total or response.ops:
            lines.append(f"  findings {_counts(response.findings)}; "
                         f"notes {_counts(response.notes)}; "
                         f"{response.ops:.0f} proposed edit(s) -- "
                         f"`ltm plan {response.run}` to see them")
        else:
            lines.append(f"  nothing flagged in this round"
                         + (f"; {_counts(response.notes)} note(s) to look at "
                            f"anyway -- `ltm plan {response.run}`"
                            if response.notes else ""))
        if response.detectors_skipped:
            lines.append("  not run (no control pair, INV-9): "
                         + ", ".join(str(d) for d in response.detectors_skipped))
        for name, reason in response.detectors_failed:
            lines.append(f"  {name} was enabled and did not run: {reason}")
        for p in response.paths:
            lines.append(f"  {p}")
    elif isinstance(response, schema.DetectResponse):
        lines.append(f"run {response.run} revision {response.rev}")
        lines.append(f"  findings {_counts(response.findings)}; "
                     f"proposals {_counts(response.proposals)}; "
                     f"notes {_counts(response.notes)}")
        if response.detectors_skipped:
            lines.append("  not run (no control pair, INV-9): "
                         + ", ".join(str(d) for d in response.detectors_skipped))
        for name, reason in response.detectors_failed:
            lines.append(f"  {name} was enabled and did not run: {reason}")
    elif isinstance(response, schema.AcceptResponse):
        what = ("declined -- nothing changed, and this round is on record as a "
                "decision" if response.declined >= 1.0
                else f"{response.ops:.0f} op(s) applied")
        lines.append(f"run {response.run}  {response.source_rev} -> "
                     f"{response.rev}: {what}")
        for p in response.paths:
            lines.append(f"  {p}")
    elif isinstance(response, schema.RunsResponse):
        for ref in response.runs:
            # A high water of -1 is "no round has run" and it is NOT r000: the
            # difference is exactly what INV-17 keeps asking for, so it is said in
            # words rather than printed as a negative serial.
            how_far = ("no round yet" if ref.high_water < 0
                       else f"up to r{int(ref.high_water):03d}")
            lines.append(f"{ref.run}  ({how_far})")
        if not response.runs:
            lines.append("no runs under this root yet")
    elif isinstance(response, schema.ShowResponse):
        lines.append(f"run {response.run} revision {response.rev}")
        if root is not None:
            marker = layout.intake_dir(root) / f"{response.run}.json"
            lines += [f"  {s}" for s in came_from.describe(came_from.on_marker(marker))]
        lines.append("  " + ", ".join(f"{k} {v:g}" for k, v
                                      in sorted(response.counts.items())))
        for stretch, language in zip(response.stretches, response.languages):
            lines.append(f"  {stretch['start']:8.1f}-{stretch['end']:8.1f}  "
                         f"{language}  agreement {stretch['agreement']:.3f}")
        for p in response.paths:
            lines.append(f"  {p}")
    elif isinstance(response, schema.OpenResponse):
        lines.append(response.path if response.exists >= 1.0
                     else f"{response.path}  (does not exist)")
    elif isinstance(response, schema.CiteResponse):
        lines.append(f"cited {response.run} {response.rev}")
        for p in response.paths:
            lines.append(f"  {p}")
    elif isinstance(response, schema.CancelResponse):
        if response.accepted >= 1.0:
            eta = ("" if response.eta_seconds is None
                   else f"  (up to {response.eta_seconds:.0f}s to stop)")
            lines.append(f"cancelling from {response.state}{eta}")
        else:
            lines.append(f"cannot cancel from {response.state}: "
                         + refusal_sentence(str(response.reason_code)))
    else:
        lines.append(str(response))
    lines.extend(extra)
    return "\n".join(lines)


def _counts(mapping: Mapping[str, float]) -> str:
    return ", ".join(f"{k} {v:.0f}" for k, v in sorted(mapping.items())) or "none"


def as_json(response: schema.Response) -> str:
    """The response as data, for a human who wants to pipe it.

    Not the agent face -- that one calls `verbs.call` directly and never parses
    text. This exists so a person can grep, and it is the same object either way.
    """
    def plain(value):
        # `Closed`, not `Identifier`: `StateName` and `Provenance` are the other
        # two closed sets, and naming the subclasses here is the allowlist shape
        # A-8 refuses -- the third one would be found by whoever hit the bug.
        if isinstance(value, schema.Closed):
            return value.text
        if isinstance(value, Mapping):
            return {k: plain(v) for k, v in value.items()}
        if isinstance(value, (tuple, list)):
            return [plain(v) for v in value]
        if hasattr(value, "__dataclass_fields__"):
            return {f: plain(getattr(value, f))
                    for f in value.__dataclass_fields__}
        return value
    return json.dumps(plain(response), ensure_ascii=False, indent=2)


# --- main ----------------------------------------------------------------

def _asked_for_the_missing_gpu(ns, e: schema.AgentError) -> bool:
    """Is this the one refusal a person can answer with a yes?

    `--json` is excluded on purpose. That face is a machine's, and a machine does
    not get asked a question -- it gets the code plus its subject and decides for
    itself whether to call again with `compute_type="int8", device="cpu"`. A
    prompt on a pipe is a hang.
    """
    return (not ns.json and ns.verb == "decode"
            and e.code == "engine-failed" and e.subject == NO_GPU)


def _may_use_the_processor(ns, stream=None) -> bool:
    """Say what happened in ordinary words, then let the person decide.

    Three things are deliberate here. The words name no library and no numeric
    format -- someone who does not know what `float16` is still has to be able to
    answer. The default is NO, because saying yes silently is the invisible
    downgrade this pause exists instead of. And a non-interactive run does not
    guess: it prints the flag that would consent and stops, since a prompt nobody
    can see is a hang, and assuming a yes on a script's behalf is the same
    invisible downgrade wearing a different hat.
    """
    if ns.if_no_gpu == "fail":
        return False
    print("this machine cannot use its graphics card for this right now, so "
          "nothing was decoded.", file=sys.stderr)
    print("it can run on the processor instead: same words, several times "
          "slower.", file=sys.stderr)
    if ns.if_no_gpu == "cpu":
        print("running on the processor, as --if-no-gpu cpu asked.",
              file=sys.stderr)
        return True
    if not sys.stdin.isatty():
        print("re-run with --if-no-gpu cpu to say yes, or "
              "--compute-type int8 --device cpu to be explicit.",
              file=sys.stderr)
        return False
    print("use the processor for this run? [y/N] ", end="", file=sys.stderr)
    sys.stderr.flush()
    return (stream or sys.stdin).readline().strip().lower() in ("y", "yes")


def _run_verb(ns, ctx, line):
    """Call the verb, and turn "there is no GPU" into a question rather than a wall.

    The retry is here rather than inside `decode` so that the product's core never
    changes what it was asked to do: the verb refused, a PERSON answered, and the
    second call is a different request with `compute_type` and `device` in it --
    which lands in `_origin.json` like any other decode. A silent fallback inside
    the verb would leave a revision whose record says it ran on the processor with
    nothing saying anyone agreed to that.
    """
    try:
        return VERBS[ns.verb](ctx, **_args_for(ns))
    except schema.AgentError as e:
        if not _asked_for_the_missing_gpu(ns, e):
            raise
        line.done()
        if not _may_use_the_processor(ns):
            raise                       # still a refusal, and it prints as one
        args = dict(_args_for(ns))
        args["compute_type"], args["device"] = CPU_FALLBACK
        return VERBS[ns.verb](ctx, **args)


def main(argv: Optional[Sequence[str]] = None) -> int:
    import pathlib

    ns = build_parser().parse_args(argv)

    # The namespace is answered before anything else exists. No `Context`, no
    # root, no cancellation: `ltm agent which` must work on a machine with no
    # store and no output root, because it is what a caller runs to find out
    # whether the product is there at all.
    if ns.verb == "agent":
        return agent.run(ns)

    line = Line()
    cancellation = progress.Cancellation()

    # The printed line is for a person, so `--json` turns it off; the stream is
    # for whoever asked for it, so it is on in both modes. A caller that wants
    # both gets both.
    fan = Fan([] if ns.json else [line])
    if ns.progress_file:
        fan.sinks.append(ProgressStream(pathlib.Path(ns.progress_file)))
    if ns.cancel_file:
        # Daemon: the watcher outlives nothing. Every exit from this function is
        # the process exiting, and a thread that had to be joined would need a
        # `finally` on five return paths to avoid hanging a refusal.
        threading.Thread(
            target=watch_for_cancel,
            args=(pathlib.Path(ns.cancel_file), cancellation, fan),
            daemon=True, name="ltm-cancel-watch").start()

    ctx = verbs.Context(root=pathlib.Path(ns.root),
                        on_progress=fan or None,
                        cancellation=cancellation)

    warnings: list[str] = []
    if ns.verb == "detect" and ns.enable:
        # §6.4: the user asked for an uncontrolled detector, so the user is told
        # what its output is worth. Printed before the run, because afterwards it
        # reads as an excuse for the findings.
        warnings = [f"  {w}" for w in detectors.warnings_for(tuple(ns.enable))]
        for w in warnings:
            print(w, file=sys.stderr)

    try:
        response = _run_verb(ns, ctx, line)
    except KeyboardInterrupt:
        # A cancel REQUEST, not a kill. The state is whatever the run reached; from
        # the CLI's vantage the honest answer is the phase it was last told about,
        # and `ack` decides whether that state may be cancelled at all.
        line.done()
        state = _state_for(line.phase)
        reply = cancellation.request(state)
        if reply.accepted:
            print(f"cancelling from {state} -- partial output is discarded, so "
                  f"nothing was written", file=sys.stderr)
        else:
            print(f"cannot cancel from {state}: {reply.reason}", file=sys.stderr)
        return 130
    except schema.AgentError as e:
        line.done()
        if ns.json:
            # A refusal under `--json` is DATA. Without this the only machine
            # readable refusal was in-process (`verbs.call`), so anything driving
            # the product out of process -- which R4 is about -- had to parse an
            # English sentence off stderr to learn that `no-such-run` happened. The
            # sentence is still the human face's answer; it is not the only one.
            print(as_json(schema.ErrorResponse(
                verb=ident(ns.verb), code=ident(e.code),
                subject=ident(e.subject) if e.subject else None)))
        else:
            print(f"{e.code}: {_ERRORS[e.code]}", file=sys.stderr)
            if e.subject:
                print(f"  ({e.subject})", file=sys.stderr)
                if e.subject in _SUBJECT_SENTENCES:
                    print(f"  {_SUBJECT_SENTENCES[e.subject]}", file=sys.stderr)
        return 2
    except (detectors.DetectError, ValueError) as e:
        # The product's own refusals carry their reason in the message ON PURPOSE:
        # they are written for a person to read, and this is the face that reads
        # them out. The agent face maps the same exceptions to codes.
        line.done()
        print(str(e), file=sys.stderr)
        return 2

    line.done()
    if ns.verb == "open" and isinstance(response, schema.OpenResponse):
        _launch(response.path)
    print(as_json(response) if ns.json
          else render(response, root=pathlib.Path(ns.root)))
    return 0


#: Which state the CLI is in, from the phase it last reported. `detect` is the
#: phase R13 added to the cancellable set, so the mapping matters: a Ctrl-C during
#: detection must be ACCEPTED, and one during `apply` must be refused with INV-13's
#: reason rather than tearing a member set in half.
_PHASE_STATE: Mapping[Optional[str], str] = {
    None: "queued",
    "load-model": "decoding",
    "transcribe-call": "decoding",
    "decode": "decoding",
    "language-slots": "decoding",
    "detect": "detecting",
    "apply": "applying",
}


def _state_for(phase: Optional[str]) -> str:
    return _PHASE_STATE[phase]


_MISSING_PHASES = [p for p in progress.PHASE_UNITS if p not in _PHASE_STATE]
if _MISSING_PHASES:
    raise RuntimeError(
        f"phase(s) {_MISSING_PHASES} have no state in `_PHASE_STATE`, so a Ctrl-C "
        f"during them could not be answered. Every phase in `PHASE_UNITS` belongs "
        f"to exactly one state -- a phase added without one is a wait with no "
        f"cancel, which is R13's defect.")


def _launch(path: str) -> None:
    """Hand a path to the OS. **Only the human face does this** (§9).

    An agent asking for a window is a different act from a person clicking one, so
    `open_path` answers with the path and this is where the side effect happens.
    """
    try:
        if os.name == "nt":
            os.startfile(path)             # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", path], check=False)
    except OSError as e:
        print(f"could not open {path}: {e}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
