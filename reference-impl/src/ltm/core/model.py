"""The data model: frozen dataclasses, no behaviour beyond their own rules.

PSM section 4. Every rule a type carries names the invariant it carries, and it
is enforced in `__post_init__` rather than left to a caller's memory -- a rule
that lives in prose is only as strong as the next reader's attention.

Four layers, three of them present in milestone 1: `Stretch` > `Window` > `Cue`.
The `Token` layer (PIM section 2.4, T-1..T-3) is milestone 2 and is deliberately
absent -- a dataclass nothing produces is an orphan, and a test asserting a
condition that cannot arise is green forever.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Mapping, Optional, Sequence

if TYPE_CHECKING:                      # annotations only; core.editscript
    from .editscript import EditOp     # imports THIS module, never the reverse

# The layers a ratio may be counted over. `token` is absent on purpose (see the
# module docstring); adding it is a milestone-2 edit, not a default.
LAYERS = ("stretch", "window", "cue")
Layer = Literal["stretch", "window", "cue"]

Language = Literal["zh", "en", "undetermined"]

# BR-4's criterion, as a number with a name. It is a DEFINITION -- "no label has
# majority support from two independent signals" -- and not a tuned threshold:
# there is no ground truth for this recording, so a swept optimum would only
# measure how alike the two signals are. Do not "improve" it with a sweep.
NO_MAJORITY = 0.5


class ModelError(ValueError):
    """A type was handed something its own rule forbids.

    A distinct class because these are programming errors in the caller, not
    runtime conditions of the recording: nothing in the pipeline is expected to
    catch one and continue.
    """


@dataclass(frozen=True, slots=True)
class Rate:
    """A ratio that cannot exist without naming its population (INV-1).

    A helper function taking `population=` would still let a caller compute
    `a / b` by hand and print it. A TYPE cannot be constructed without the
    population and the layer, so a rate that reaches an output has already
    answered "out of what?".
    """

    count: int
    population: int
    layer: Layer

    def __post_init__(self) -> None:
        if self.layer not in LAYERS:
            raise ModelError(f"layer must be one of {LAYERS}: {self.layer!r}")
        if self.population <= 0:
            raise ModelError(
                "a rate over an empty population is not a rate; report the "
                f"count alone (count={self.count}, layer={self.layer})"
            )
        if not 0 <= self.count <= self.population:
            raise ModelError(
                f"count {self.count} outside population {self.population}"
            )

    @property
    def value(self) -> float:
        return self.count / self.population

    def __str__(self) -> str:  # the population travels with the number
        return f"{self.count}/{self.population} {self.layer} ({self.value:.4f})"


@dataclass(frozen=True, slots=True)
class Span:
    """A character range inside one `Cue`. Source coordinates always (INV-5)."""

    cue_id: str
    char_start: int
    char_end: int

    def __post_init__(self) -> None:
        if self.char_start < 0 or self.char_end < self.char_start:
            raise ModelError(
                f"empty or negative span: [{self.char_start}, {self.char_end})"
            )

    def __len__(self) -> int:
        return self.char_end - self.char_start


@dataclass(frozen=True, slots=True)
class Cue:
    id: str
    start: float
    end: float
    text: str
    window_id: str
    stretch_id: str

    def __post_init__(self) -> None:
        # Every Cue knows which window and which stretch it sits in (INV-1):
        # this is what lets a rate name its population later without guessing.
        if not self.window_id or not self.stretch_id:
            raise ModelError(
                f"cue {self.id!r} must name its window and stretch (INV-1)"
            )
        if self.end < self.start:
            raise ModelError(f"cue {self.id!r} ends before it starts")


@dataclass(frozen=True, slots=True)
class Window:
    """A run of equal decode metrics. Engineering metrics only.

    NOTE, because two things were once both called "window" (round 7 RF-2):
    this is NOT the 30-second language slot. It has no language field, so
    language voting cannot be a walk over `Window` objects. The language map's
    unit is `detect.langmap.LangSlot`, a different population on the same
    recording (73 slots against 79 windows on the baseline).
    """

    id: str
    start: float
    end: float
    avg_logprob: float
    temperature: float
    compression_ratio: float
    no_speech_prob: float


@dataclass(frozen=True, slots=True)
class Stretch:
    """A passage one language dominates, with how sure the product is (BR-4)."""

    id: str
    start: float
    end: float
    language: Language
    agreement: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.agreement <= 1.0:
            raise ModelError(f"agreement outside [0, 1]: {self.agreement}")
        # BR-4 lives in the type. `< 0.5` and `undetermined` are two spellings
        # of one fact, and a caller that sets one without the other has made
        # the product claim a language it cannot tell apart -- or hide one it
        # can. Milestone 1's degradation order (PSM section 14) warns about
        # exactly this: a whole-file agreement of 0.795 PASSES 0.5 and would
        # emit zero `undetermined` where five of eighteen are due.
        undetermined = self.language == "undetermined"
        if (self.agreement < NO_MAJORITY) != undetermined:
            raise ModelError(
                f"BR-4: agreement {self.agreement} and language "
                f"{self.language!r} disagree; below {NO_MAJORITY} the language "
                f"must be 'undetermined' and at or above it must not be"
            )


@dataclass(frozen=True, slots=True)
class EngineReport:
    """Exactly five fields (PIM section 2.1), and no sixth.

    The fifth, `multilingual`, arrived with E-3 (PIM v1.2/v1.3): it is a decode
    CONDITION beside `vad_enabled` and `condition_on_previous_text`, and it is what
    says how `languageDetected` in the decode record may be read. The two language
    rows §2.1 lists beside it stay in `_origin.json`'s per-revision decode record
    (PSM section 17.0 (1)): neither is a detection input.

    Three fields a previous draft carried are gone and must not come back:
    `longestRepeatRun` and `repeatedText` were claimed as S3's source and are
    not -- they are file-level scalars with no position, so they cannot build a
    `Span`, and a file with two qualifying runs cannot even express the count.
    `temperatureFallbacks` was defined and never consumed. `model` and
    `computeType` belong in `_origin.json`, which is a record, not a contract
    input.
    """

    vad_kept: Optional[float]
    # E-2 has THREE branches, so this is not a plain bool (PIM section 11:
    # `vadEnabled` is what 「分辨『沒回報』與『那一次沒跑』」). `None` = the engine
    # said nothing about VAD; `False` = it ran without VAD. A bool here would
    # collapse the two and make branch (c) inexpressible -- the same class of
    # defect as E-1's on the field below.
    vad_enabled: Optional[bool]
    # E-1: `None` means the engine did not report it. "Not passed" must stay
    # distinguishable from "passed as False", or the flag cannot be audited.
    condition_on_previous_text: Optional[bool]
    # E-3(c): whether the engine re-detected the language per decode window. Same
    # three values as the field above, same reason: `None` = never reported, which
    # is every plan written before 2026-09-16 -- they ran at the library default,
    # but the record never said so and is not back-filled.
    multilingual: Optional[bool]
    degenerated: bool

    def vad_kept_is_trustworthy(self) -> bool:
        """E-2(a): `vad_kept` means nothing unless VAD is known to have run.

        Measured contradiction on record: the engine once reported a VAD-kept
        duration 10.00 s LONGER than the audio, on a `--no-vad` run
        (`docs/evidence/vadseconds_2026-09-09.py`). The excess was exactly 10.00,
        not 9.87 -- a value something fills in when VAD is off, not a measurement.
        So the defect was never "the engine computed it wrong"; it was a contract
        asserting an impossibility about a field that has no meaning here.
        """
        return self.vad_enabled is True and self.vad_kept is not None

    def vad_verdict(self) -> str:
        """Which of E-2's three branches this report is in.

        Named rather than left to each caller's `if`, because branches (b) and (c)
        differ in what they license: (b) knows VAD did not run, (c) does not know
        anything. Both refuse to consume `vad_kept`; only (c) is missing
        information someone could go and get.
        """
        if self.vad_enabled is True:
            return "trustworthy" if self.vad_kept is not None else "enabled-but-silent"
        if self.vad_enabled is False:
            return "did-not-run"
        return "not-reported"


@dataclass(frozen=True, slots=True)
class Recording:
    id: str
    path: str
    duration: float
    sha256: str
    report: EngineReport
    stretches: tuple[Stretch, ...]
    windows: tuple[Window, ...]
    cues: tuple[Cue, ...]

    def __post_init__(self) -> None:
        if self.duration <= 0:
            raise ModelError(f"duration must be positive: {self.duration}")
        # The denominator for the decoding progress bar comes from `ffprobe`,
        # never from the engine (PSM section 8.1). Nothing here can enforce
        # that; what it CAN enforce is that a Recording knows its own duration.

    def rate_over_cues(self, count: int) -> Rate:
        return Rate(count, len(self.cues), "cue")

    def rate_over_windows(self, count: int) -> Rate:
        return Rate(count, len(self.windows), "window")

    def rate_over_stretches(self, count: int) -> Rate:
        return Rate(count, len(self.stretches), "stretch")


@dataclass(frozen=True, slots=True)
class Evidence:
    """Numbers only, and it knows which layer it was measured on.

    INV-12: no agent-facing response may carry a free-text field able to hold
    transcript content, so evidence is `(code -> number)` pairs, never prose.
    The codes are keys, not sentences.
    """

    layer: Layer
    values: tuple[tuple[str, float], ...]

    def __post_init__(self) -> None:
        if self.layer not in LAYERS:
            raise ModelError(f"layer must be one of {LAYERS}: {self.layer!r}")
        if not self.values:
            raise ModelError("evidence with no numbers is not evidence")

    @classmethod
    def of(cls, layer: Layer, **values: float) -> "Evidence":
        return cls(layer, tuple(sorted(values.items())))

    def as_dict(self) -> Mapping[str, float]:
        return dict(self.values)


@dataclass(frozen=True, slots=True)
class Finding:
    """A statement about the transcript that carries at least one action (I-3).

    A statement carrying no action is a `Note` -- a DIFFERENT TYPE, so the
    distinction cannot be lost by a careless caller.
    """

    id: str
    span: Span
    code: str
    severity: Literal["info", "warn", "high"]
    unit: Layer
    evidence: Evidence
    actions: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.actions:
            raise ModelError(
                f"finding {self.id!r} carries no action; it is a Note (I-3)"
            )
        # INV-2: window-level evidence produces ONE window-level finding, never
        # N cue-level ones. The unit and the evidence's layer are the same fact.
        if self.unit != self.evidence.layer:
            raise ModelError(
                f"INV-2: finding {self.id!r} is {self.unit}-level but rests on "
                f"{self.evidence.layer}-level evidence"
            )


@dataclass(frozen=True, slots=True)
class Note:
    """Something worth saying that nothing can be done about.

    One signal only (INV-6), or a finding with no available action (I-3). It is
    NOT a weaker finding: it cannot carry an `EditOp`, by type.
    """

    id: str
    span: Span
    code: str
    reason: Literal["single-signal", "no-action", "near-threshold"]


@dataclass(frozen=True, slots=True)
class PlanSummary:
    """The fields at least five rules demand a plan summary state (PIM 5.5).

    `detectors_skipped` and `detectors_failed` are TWO fields (round 7 RE-3).
    "Never enabled, because INV-9 has no control pair for it" and "enabled, ran,
    raised" are different facts, and BR-10 wants the second said out loud. The
    edge `detecting -> proposing` does not ask about detector health, so without
    a second field that failure has nowhere to land and the run looks identical
    to a clean one.
    """

    findings: Mapping[str, int]
    proposals: Mapping[str, int]
    notes: Mapping[str, int]
    unverified: Mapping[str, int]
    detectors_skipped: tuple[str, ...]
    detectors_failed: tuple[tuple[str, str], ...]  # (detector, reason)
    engine_health: Mapping[str, object]

    def __post_init__(self) -> None:
        # Absence and zero must stay distinguishable (PIM section 5.5):
        # `unverified["no-vad"] == 0` is "the gate ran and found nothing
        # ungated"; a missing key is "this round never reached that gate".
        # Nothing to enforce here -- the rule is that callers must not
        # substitute 0 for absence, and the tests check both spellings.
        pass

    @property
    def healthy(self) -> bool:
        return not self.detectors_failed


@dataclass(frozen=True, slots=True)
class Plan:
    run: str
    findings: tuple[Finding, ...]
    notes: tuple[Note, ...]
    script: tuple["EditOp", ...]   # core.editscript owns the element type
    summary: PlanSummary
    #: Dormant ops `accept` applies only when named (`--candidate N`), never by
    #: default; see `detect.detectors.Verdict.candidates`.
    candidates: tuple["EditOp", ...] = ()


@dataclass(frozen=True, slots=True)
class Revision:
    """One event in a run's chain, not one transcript.

    A declined round consumes a serial: "I looked and decided not to change it"
    and "I never ran" must not look the same on disk. `source` makes the chain
    explicit (`r002.source == "r001"`), which a timestamp cannot express --
    and Windows file times move on a ~15.6 ms tick, so two files written in one
    tick tie (P-73).
    """

    serial: str
    source: Optional[str]
    kind: Literal["decode", "edit", "declined"]

    def __post_init__(self) -> None:
        if not self.serial.startswith("r") or not self.serial[1:].isdigit():
            raise ModelError(f"serial must look like r000: {self.serial!r}")
        if self.kind == "decode" and self.source is not None:
            raise ModelError("a decode revision has no source revision")
        if self.kind != "decode" and self.source is None:
            raise ModelError(f"{self.kind} revision {self.serial} needs a source")


def stretch_of(cue: Cue, stretches: Sequence[Stretch]) -> Stretch:
    for s in stretches:
        if s.id == cue.stretch_id:
            return s
    raise ModelError(f"cue {cue.id!r} names stretch {cue.stretch_id!r}, absent")


def undetermined_stretches(rec: Recording) -> tuple[Stretch, ...]:
    """The passages the product admits it cannot tell apart (BR-4)."""
    return tuple(s for s in rec.stretches if s.language == "undetermined")
