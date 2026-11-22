"""A sidecar that decodes nothing. Standard library only, no product imports.

The real sidecar needs the ASR interpreter, a 3 GB model and ~6 s per call, none
of which belongs in an assertion about a one-second gap ceiling. This stub speaks
the same wire protocol under the SAME interpreter as the tests, so the channel
assertions are deterministic and fast.

What it deliberately does NOT abstract away: it heartbeats, it checks for cancel
only between segments (exactly like the real one, which cannot check inside the
`transcribe()` call), and it can be told to misbehave. The misbehaviours are the
negative controls from PSM section 8.1 -- a stub that could only behave correctly
would make every limit below green by construction.

Behaviour is set through the `LTM_STUB` environment variable holding a JSON
object, not through the request: `EngineRequest` refuses unknown fields, and that
is a property worth keeping rather than loosening for a fixture.

`LTM_STUB` keys (all optional):
    beats           how many heartbeats to emit before the first segment
    segments        how many segments to emit
    gap             seconds between events
    no_heartbeat    emit only on segment arrival -- limit (a)'s negative control
    no_denominator  send `decode` with total=None -- limit (b)'s
    ignore_cancel   receive the cancel and exit quietly -- limit (c)'s
    silent          emit nothing at all, then sleep (the unexplained stall)
    no_gpu          fail at load with `compute-unsupported`, the one engine
                    failure a person is asked to answer (A-22)
    crash           raise after `segments` segments
    slots           how many language slots to emit (default 2)
    slot_langs      the exact per-slot language codes, e.g. ["zh", "ms"]
    no_slots        skip the language-slot pass even though it was requested
    seg_seconds     how long each segment is (default 1.0)
    past_end        seconds to add to the LAST segment's end, so it finishes past
                    the measured duration -- the real engine does this (A-23)
    texts           the exact per-segment texts, so a caller can build a recording
                    whose TEXT side has a language boundary in it -- 20 letters of
                    one script then 20 of another. Without this the stub's texts are
                    all under `langmap.MIN_LETTERS` and every slot is undecidable,
                    which makes the language map untestable through the verbs: the
                    pipeline would run and produce one `undetermined` stretch
                    whatever the audio side said
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import threading
import time

CANCEL = threading.Event()
T0 = time.monotonic()
SINK = None


def emit(ev, **fields):
    fields["ev"] = ev
    fields.setdefault("elapsed", round(time.monotonic() - T0, 3))
    with LOCK:
        SINK.write(json.dumps(fields, ensure_ascii=False) + "\n")
        SINK.flush()


LOCK = threading.Lock()


def watch_stdin():
    for line in sys.stdin:
        if line.strip() == "cancel":
            CANCEL.set()
            return
    CANCEL.set()


def watch_file(path: pathlib.Path, interval: float):
    while not CANCEL.is_set():
        if path.exists():
            CANCEL.set()
            return
        time.sleep(interval)


def main() -> int:
    global SINK
    request = json.loads(sys.stdin.readline())
    # Behaviour comes from the environment, NOT from the request: the request is
    # the product's own contract and `EngineRequest` refuses unknown fields, which
    # is a property worth keeping rather than loosening for a test fixture.
    stub = json.loads(os.environ.get("LTM_STUB") or "{}")
    interval = float(request.get("heartbeat_seconds") or 0.2)
    gap = float(stub.get("gap", interval / 2))

    if request.get("progress_path"):
        p = pathlib.Path(request["progress_path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        SINK = open(p, "w", encoding="utf-8", buffering=1)
        threading.Thread(target=watch_file,
                         args=(pathlib.Path(request["cancel_path"]), gap),
                         daemon=True).start()
    else:
        SINK = sys.stdout
        threading.Thread(target=watch_stdin, daemon=True).start()

    if stub.get("silent"):
        time.sleep(float(stub.get("silent")))
        return 0

    # "This machine cannot do that arithmetic", raised where the real one raises
    # it: while loading the model, before anything has been decoded. The real
    # child decides this by asking ctranslate2 what the machine supports; the stub
    # is told, because the point of the stub is the PARENT's and the CLI's
    # behaviour after the event, which must not need a GPU to be absent to test.
    if stub.get("no_gpu"):
        emit("phase", phase="load-model", model="stub")
        print("ValueError: stub refuses this compute type",
              file=sys.stderr, flush=True)
        emit("error", code="compute-unsupported", phase="load-model",
             kind="ValueError")
        return 1

    n_seg = int(stub.get("segments", 3))
    total = request.get("duration_seconds") or float(n_seg)

    emit("phase", phase="load-model", model="stub")
    if not stub.get("no_heartbeat"):
        for _ in range(int(stub.get("beats", 2))):
            time.sleep(gap)
            emit("beat", phase="load-model")

    emit("phase", phase="transcribe-call")
    if not stub.get("no_heartbeat"):
        time.sleep(gap)
        emit("beat", phase="transcribe-call", estimated_seconds=round(total * 0.0029, 3))

    if stub.get("no_denominator"):
        # A `decode` phase with no total. The runner must refuse to build the
        # event, which is how limit (b) goes red rather than silently rendering a
        # bar with no bar.
        emit("phase", phase="decode")
    else:
        emit("phase", phase="decode", done=0.0, total=total, unit="audio-seconds")

    for i in range(n_seg):
        if CANCEL.is_set() and not stub.get("ignore_cancel"):
            emit("cancelled", phase="decode")
            return 0
        if CANCEL.is_set() and stub.get("ignore_cancel"):
            return 0                  # answer nothing and vanish
        if not stub.get("no_heartbeat"):
            time.sleep(gap)
        texts = stub.get("texts")
        if texts:
            text = texts[i % len(texts)]
        else:
            text = stub.get("text", "對對對") if i == 0 else "fine"
        step = float(stub.get("seg_seconds", 1.0))
        end = (i + 1) * step
        if i + 1 == n_seg:
            # The last segment may run past the end of the file, because the real
            # engine's timestamps do: measured 0.44 s past on a 51-minute
            # recording, against a duration `ffprobe` measured.
            end += float(stub.get("past_end", 0.0))
        emit("segment", idx=i, start=i * step, end=end,
             text=text,
             avg_logprob=-0.2 if i < 2 else -0.9,
             temperature=0.0 if i < 2 else 1.0,
             compression_ratio=1.2 if i < 2 else 3.2,
             no_speech_prob=0.01)
        if stub.get("crash") and i + 1 >= int(stub["crash"]):
            # The real sidecar's order: stderr FIRST and flushed, then the event that
            # makes the parent terminate us. `stderr_late` reproduces the order this
            # file used to have, with a sleep so the race is not a race -- that is the
            # known-true positive for "the failure names a file that has something in
            # it", and without it the assertion would pass on a child that got lucky.
            if stub.get("stderr_late"):
                emit("error", code="crashed", phase="decode", kind="StubCrash")
                time.sleep(2.0)
                print("StubCrash: written too late", file=sys.stderr, flush=True)
            else:
                print("StubCrash: the detail the parent points at",
                      file=sys.stderr, flush=True)
                emit("error", code="crashed", phase="decode", kind="StubCrash")
            return 1

    # The language-slot pass, on the same wire as the real one. `slots` is how many
    # to emit; `slot_langs` lets a test hand back a specific sequence, which is what
    # makes the audio side's ms->en alias and a partial pass testable without the
    # 3 GB model. `no_slots` runs the decode with the pass turned off, which is the
    # known-false control for "the audio side is required".
    slots_ran = bool(request.get("language_slots", True))
    if slots_ran and not stub.get("no_slots"):
        langs = stub.get("slot_langs") or ["zh"] * int(stub.get("slots", 2))
        emit("phase", phase="language-slots", done=0.0, total=float(len(langs)),
             unit="slots")
        for i, lang in enumerate(langs):
            if CANCEL.is_set() and not stub.get("ignore_cancel"):
                emit("cancelled", phase="language-slots")
                return 0
            if not stub.get("no_heartbeat"):
                time.sleep(gap)
            emit("slot", index=i, start=i * 30.0, language=lang,
                 probability=0.9)

    emit("report",
         vad_kept=stub.get("vad_kept", total),
         vad_enabled=stub.get("vad_enabled", bool(request.get("vad", True))),
         # What the child DID, so that `no_slots` (asked, delivered nothing) does
         # not read like `language_slots=False` (never asked). Same shape as
         # `vad_enabled`; same reason.
         language_slots=slots_ran,
         condition_on_previous_text=request["condition_on_previous_text"],
         # Echoed like the real child, and `silent_multilingual` makes it say
         # nothing -- the known-false control for "the record holds the child's
         # word, not the request's".
         multilingual=(None if stub.get("silent_multilingual")
                       else request.get("multilingual")),
         # A pinned language is echoed, an unpinned one detected -- which is what
         # the real engine does, and it is the only way a test can see that the pin
         # crossed the wire rather than merely reaching `EngineRequest`.
         # I-28 / I-27, echoed like the real child; `silent_settings` makes it say
         # nothing, the control for "the record holds the child's word".
         language_set=(None if stub.get("silent_settings")
                       else (sorted(request["language_set"])
                             if request.get("language_set") else None)),
         vad_parameters=(None if stub.get("silent_settings") else (
             {k: v for k, v in (
                 ("threshold", request.get("vad_threshold")),
                 ("min_silence_duration_ms", request.get("vad_min_silence_ms")),
                 ("speech_pad_ms", request.get("vad_speech_pad_ms")))
              if v is not None} or None)),
         duration=total, language=request.get("language") or "zh",
         language_probability=0.85,
         model_load_seconds=0.01, call_seconds=0.02)
    emit("done", phase="decode")
    return 0


if __name__ == "__main__":
    sys.exit(main())
