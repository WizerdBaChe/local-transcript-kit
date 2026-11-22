"""Run BY the ASR interpreter. Never imported by product code.

    runtime\\asr-py\\Scripts\\python.exe -m ltm.engine.sidecar
    runtime\\asr-py\\Scripts\\python.exe src/ltm/engine/sidecar.py

(from the repository root; `runtime/` is git-ignored and `$LTM_SIDECAR_PYTHON`
overrides it -- see `contract.py`).

Reads one JSON request on stdin line 1, then emits one JSON event per line and
keeps reading stdin for `cancel`. It **re-declares** the event names instead of
importing `contract.py`, because this file runs under an interpreter that does not
have the product on its path -- and a child that cannot start because of an import
error cannot report why it did not start.

Two things it must do and one it must not:

* **Pass `condition_on_previous_text` explicitly** (E-1). The upstream product
  never passed it and therefore ran at faster-whisper's default -- on -- while
  arguing about a value nobody was sending. `tests/test_engine.py` reads this
  file's AST and asserts the keyword is in the `transcribe()` call, because the
  only interpreter that can execute the call is not the one that runs the tests.
* **Heartbeat inside the `transcribe()` call.** The call is indivisible from here:
  before it returns, faster-whisper eagerly decodes the whole file to 16 kHz, runs
  VAD over the whole array, computes log-mel features, and detects the language --
  1.34 s + 3.89 s + 0.88 s + 0.17 s on a 36.4-minute file, with no callback in any
  of them. So it beats from a thread and reports `elapsed` with an estimate, and
  it does NOT report `vad` as a phase with a denominator: that would claim a
  channel that does not exist.
* **Not answer a cancel inside that call.** It cannot, and pretending otherwise
  would be worse than the delay. The between-segments check starts once the
  generator exists; the parent is told the remaining call time in the ack instead.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import threading
import time

EV_PHASE, EV_BEAT, EV_SEGMENT, EV_SLOT = "phase", "beat", "segment", "slot"
EV_REPORT, EV_DONE, EV_ERROR, EV_CANCELLED = "report", "done", "error", "cancelled"
CONTROL_CANCEL = "cancel"

#: The language-slot grid, re-declared here for the same reason as the event
#: names: this file runs under an interpreter that does not have the product on
#: its path. `tests/test_engine.py` asserts these equal `detect.langmap`'s, so the
#: two copies cannot drift silently -- a re-declaration a test does not pin is
#: just a second definition.
SLOT_SECONDS = 30.0
SAMPLE_RATE = 16000
#: A tail shorter than this is not a slot: `detect_language` on half a second of
#: audio is not a measurement. It is the rule `langmap_probe_2026-09-09.py` ran,
#: and it is why the baseline has 73 slots rather than 74.
MIN_SLOT_SECONDS = 1.0


def slot_count(samples: int) -> int:
    """How many slots this many 16 kHz samples make."""
    whole, rest = divmod(samples, int(SLOT_SECONDS * SAMPLE_RATE))
    return whole + (1 if rest >= MIN_SLOT_SECONDS * SAMPLE_RATE else 0)

_out_lock = threading.Lock()
_sink = None                 # set in main(): stdout, or the progress file
_cancel = threading.Event()
_cancel_file: pathlib.Path | None = None
_t0 = time.monotonic()


def emit(ev: str, **fields) -> None:
    """One line, flushed. The lock matters: the heartbeat thread and the decode
    loop both write, and a half-written line is an unparsable event rather than a
    late one."""
    fields["ev"] = ev
    fields.setdefault("elapsed", round(time.monotonic() - _t0, 3))
    line = json.dumps(fields, ensure_ascii=False)
    with _out_lock:
        _sink.write(line + "\n")
        _sink.flush()


def watch_stdin() -> None:
    """Primary channel: a `cancel` line, or EOF meaning the parent is gone.

    **Started only AFTER the model is loaded.** A thread blocked on `sys.stdin`
    while the parent holds that pipe open deadlocks `WhisperModel(...)` --
    measured, reproducible, and it is the stall
    `docs/evidence/sidecar_streaming_2026-09-09.md` recorded as unexplained. The
    matrix is in `docs/evidence/sidecar_stdin_deadlock_2026-09-09.txt`: the load
    completes in ~3.2 s with stdin closed, with stdin open and no reader, and with
    a heartbeat thread running; it hangs indefinitely only when a reader is blocked
    on stdin with the pipe still open. Start it afterwards and the same child
    loads in 3.18 s and still receives the cancel.
    """
    for line in sys.stdin:
        if line.strip() == CONTROL_CANCEL:
            _cancel.set()
            return
    _cancel.set()            # EOF: nobody is listening any more


def watch_parent(interval: float) -> None:
    """Cover the load window, where the stdin reader may not run.

    The primary channel gets parent death free through stdin EOF -- but not while
    that reader is deliberately absent, so during the load this is what notices an
    orphaned child. Polling `getppid()` touches no shared buffer and is therefore
    not the thing that deadlocks the load.
    """
    parent = os.getppid()
    while not _cancel.is_set():
        try:
            if os.getppid() != parent:
                _cancel.set()
                return
        except OSError:
            _cancel.set()
            return
        time.sleep(interval)


def watch_cancel_file(interval: float) -> None:
    """Alternative channel: a sentinel file, plus our own is-the-parent-alive check.

    The parent check is not redundant here. The primary channel gets parent death
    for free through stdin EOF; with the sentinel channel there is no pipe to
    notice, so an orphaned child would decode a whole file for nobody.
    """
    parent = os.getppid()
    while not _cancel.is_set():
        if _cancel_file is not None and _cancel_file.exists():
            _cancel.set()
            return
        try:
            if os.getppid() != parent:
                _cancel.set()
                return
        except OSError:
            _cancel.set()
            return
        time.sleep(interval)


def heartbeat(phase: str, interval: float, estimate: float | None,
              stop: threading.Event) -> None:
    """Beat until told to stop. Says only what is true: time has passed."""
    while not stop.wait(interval):
        fields = {"phase": phase}
        if estimate is not None:
            fields["estimated_seconds"] = round(estimate, 2)
        emit(EV_BEAT, **fields)


def compute_unsupported(exc: BaseException, device: str,
                        compute_type: str) -> bool:
    """Is this failure "this machine cannot do that arithmetic"?

    **Asked as a capability question, never by matching the exception's wording.**
    ctranslate2 is the thing that knows, so it is asked: if the requested compute
    type appears in `get_supported_compute_types` for a device that exists here,
    then the machine CAN do it and this ValueError is about something else. A code
    read off an error message is a theory about what went wrong, and
    `EngineFailure.CODES` exists to keep theories out (INV-14).

    Every uncertainty answers `False` -- ctranslate2 absent, the query raising,
    an exception of another class. "Not determined" reports as `crashed`, which
    says the child died and points at the stderr file, rather than as a specific
    cause nobody established.

    It runs in the child because this is the interpreter that HAS ctranslate2;
    the parent cannot import it and must not try (R8/R9).
    """
    if not isinstance(exc, ValueError):
        return False
    try:
        import ctranslate2                              # noqa: PLC0415
    except Exception:                                   # noqa: BLE001
        return False
    devices = ("cuda", "cpu") if device == "auto" else (device,)
    for dev in devices:
        try:
            supported = ctranslate2.get_supported_compute_types(dev)
        except Exception:                               # noqa: BLE001
            continue                     # that device is not usable here at all
        if compute_type in supported:
            return False                 # the machine can; the failure is other
    return True


class LanguageSetModel:
    """The ctranslate2 Whisper model, with `detect_language` narrowed to a set (I-28).

    faster-whisper 1.2.1 has no "choose only among these languages" parameter:
    both its file-level detection and its per-window path (`multilingual=True`,
    `transcribe.py:1192-1198`) take `results[0][0]`, the top language of ALL
    of them. Both go through `WhisperModel.model.detect_language`, so this is the
    one point where a narrowed answer reaches both. Everything else is passed
    through untouched. Probabilities are the model's own, not renormalised: the
    reported `language_probability` stays a number the model actually produced.
    """

    def __init__(self, inner, allowed) -> None:
        self._inner = inner
        self._allowed = frozenset(allowed)

    def detect_language(self, *args, **kwargs):
        out = []
        for per_item in self._inner.detect_language(*args, **kwargs):
            kept = [(tok, p) for tok, p in per_item
                    if tok[2:-2] in self._allowed]
            # Never empty: the codes were checked against the model before the
            # call, so an empty list here would be a library change, and the top
            # language of all of them is a louder failure than an IndexError.
            out.append(kept or list(per_item))
        return out

    def __getattr__(self, name):
        return getattr(self._inner, name)


def main(argv: list[str]) -> int:
    global _sink, _cancel_file
    request = json.loads(sys.stdin.readline())
    progress_path = request.get("progress_path")
    _cancel_file = (pathlib.Path(request["cancel_path"])
                    if request.get("cancel_path") else None)
    interval = float(request.get("heartbeat_seconds") or 0.5)

    if progress_path:
        pathlib.Path(progress_path).parent.mkdir(parents=True, exist_ok=True)
        _sink = open(progress_path, "w", encoding="utf-8", buffering=1)
        threading.Thread(target=watch_cancel_file, args=(interval,),
                         daemon=True).start()
    else:
        _sink = sys.stdout
        # NOT the stdin watcher, not yet -- see `watch_stdin`. Until the model is
        # loaded, the only thing watching is the parent-alive poll.
        threading.Thread(target=watch_parent, args=(interval,), daemon=True).start()

    duration = request.get("duration_seconds")
    device = request.get("device", "auto")
    compute_type = request.get("compute_type", "float16")
    # The phase the failure handler will name. Tracked rather than hardcoded: a
    # crash while loading the model used to report `during decode`, which sends
    # the reader to the wrong half of the run.
    phase = "load-model"
    try:
        # Imported here, not at module scope: the import itself takes seconds, and
        # the parent's first-event deadline should be measured against a child
        # that has already said "I am loading", not against an unexplained silence.
        emit(EV_PHASE, phase="load-model", model=os.path.basename(
            str(request.get("model_path", ""))))
        stop = threading.Event()
        threading.Thread(target=heartbeat, args=("load-model", interval, None, stop),
                         daemon=True).start()
        t = time.monotonic()
        from faster_whisper import WhisperModel        # noqa: E402
        wm = WhisperModel(request["model_path"], device=device,
                          compute_type=compute_type)
        load_seconds = time.monotonic() - t
        stop.set()

        # Now, and not before: the model is constructed, so a blocked stdin read
        # can no longer deadlock it. From here on a cancel arrives the fast way.
        if not progress_path:
            threading.Thread(target=watch_stdin, daemon=True).start()

        if _cancel.is_set():
            emit(EV_CANCELLED, phase="load-model")
            return 0

        # I-28: narrow language detection to the asked set, checked against what
        # this model can name first -- a code it does not know would otherwise
        # narrow to nothing and silently fall back to every language.
        language_set = request.get("language_set")
        unrestricted = wm.model
        if language_set:
            unknown = sorted(set(language_set) - set(wm.supported_languages))
            if unknown:
                raise ValueError(f"language set names codes this model does "
                                 f"not know: {unknown}")
            wm.model = LanguageSetModel(unrestricted, language_set)
        # I-27: VAD overrides only; `None` keeps the library's own VadOptions.
        vad_parameters = {k: v for k, v in (
            ("threshold", request.get("vad_threshold")),
            ("min_silence_duration_ms", request.get("vad_min_silence_ms")),
            ("speech_pad_ms", request.get("vad_speech_pad_ms")))
            if v is not None} or None

        # The indivisible call. Heartbeat only, and the estimate comes from the
        # duration the parent measured with ffprobe -- not from the engine, which
        # has been seen reporting a length 10.00 s longer than the audio (E-2).
        phase = "transcribe-call"
        emit(EV_PHASE, phase="transcribe-call")
        estimate = (duration * 0.0029) if duration else None
        stop = threading.Event()
        threading.Thread(target=heartbeat,
                         args=("transcribe-call", interval, estimate, stop),
                         daemon=True).start()
        t = time.monotonic()
        segments, info = wm.transcribe(
            request["audio"],
            language=request.get("language"),
            beam_size=int(request.get("beam_size", 5)),
            vad_filter=bool(request.get("vad", True)),
            vad_parameters=vad_parameters,
            # E-1. Explicit, always, and never left to the engine's default.
            condition_on_previous_text=bool(
                request["condition_on_previous_text"]),
            # E-3(a). Same rule as E-1: explicit, always. The library default is
            # False; the product's decision is carried in the request.
            multilingual=bool(request["multilingual"]),
        )
        call_seconds = time.monotonic() - t
        stop.set()

        total = duration or getattr(info, "duration", None)
        phase = "decode"
        emit(EV_PHASE, phase="decode", done=0.0, total=total,
             unit="audio-seconds")
        idx = 0
        for seg in segments:
            if _cancel.is_set():
                # Between segments -- the only place this check can happen.
                emit(EV_CANCELLED, phase="decode")
                return 0
            emit(EV_SEGMENT, idx=idx, start=seg.start, end=seg.end, text=seg.text,
                 avg_logprob=seg.avg_logprob, temperature=seg.temperature,
                 compression_ratio=seg.compression_ratio,
                 no_speech_prob=seg.no_speech_prob)
            idx += 1

        # The language-slot pass (§5.5.1). AFTER the decode loop, because the
        # generator is lazy and the segments must be drained before the model is
        # asked to do anything else. It re-decodes the audio to 16 kHz rather than
        # reusing the array `transcribe()` built internally: that array is not
        # exposed, and reaching into a library's private state to save 1.34 s is
        # how a pinned version becomes a requirement.
        slots_ran = bool(request.get("language_slots", True))
        # The slots are the SECOND language signal, so they vote over every
        # language: narrowed to the set, an out-of-set language could never show
        # up as a disagreement, which is the one thing they are there to show.
        wm.model = unrestricted
        if slots_ran:
            from faster_whisper import decode_audio        # noqa: E402
            samples = decode_audio(request["audio"], sampling_rate=SAMPLE_RATE)
            n = slot_count(len(samples))
            phase = "language-slots"
            emit(EV_PHASE, phase="language-slots", done=0.0, total=float(n),
                 unit="slots")
            for i in range(n):
                if _cancel.is_set():
                    # Between slots -- section 8.2 names this poll point beside
                    # the between-segments one.
                    emit(EV_CANCELLED, phase="language-slots")
                    return 0
                a = int(i * SLOT_SECONDS * SAMPLE_RATE)
                b = min(int((i + 1) * SLOT_SECONDS * SAMPLE_RATE), len(samples))
                lang, prob, _all = wm.detect_language(audio=samples[a:b])
                emit(EV_SLOT, index=i, start=i * SLOT_SECONDS,
                     language=lang, probability=float(prob))
                emit(EV_BEAT, phase="language-slots", done=float(i + 1),
                     total=float(n), unit="slots")

        # `vad_enabled` is what was actually asked for, and it is reported even
        # when false, so that "the engine said nothing" stays distinguishable from
        # "it ran without VAD" (E-2's three branches). `language_slots` is here for
        # exactly the same reason: an empty slot list means either "not asked" or
        # "asked and it produced nothing", and without this field the parent cannot
        # tell those apart -- it would have to trust that its own request reached a
        # child that honoured it, which is the assumption E-1 was.
        emit(EV_REPORT,
             vad_kept=getattr(info, "duration_after_vad", None),
             vad_enabled=bool(request.get("vad", True)),
             language_slots=slots_ran,
             condition_on_previous_text=bool(
                 request["condition_on_previous_text"]),
             multilingual=bool(request["multilingual"]),
             language_set=sorted(language_set) if language_set else None,
             vad_parameters=(vad_parameters
                             if bool(request.get("vad", True)) else None),
             duration=getattr(info, "duration", None),
             language=getattr(info, "language", None),
             language_probability=getattr(info, "language_probability", None),
             model_load_seconds=round(load_seconds, 3),
             call_seconds=round(call_seconds, 3))
        emit(EV_DONE, phase="decode")
        return 0
    except Exception as e:                       # noqa: BLE001
        # **stderr first, flushed, and only then the event.** The event is what makes
        # the parent stop waiting -- it breaks its loop and calls `proc.terminate()`
        # at once -- so a child that announces the failure before recording it is
        # racing the terminate, and it loses: the parent then reports a failure whose
        # `stderr_path` names an EMPTY file. Observed on this machine when the GPU
        # became unavailable (`ValueError: Requested float16 compute type…`): the
        # refusal was correct, the one place it told the reader to look said nothing.
        #
        # The event still carries the class name and nothing else. The message can
        # hold a path or a fragment of audio metadata, so it goes to the file the
        # parent named, which is where detail belongs (INV-14, INV-11).
        print(f"{type(e).__name__}: {e}", file=sys.stderr, flush=True)
        # One code the parent may act on, and it is a MEASURED capability rather
        # than a reading of the message above (A-22, user ruling UAT A-8). It is
        # the difference between "the child died, go read a file" and "this
        # machine cannot do this arithmetic, and here is the one that it can" --
        # the second is a question worth asking a person, the first is not.
        code = ("compute-unsupported"
                if compute_unsupported(e, device, compute_type) else "crashed")
        emit(EV_ERROR, code=code, phase=phase, kind=type(e).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
