"""Spawn the sidecar, stream progress, answer a cancel, normalise the output.

PSM sections 8.1, 8.2, 8.3. Three things here are decisions rather than plumbing:

**The heartbeat is synthesised by the PARENT, not only by the child.** The child
does beat inside the `transcribe()` call -- it is the only thing it can do there,
since faster-whisper exposes no callback in any of the call's four eager stages --
but INV-22's contract is with the *user*, and the user is on the parent's side. A
child that goes quiet must not produce a frozen interface, and this project has a
measured, unexplained case of exactly that. So the reader runs on its own thread
and the main loop emits on a timer regardless. A synthesised beat never claims
progress it does not have: it repeats the last known `done` and raises only
`elapsed`, which is true.

**Cancel is answered before it is acted on.** `Cancellation.request()` returns the
`CancelAck` immediately from the state, and only then does the channel tell the
child. "Pressed it, nothing happened, then it vanished" is the other half of the
user's complaint, and it is caused by systems that act first and report later.

**Normalisation stops where the data stops.** `Window`s are runs of equal decode
metrics and need nothing else, so they are built here. `Cue`s need a
`stretch_id` (INV-1) and stretches come from the language map, which needs the
decoded text -- so `build_recording` takes the stretches as an argument instead of
inventing a whole-file stretch with a fabricated `agreement`. A provisional
`Stretch` would have to pick a number for a field whose whole purpose is to say
how sure two independent signals were, and there is no such number yet.
"""
from __future__ import annotations

import dataclasses
import pathlib
import queue
import subprocess
import threading
import time
from typing import Callable, Iterable, Optional, Sequence

from ..core import model
from ..core.progress import (Cancellation, MAX_GAP_SECONDS, ProgressEvent)
from . import contract
from .channel import Channel, channel_for
from .contract import (ContractError, EngineFailure, EngineRequest, EngineResult,
                       RawSegment, RawSlot)

#: How long the parent waits for the child's FIRST event before calling it silent.
#: Generous on purpose: the model load is 4.8-7.2 s on this machine and a slower
#: disk is not a defect. The stall this guards against produced nothing at all,
#: not something late.
FIRST_EVENT_TIMEOUT = 60.0

#: After a cancel is sent, how long the child gets to say `cancelled` before it is
#: terminated. Longer than the worst-case in-call latency (~6.3 s on a 36-minute
#: file) so that a child which *is* going to answer is not killed for being slow.
CANCEL_GRACE = 9.0

ProgressSink = Callable[[ProgressEvent], None]


class EngineError(RuntimeError):
    """Carries an `EngineFailure`. The message names only what was determined."""

    def __init__(self, failure: EngineFailure) -> None:
        super().__init__(failure.message())
        self.failure = failure


def sidecar_script() -> pathlib.Path:
    return pathlib.Path(__file__).with_name("sidecar.py")


def _spawn(request: EngineRequest, channel: Channel, stderr_path: pathlib.Path,
           python: Optional[str] = None,
           script: Optional[pathlib.Path] = None) -> subprocess.Popen:
    exe = python or contract.sidecar_python()
    if not pathlib.Path(exe).exists():
        raise EngineError(EngineFailure(code="interpreter-missing",
                                        phase="load-model",
                                        channel=channel.name))
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    argv = [exe, str(script or sidecar_script())]
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        # The alternative channel takes no stdout pipe at all -- that is the whole
        # point of it, since the pipe is the path the unexplained stall lives on.
        stdout=subprocess.PIPE if not request.uses_files else subprocess.DEVNULL,
        stderr=open(stderr_path, "w", encoding="utf-8"),
        text=True, encoding="utf-8", bufsize=1)
    assert proc.stdin is not None
    proc.stdin.write(request.to_line())
    proc.stdin.flush()
    return proc


def check_model_is_local(model_path: str) -> None:
    """The weights must be a directory on this disk, never a model id.

    This is the one route by which the core path could touch the network, and an
    import-graph check cannot see it: `WhisperModel("large-v3")` is a *download*,
    and the string is indistinguishable from a path to any static analysis. INV-18
    is about the product's graph; this is about its behaviour, so it is checked
    where the value is used rather than where a library is imported.

    Refusing before the spawn also means the failure names the missing weights
    instead of surfacing as an unexplained silence from a child that is quietly
    fetching several gigabytes.
    """
    p = pathlib.Path(model_path)
    if not p.is_dir():
        raise EngineError(EngineFailure(
            code="model-missing", phase="load-model",
            detail={"is_absolute": float(p.is_absolute())}))
    # A directory is not yet a model. A Hugging Face snapshot with its config.json
    # never materialised (seen 2026-10-27) passed the check above and then crashed
    # inside ctranslate2 with a JSON type_error that names no file at all. And a
    # folder without tokenizer.json is worse than a crash: faster-whisper quietly
    # fetches a tokenizer from the hub, which is the network route this check
    # exists to close. So every file the engine needs is checked here, by name.
    missing = {f"missing_{alts[0].replace('.', '_')}": 1.0
               for alts in REQUIRED_MODEL_FILES
               if not any((p / name).is_file() for name in alts)}
    if missing:
        raise EngineError(EngineFailure(
            code="model-missing", phase="load-model",
            detail={"is_absolute": float(p.is_absolute()), **missing}))


