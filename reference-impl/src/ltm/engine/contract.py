"""The wire between the product and the sidecar. Stdlib only, both sides.

This module is imported by the product AND read by `sidecar.py`, which runs under
a different interpreter -- so it may depend on nothing but the standard library,
and `sidecar.py` re-declares the event names rather than importing this file
across the interpreter boundary (a child that cannot start because an import
failed cannot report why it did not start).

**One line of JSON per event, and the vocabulary is closed.** A free-form channel
would be the easiest place for a schema to drift, and drift here is invisible: the
parent would keep reading lines and silently stop understanding some of them.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

#: The speech runtime lives inside the checkout, in `runtime/` (git-ignored), so
#: the defaults are derived from this file's place in the tree rather than written
#: as an absolute path -- a hardcoded path is what makes a product unrunnable on
#: the next machine, and what left the runtime ownerless in a sibling folder until
#: 2026-09-19. Both are overridable; outside a checkout (an installed wheel) the
#: derived path does not exist and the decode fails as `interpreter-missing` /
#: `model-missing`, which is the signal to set the variables.
SIDECAR_PYTHON_VAR = "LTM_SIDECAR_PYTHON"
MODEL_DIR_VAR = "LTM_MODEL_DIR"
RUNTIME_DIR = Path(__file__).resolve().parents[3] / "runtime"
DEFAULT_SIDECAR_PYTHON = str(RUNTIME_DIR / "asr-py" / "Scripts" / "python.exe")
DEFAULT_MODEL_DIR = str(RUNTIME_DIR / "models" / "faster-whisper-large-v3")


def sidecar_python() -> str:
    return os.environ.get(SIDECAR_PYTHON_VAR) or DEFAULT_SIDECAR_PYTHON


def model_dir() -> str:
    return os.environ.get(MODEL_DIR_VAR) or DEFAULT_MODEL_DIR


#: Child -> parent. Closed set; the parent refuses an unknown name rather than
#: ignoring it, because "ignored one event kind" is how a progress bar goes quiet
#: for a reason nobody can find.
EVENTS = ("phase", "beat", "segment", "slot", "report", "done", "error",
          "cancelled")

#: Parent -> child, on stdin. Line 1 is always the request; after that, control.
CONTROL_CANCEL = "cancel"


class ContractError(ValueError):
    """A line on the wire is not something this contract describes."""


@dataclass(frozen=True, slots=True)
class EngineRequest:
    """What the parent asks for. Every field is sent, always.

    **`condition_on_previous_text` has no `None` and no "leave it to the engine"
    (E-1).** The whole finding behind E-1 is that the upstream product never
    passed this parameter, so it ran at faster-whisper's default (on) while the
    documentation argued about a value nobody was sending. A field with a default
    of `False` that is *always serialised* makes "not passed" impossible rather
    than merely discouraged, and `_origin.json` records the value that was sent.
    """

    audio: str
    language: Optional[str] = None
    vad: bool = True
    condition_on_previous_text: bool = False
    #: E-3 (PIM v1.3 section 2.45). Per-window language detection inside the
    #: engine, and it has no `None` for E-1's reason: the library default is
    #: `False` and the shipped decision is `True`. Measured on the bilingual
    #: corpus recording: 88 -> 7,290 Chinese characters, zero measurable change on
    #: two monolingual ones, 0.965x the time (`docs/multilingual-verdict-2026-09-11.md`).
    #: Mutually exclusive with a pinned `language` -- see `__post_init__`.
    multilingual: bool = True
    beam_size: int = 5
    model_path: str = field(default_factory=model_dir)
    compute_type: str = "float16"
    device: str = "auto"
    #: From `ffprobe`, never from the engine (E-2, and §8.1: a bar had better
    #: stall at 99% than reach 103%). `None` means the caller has no denominator
    #: yet, which the child reports as a phase with no total rather than guessing.
    duration_seconds: Optional[float] = None
    #: Alternative channel (§8.3). Both `None` -> the primary channel: progress on
    #: stdout, cancel on stdin. `engine/` never names these files; the caller,
    #: which may import `store`, hands them in.
    progress_path: Optional[str] = None
    cancel_path: Optional[str] = None
    heartbeat_seconds: float = 0.5
    #: The per-slot `detect_language` pass (§5.5.1). On by default, because
    #: `Stretch.agreement` has no second signal without it and a `Stretch` without
    #: agreement is the field the product exists to print. It costs one extra
    #: encoder pass per slot -- 21.7 s against a 189.8 s decode, 11% -- and it is
    #: a request field rather than an assumption so that the cost is a decision
    #: somebody made and `_origin.json` can record which way.
    language_slots: bool = True
    #: I-28. The languages per-window detection may choose from, e.g.
    #: `("en", "zh")`. `None` = every language the model knows (the shipped
    #: default). faster-whisper 1.2.1 has no parameter for this -- its per-window
    #: path takes the top language of ALL of them (`transcribe.py:1192-1198`) --
    #: so the child narrows the model's own `detect_language` answer to this set.
    #: Only meaningful with `multilingual=True`; a pinned `language` is a set of
    #: one already and the two are refused together.
    language_set: Optional[tuple[str, ...]] = None
    #: I-27. Silero VAD knobs, each `None` = the library default (0.5 / 2000 ms /
    #: 400 ms in faster-whisper 1.2.1's `VadOptions`). Sent only as overrides, so
    #: a request that sets none of them is the decode every earlier record made.
    vad_threshold: Optional[float] = None
    vad_min_silence_ms: Optional[int] = None
    vad_speech_pad_ms: Optional[int] = None

    def vad_parameters(self) -> Optional[dict[str, Any]]:
        """The VadOptions overrides this request carries, or `None` for none."""
        out = {k: v for k, v in (("threshold", self.vad_threshold),
                                 ("min_silence_duration_ms",
                                  self.vad_min_silence_ms),
                                 ("speech_pad_ms", self.vad_speech_pad_ms))
               if v is not None}
        return out or None

    def __post_init__(self) -> None:
        if isinstance(self.language_set, list):
            # The wire carries a JSON list; the record keeps the tuple.
            object.__setattr__(self, "language_set", tuple(self.language_set))
        if self.language_set is not None:
            if not self.language_set:
                raise ContractError("an empty language set allows no language")
            if self.language is not None or not self.multilingual:
                raise ContractError(
                    "a language set narrows PER-WINDOW detection (I-28); it needs "
                    "multilingual=True and no pinned language. A pin is already "
                    "a set of one.")
        if self.vad_parameters() is not None and not self.vad:
            raise ContractError(
                "VAD knobs were set on a decode that turns VAD off; one of the "
                "two requests would be silently ignored")
        if self.vad_threshold is not None and not 0.0 < self.vad_threshold < 1.0:
            raise ContractError(f"vad threshold must be in (0, 1): {self.vad_threshold}")
        for name in ("vad_min_silence_ms", "vad_speech_pad_ms"):
            v = getattr(self, name)
            if v is not None and (not isinstance(v, int) or v < 0):
                raise ContractError(f"{name} must be a non-negative integer: {v!r}")
        if not isinstance(self.condition_on_previous_text, bool):
            raise ContractError(
                "condition_on_previous_text must be a bool and must be sent "
                "explicitly (E-1); the engine's own default is the thing this "
                "field exists to stop us relying on")
        if not isinstance(self.multilingual, bool):
            raise ContractError(
                "multilingual must be a bool and must be sent explicitly (E-3(a)); "
                "'not passed' would run at the library default, which is the "
                "decode that wrote zero Chinese characters over Mandarin speech")
        if self.multilingual and self.language is not None:
            # E-3(f). The engine's per-window detection has no guard for a pinned
            # language: it rewrites the tokenizer's language anyway, while the
            # reported language stays the pinned one at probability 1.0 -- so the
            # pin is overridden and the record says it took. Measured: pinning
            # `ja` produced 189 kana with the flag off and 0 with it on.
            raise ContractError(
                "a pinned language and per-window language detection cannot both "
                "be asked for (E-3(f)): the detection silently overrides the pin. "
                "Pin with multilingual=False, or leave the language unpinned.")
        if self.heartbeat_seconds <= 0:
            raise ContractError("a heartbeat needs a positive interval")
        if self.duration_seconds is not None and self.duration_seconds <= 0:
            raise ContractError(f"duration: {self.duration_seconds}")
        if (self.progress_path is None) != (self.cancel_path is None):
            raise ContractError(
                "the alternative channel needs both a progress file and a cancel "
                "sentinel, or neither. Half of it is a channel that reports and "
                "cannot be stopped, or stops and cannot report.")

    @property
    def uses_files(self) -> bool:
        return self.progress_path is not None

    def to_line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False) + "\n"

    @classmethod
    def from_line(cls, line: str) -> "EngineRequest":
        data = json.loads(line)
        unknown = set(data) - set(cls.__slots__)
        if unknown:
            raise ContractError(f"unknown request fields: {sorted(unknown)}")
        return cls(**data)


@dataclass(frozen=True, slots=True)
class RawSegment:
    """One decoded segment, exactly as the engine gave it.

    Deliberately not a `core.Cue`: normalising is the runner's job and it needs
    the metric fields to build `Window`s. A type that arrived pre-normalised would
    have thrown away the grouping the window population is counted over.
    """

    idx: int
    start: float
    end: float
    text: str
    avg_logprob: float
    temperature: float
    compression_ratio: float
    no_speech_prob: float


@dataclass(frozen=True, slots=True)
class RawSlot:
    """One 30-second language slot's audio-side vote (§5.5.1).

    It carries the engine's OWN language code, not `core.Language`: the encoder can
    name a language this product has no label for (it returns `ms` for English
    speech in the baseline recording), and coercing that into `undetermined` here
    would turn a measurement into an admission of ignorance. `detect.langmap` owns
    the alias, and it owns it in one place.
    """

    index: int
    start: float
    language: str
    probability: float


@dataclass(frozen=True, slots=True)
class EngineResult:
    """What the child finished with. Not a `Recording` -- see `RawSegment`."""

    segments: tuple[RawSegment, ...]
    #: Empty when the pass was not asked for, AND empty when it was asked for and
    #: delivered nothing. `language_slots` below is what tells those two apart, and
    #: the child reports it for the same reason it reports
    #: `condition_on_previous_text`: E-1 was a parameter nobody sent while the
    #: document argued about its value. A caller must never read an empty tuple as
    #: "the audio side disagreed everywhere" -- that is BR-4's `undetermined`, a
    #: different fact with a different spelling.
    slots: tuple[RawSlot, ...]
    #: What the CHILD says it did, not what the parent asked. `None` means the child
    #: never said -- an older sidecar, or one that died before its report.
    language_slots: Optional[bool]
    vad_kept: Optional[float]
    vad_enabled: Optional[bool]
    condition_on_previous_text: Optional[bool]
    #: E-3(c): the child's word, `None` when it said nothing -- never the request.
    multilingual: Optional[bool]
    duration: Optional[float]
    language: Optional[str]
    language_probability: Optional[float]
    model_load_seconds: Optional[float] = None
    call_seconds: Optional[float] = None
    #: Seconds by which the engine's LAST segment ended past the duration
    #: `ffprobe` measured. `0.0` means it did not.
    #:
    #: It exists because the two numbers come from different places and the
    #: engine's is not authoritative -- E-2 recorded the engine reporting a
    #: duration 10.00 s LONGER than the audio, and this is the same disagreement
    #: seen from the timestamp side. Measured on a 51-minute recording: 0.44 s
    #: past the end, which the progress guard correctly called impossible and
    #: which used to end the run (A-23). Kept as a number rather than dropped:
    #: a decode whose timestamps run past the end of the file is a fact about
    #: that decode, and the last cue's `end` is what a caller seeks with.
    timestamps_past_end: float = 0.0
    #: I-28 / I-27: what the CHILD says it used -- the language set it narrowed
    #: detection to and the VAD overrides it passed. `None` = it said nothing
    #: (an older sidecar) or nothing was asked; the request is not evidence.
    language_set: Optional[tuple[str, ...]] = None
    vad_parameters: Optional[Mapping[str, Any]] = None


@dataclass(frozen=True, slots=True)
class EngineFailure:
    """A failure that names only what it determined (INV-14).

    Two rules, and both of them are about what this type does NOT carry:

    * **no guessed cause.** `code` is drawn from a closed set of things the parent
      can actually observe. The one failure mode this project has on record as
      unexplained (`docs/evidence/sidecar_streaming_2026-09-09.md`) reports as
      `silent`, which says what was seen -- no output before the deadline -- and
      not why.
    * **no captured text inline.** The child's stderr goes to a FILE and this
      carries the path. `path` is on INV-12's allowlist; a `stderr` string field
      would be a free-text channel out of the engine, and the engine is the one
      component that has the transcript in memory.
    """

    code: str
    phase: Optional[str] = None
    channel: Optional[str] = None
    exit_code: Optional[int] = None
    stderr_path: Optional[str] = None
    detail: Mapping[str, float] = field(default_factory=dict)

    #: `no-segments` was here until 2026-09-27 (I-22) and is gone on purpose: an
    #: empty decode is a silent recording answered correctly, not a failure.
    #: `no-report` took over the one case it was right about -- a child that ended
    #: with no words AND without the report every finished decode sends -- and it
    #: names what was seen missing, not the absence of words, which is also what a
    #: silent recording looks like.
    CODES = ("silent", "exited", "crashed", "unparsable", "no-report",
             "interpreter-missing", "audio-missing", "model-missing",
             "vad-kept-impossible", "probe-missing", "probe-failed",
             # Observable, and that is why it is allowed to exist: the child asks
             # ctranslate2 which compute types this machine supports and compares.
             # It is NOT read out of the exception's wording -- a code derived
             # from a message is a theory about what went wrong, which is the one
             # thing this set forbids (A-22).
             "compute-unsupported",
             # A cancel this parent ASKED for, answered by the child stopping.
             # It used to come back as `exited`, which is indistinguishable from
             # the engine dying -- so a person who pressed cancel was told their
             # run had crashed, and any surface reading the code had to GUESS
             # from the fact that it had sent a cancel. Guessing is precisely
             # what R13 forbids: the request and the answer are separate, and
             # the answer has to be the child's. Found 2026-09-16 while building
             # the page that starts decodes (PSM section 19, card GUI-1).
             "cancelled")

    def __post_init__(self) -> None:
        if self.code not in self.CODES:
            raise ContractError(
                f"{self.code!r} is not an observable failure; one of "
                f"{list(self.CODES)}. A new code means a new thing the parent can "
                f"see, not a new theory about what went wrong.")

    def message(self) -> str:
        parts = [f"engine failed: {self.code}"]
        if self.phase:
            parts.append(f"during {self.phase}")
        if self.channel:
            parts.append(f"on the {self.channel} channel")
        if self.exit_code is not None:
            parts.append(f"exit {self.exit_code}")
        if self.stderr_path:
            parts.append(f"stderr in {self.stderr_path}")
        return "; ".join(parts)


def parse_event(line: str) -> dict[str, Any]:
    """One line from the child. Unknown event names are refused, not skipped."""
    line = line.strip()
    if not line:
        raise ContractError("empty line")
    try:
        data = json.loads(line)
    except json.JSONDecodeError as e:
        raise ContractError(f"not JSON: {line[:120]!r} ({e})") from e
    if not isinstance(data, dict) or "ev" not in data:
        raise ContractError(f"no event name: {line[:120]!r}")
    if data["ev"] not in EVENTS:
        raise ContractError(f"unknown event {data['ev']!r}; one of {list(EVENTS)}")
    return data