#: What a CTranslate2 Whisper folder must hold before the engine is spawned. Each
#: entry is one requirement; its alternatives are the names it may go by (the
#: vocabulary is `.json` in large-v3's conversion and `.txt` in older ones).
REQUIRED_MODEL_FILES = (
    ("model.bin",),
    ("config.json",),
    ("tokenizer.json",),
    ("vocabulary.json", "vocabulary.txt"),
)


def _reader(channel: Channel, proc, out: "queue.Queue") -> None:
    try:
        for event in channel.events(proc):
            out.put(("event", event))
    except ContractError as e:
        out.put(("unparsable", e))
    except Exception as e:                       # a dead pipe, a vanished file
        out.put(("broken", e))
    finally:
        out.put(("eof", None))


#: The duration probe. On PATH per §1's environment table, where it is a verified
#: prerequisite rather than an optional nicety.
FFPROBE = "ffprobe"
PROBE_TIMEOUT = 30.0


def probe_duration(audio: str) -> float:
    """Seconds of audio, from `ffprobe` and **never from the engine**.

    §8.1 and `core.model.Recording`'s own note: the decoding bar's denominator comes
    from the probe. That is not a preference for one tool -- the engine has been
    observed reporting a length **10.00 s longer than the audio** on a `--no-vad`
    run (`docs/evidence/vadseconds_2026-09-09.py`, E-2), so a bar built on the
    engine's number can exceed 100% and teach the user to stop believing it.

    The probe is also what makes 「the denominator exists before the engine does」
    true: 2183.68 s in **0.03-0.54 s** against a 4.8-7.2 s model load
    (`docs/evidence/sidecar_streaming_2026-09-09.md`), an order of magnitude under
    it, so nothing waits for this.

    **A missing or failing probe is a loud failure, not a fallback.** Falling back
    to the engine's number would produce a plausible bar built on the value E-2 is
    about, and nothing in the output would say which of the two it was. `ffprobe` is
    a declared prerequisite (§1); an absent one is an environment fault the user can
    fix in a minute, and being told so beats a silent downgrade.
    """
    try:
        done = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(audio)],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT)
    except FileNotFoundError as e:
        raise EngineError(EngineFailure(
            code="probe-missing", phase="load-model")) from e
    except subprocess.TimeoutExpired as e:
        raise EngineError(EngineFailure(
            code="probe-failed", phase="load-model",
            detail={"timeout_seconds": PROBE_TIMEOUT})) from e
    if done.returncode != 0:
        raise EngineError(EngineFailure(
            code="probe-failed", phase="load-model",
            detail={"exit_code": float(done.returncode)}))
    try:
        seconds = float(done.stdout.strip())
    except ValueError as e:
        # The probe's stdout is NOT put in the failure: it is text from an external
        # tool about the user's own media, and INV-14 keeps captured text out of a
        # failure record. What is said is that the answer was not a number.
        raise EngineError(EngineFailure(
            code="probe-failed", phase="load-model")) from e
    if seconds <= 0:
        raise EngineError(EngineFailure(
            code="probe-failed", phase="load-model",
            detail={"seconds": seconds}))
    return seconds


def run(request: EngineRequest, *,
        cancellation: Optional[Cancellation] = None,
        on_progress: Optional[ProgressSink] = None,
        state: str = "decoding",
        python: Optional[str] = None,
        script: Optional[pathlib.Path] = None,
        stderr_path: Optional[pathlib.Path] = None,
        first_event_timeout: float = FIRST_EVENT_TIMEOUT) -> EngineResult:
    """Decode `request.audio`, emitting progress at least once a second.

    Raises `EngineError` with a failure that names only what was observed.
    """
    if not pathlib.Path(request.audio).exists():
        raise EngineError(EngineFailure(code="audio-missing", phase="load-model"))
    check_model_is_local(request.model_path)
    if request.duration_seconds is None:
        request = dataclasses.replace(
            request, duration_seconds=probe_duration(request.audio))

    channel = channel_for(request)
    stderr_path = stderr_path or pathlib.Path(request.audio).with_suffix(".engine.log")
    proc = _spawn(request, channel, stderr_path, python=python, script=script)

    events: "queue.Queue" = queue.Queue()
    reader = threading.Thread(target=_reader, args=(channel, proc, events),
                              daemon=True)
    reader.start()

    beat = min(request.heartbeat_seconds, MAX_GAP_SECONDS / 2)
    started = time.monotonic()
    phase, done, total, unit = "load-model", None, None, None
    segments: list[RawSegment] = []
    slots: list[RawSlot] = []
    #: How far past the measured duration the engine's timestamps ran. See the
    #: `segment` branch below.
    past_end = 0.0
    result_fields: dict = {}
    failure: Optional[EngineFailure] = None
    saw_anything = False
    cancel_sent_at: Optional[float] = None
    cancelled = False

    def emit(**over) -> None:
        if on_progress is None:
            return
        kwargs = dict(state=state, phase=phase, elapsed=time.monotonic() - started,
                      done=done, total=total, unit=unit)
        kwargs.update(over)
        on_progress(ProgressEvent(**kwargs))

    try:
        emit()
        while True:
            try:
                kind, payload = events.get(timeout=beat)
            except queue.Empty:
                # Nothing from the child. The interface still moves, and the beat
                # says only what is true: more time has passed.
                emit()
                if not saw_anything and time.monotonic() - started > first_event_timeout:
                    failure = EngineFailure(
                        code="silent", phase=phase, channel=channel.name,
                        detail={"waited_seconds": round(time.monotonic() - started, 2)},
                        stderr_path=str(stderr_path))
                    break
                if (cancellation is not None and cancellation.requested
                        and cancel_sent_at is None):
                    channel.send_cancel(proc)
                    cancel_sent_at = time.monotonic()
                if (cancel_sent_at is not None
                        and time.monotonic() - cancel_sent_at > CANCEL_GRACE):
                    cancelled = True
                    break
                continue

            if kind == "eof":
                break
            if kind == "unparsable":
                failure = EngineFailure(code="unparsable", phase=phase,
                                        channel=channel.name,
                                        stderr_path=str(stderr_path))
                break
            if kind == "broken":
                failure = EngineFailure(code="crashed", phase=phase,
                                        channel=channel.name,
                                        stderr_path=str(stderr_path))
                break

            saw_anything = True
            event = payload
            name = event["ev"]
            if name == "phase":
                phase = event["phase"]
                total = event.get("total")
                unit = event.get("unit")
                done = event.get("done", 0.0 if total is not None else None)
                emit(detail={k: v for k, v in event.items()
                             if isinstance(v, (int, float)) and k != "total"})
            elif name == "beat":
                done = event.get("done", done)
                emit(detail={k: v for k, v in event.items()
                             if isinstance(v, (int, float))})
            elif name == "segment":
                seg = RawSegment(**{k: event[k] for k in RawSegment.__slots__})
                segments.append(seg)
                # **The denominator is measured and the numerator is the engine's,
                # so they can disagree -- and they do.** On a 51-minute recording
                # the last segment ended 0.44 s past what `ffprobe` measured. That
                # is the same disagreement E-2 recorded from the other side (the
                # engine claiming a duration 10.00 s longer than the audio), and
                # it is small, real, and not the engine misbehaving badly enough
                # to stop for.
                #
                # `ProgressEvent` is right to refuse 100.01% -- a bar that reaches
                # 103% teaches people to stop believing the number. But the
                # response to an impossible READING is to stop the bar at the end,
                # not to end a decode that has been running for a quarter of an
                # hour. So the numerator is clamped here, where the two sources
                # meet, and the overshoot is carried out on the result instead of
                # being swallowed (A-23).
                if total is not None and seg.end > total:
                    past_end = max(past_end, seg.end - total)
                    done = total
                else:
                    done = seg.end
                emit(detail={"cues": float(len(segments))})
            elif name == "slot":
                slots.append(RawSlot(**{k: event[k] for k in RawSlot.__slots__}))
                done = float(len(slots))
                emit(detail={"slots": float(len(slots))})
            elif name == "report":
                result_fields = {k: v for k, v in event.items() if k != "ev"}
            elif name == "cancelled":
                cancelled = True
                break
            elif name == "error":
                failure = EngineFailure(
                    code=event.get("code", "crashed"),
                    phase=event.get("phase", phase), channel=channel.name,
                    stderr_path=str(stderr_path))
                break
            elif name == "done":
                pass

            if (cancellation is not None and cancellation.requested
                    and cancel_sent_at is None):
                channel.send_cancel(proc)
                cancel_sent_at = time.monotonic()
    finally:
        if cancelled or failure is not None:
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        channel.close()
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()

    if cancelled:
        # Section 8.2: partial output is discarded and NOTHING is written. The
        # caller never sees the segments, so it cannot be tempted to keep them.
        #
        # The code is `cancelled` and not `exited` (2026-09-16): those two are
        # the same event seen from the process table and different events seen
        # by the person who pressed the button. While they shared a code, a
        # cancelled decode reached the user as `engine-failed (exited)` -- their
        # run reported as a crash -- and the only way for a surface to tell the
        # two apart was to remember that it had sent a cancel, which is the
        # parent deciding an outcome that is the child's to report.
        raise EngineError(EngineFailure(code="cancelled", phase=phase,
                                        channel=channel.name,
                                        exit_code=proc.returncode))
    if failure is not None:
        raise EngineError(EngineFailure(
            code=failure.code, phase=failure.phase, channel=failure.channel,
            exit_code=proc.returncode, stderr_path=failure.stderr_path,
            detail=failure.detail))
    if proc.returncode not in (0, None):
        raise EngineError(EngineFailure(code="exited", phase=phase,
                                        channel=channel.name,
                                        exit_code=proc.returncode,
                                        stderr_path=str(stderr_path)))
    # Zero segments is a RESULT, not a failure (I-22, 2026-09-27). A recording of
    # silence decoded with VAD on comes back empty -- measured on three of the M1
    # clips (`docs/m1-silence-probe-2026-09-27.md`) -- and until this date it
    # reached the user as `engine-failed: no-segments`, which says their run broke
    # when the engine had in fact answered correctly. The duration was measured by
    # `probe_duration` before the child started, so an empty decode always has
    # something to build a `Recording` over.
    #
    # What the old check ALSO caught, and this one keeps: a child that exits 0
    # having sent nothing at all -- the one that received a cancel and vanished
    # without answering it (§8.1's control for (c)). An empty decode and a vanished
    # child differ in exactly one observable way: the finished one sent its
    # `report`. So the failure names that, `no-report`, rather than the absence of
    # words, which is what silence also looks like.
    if not segments and not result_fields:
        raise EngineError(EngineFailure(code="no-report", phase=phase,
                                        channel=channel.name,
                                        stderr_path=str(stderr_path)))

    result = EngineResult(
        segments=tuple(segments),
        slots=tuple(slots),
        language_slots=result_fields.get("language_slots"),
        vad_kept=result_fields.get("vad_kept"),
        vad_enabled=result_fields.get("vad_enabled"),
        condition_on_previous_text=result_fields.get("condition_on_previous_text"),
        multilingual=result_fields.get("multilingual"),
        duration=request.duration_seconds or result_fields.get("duration"),
        language=result_fields.get("language"),
        language_probability=result_fields.get("language_probability"),
        model_load_seconds=result_fields.get("model_load_seconds"),
        call_seconds=result_fields.get("call_seconds"),
        timestamps_past_end=round(past_end, 3),
        language_set=(tuple(result_fields["language_set"])
                      if result_fields.get("language_set") else None),
        vad_parameters=result_fields.get("vad_parameters") or None)
    check_vad(result, request.duration_seconds)
    return result


#: How far `vad_kept` may sit above the probed duration and still be the same
#: length. The two are measured by different tools at different resolutions:
#: ffprobe reads the container (1473.607982 s on 2026-10-27's recording), the
#: engine counts 16 kHz samples after resampling and rounds up to a whole one
#: (23,577,728 samples = 1473.608 s). When VAD keeps the whole file -- continuous
#: speech that never pauses for the 2 s VadOptions asks -- that rounding alone was
#: an 18 microsecond "excess", and the run failed. 10 ms is 160 samples: far above
#: any resampler rounding, three orders below the 10.00 s defect E-2 was found on.
VAD_KEPT_RESOLUTION = 0.01


def check_vad(result: EngineResult, duration: Optional[float]) -> None:
    """E-2(a) only, and the restraint is the contract.

    When VAD is known to have run, `vad_kept > duration` is a defect and the run
    fails naming it -- **never rounded down to a value that looks reasonable**,
    because that shape reached a user in the upstream product and was believed for
    a while. When VAD did not run, or the engine said nothing about it, `vad_kept`
    is not consumed at all, so there is nothing here to check: the v0.4 contract
    asserted an impossibility about a field with no meaning, and its own evidence
    already contained the "impossible" number.
    """
    if duration is None or not model.EngineReport(
            vad_kept=result.vad_kept, vad_enabled=result.vad_enabled,
            condition_on_previous_text=result.condition_on_previous_text,
            multilingual=result.multilingual,
            degenerated=False).vad_kept_is_trustworthy():
        return
    assert result.vad_kept is not None
    if result.vad_kept > duration + VAD_KEPT_RESOLUTION:
        raise EngineError(EngineFailure(
            code="vad-kept-impossible", phase="decode",
            detail={"vad_kept": result.vad_kept, "duration": duration,
                    "excess": round(result.vad_kept - duration, 2)}))


# --- normalisation -------------------------------------------------------

def _runs(result: EngineResult
          ) -> tuple[tuple[model.Window, ...], tuple[str, ...]]:
    """The windows, and which one each segment is in, from ONE walk.

    The segment-to-window answer is taken while the runs are being cut, not
    recovered afterwards from times. It used to be recovered: a lookup by start
    time, inclusive at both ends, and real segments touch (one ends at the
    instant the next begins), so the first cue of every run went to the run
    before it -- S4 then read the neighbour's temperature for exactly the cue
    where the decoder's state changed. Measured 2026-10-14 on the seven stored
    runs: every window boundary but one, 9 to 124 cues per run.
    """
    out: list[model.Window] = []
    owner: list[str] = []
    key = None
    start = end = 0.0
    for seg in result.segments:
        k = (seg.avg_logprob, seg.temperature, seg.compression_ratio,
             seg.no_speech_prob)
        if k != key:
            if key is not None:
                out.append(model.Window(id=f"w{len(out)}", start=start, end=end,
                                        avg_logprob=key[0], temperature=key[1],
                                        compression_ratio=key[2],
                                        no_speech_prob=key[3]))
            key, start = k, seg.start
        owner.append(f"w{len(out)}")
        end = seg.end
    if key is not None:
        out.append(model.Window(id=f"w{len(out)}", start=start, end=end,
                                avg_logprob=key[0], temperature=key[1],
                                compression_ratio=key[2], no_speech_prob=key[3]))
    return tuple(out), tuple(owner)


def windows_of(result: EngineResult) -> tuple[model.Window, ...]:
    """Runs of equal decode metrics, **in time order** (PIM §4.1.4).

    Counting distinct metric tuples instead silently shrinks every denominator:
    the baseline has 79 runs and 77 distinct tuples.
    """
    return _runs(result)[0]


def _stretch_at(stretches: Sequence[model.Stretch], t: float) -> str:
    """Which stretch a time falls in.

    Not `model.stretch_of`, which resolves an id a `Cue` already carries. This is
    the other direction, and it is the runner's job because it is the runner that
    is assigning the ids. A time past the last boundary belongs to the last
    stretch rather than raising: the last cue can end a hair after the stretch the
    language map cut, and a cue with no stretch would violate INV-1 over a
    rounding difference.
    """
    if not stretches:
        raise contract.ContractError(
            "no stretches: a Cue cannot exist without a stretch id (INV-1), and "
            "inventing one here would mean inventing its agreement")
    for s in stretches:
        if s.start <= t < s.end:
            return s.id
    return stretches[-1].id if t >= stretches[-1].end else stretches[0].id


def build_recording(result: EngineResult, *, id: str, path: str, sha256: str,
                    stretches: Iterable[model.Stretch],
                    duration: Optional[float] = None) -> model.Recording:
    """The four layers. `stretches` is an argument for a reason -- see the module
    docstring: a `Stretch` cannot be invented here without inventing its
    `agreement`, and that field's whole job is to say how sure two independent
    signals were."""
    windows, owner = _runs(result)
    stretches = tuple(stretches)
    cues = tuple(
        model.Cue(id=f"c{seg.idx}", start=seg.start, end=seg.end, text=seg.text,
                  window_id=w, stretch_id=_stretch_at(stretches, seg.start))
        for seg, w in zip(result.segments, owner))
    return model.Recording(
        id=id, path=path,
        duration=duration or result.duration or (cues[-1].end if cues else 0.0),
        sha256=sha256,
        report=model.EngineReport(
            vad_kept=result.vad_kept, vad_enabled=result.vad_enabled,
            condition_on_previous_text=result.condition_on_previous_text,
            multilingual=result.multilingual,
            degenerated=any(s.temperature >= 1.0 for s in result.segments)),
        stretches=stretches, windows=windows, cues=cues)
