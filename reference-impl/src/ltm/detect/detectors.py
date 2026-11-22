"""The detectors, the `Plan` they produce, and the interface that keeps progress
honest.

PSM sections 6.4, 8.1 and 9's `detect` verb. `engine-loop` and `sparse-cue` are
the default set (the second since 2026-09-27, PSM section 20), and the other three
ship disabled because none of them has the control pair INV-9 demands. That is a rule about evidence, not about maturity: a
detector with no known-false control cannot be told apart from one that fires on
everything.

**A `Detector` advances per `Cue`** (RB-7, raised in round 4 and unclosed for
three rounds because the responsibility was never assigned to a module). An
interface that takes the whole transcript and returns a list cannot satisfy INV-22
limit (a), so that interface is illegal here. The rule is "advance per cue", not
"chunk if a scan exceeds one second", because the latter needs to know in advance
how long a scan will take.

**Two deviations from PSM v3's signature, both found by building it, both with the
reason the card asked for.** They are amendments to the card, not liberties:

1. The card writes `scan(cues, ctx) -> Iterator[Finding]`. A generator that yields
   only findings **cannot make limit (a) checkable**: on this project's own data a
   whole-sequence detector and a per-cue one both finish in milliseconds, so a
   timing assertion can never go red, and the card says so itself ("a rule that
   cannot fail on the data at hand can only be guaranteed in an interface"). So a
   `Step` carries **the index of the cue just consumed**, one step per cue, in
   order. The negative control -- a detector that consumes the whole sequence
   before its first yield -- then fails a rung-1 exhaustive check on the index
   sequence instead of a stopwatch.
2. `scan` takes the whole `Recording`, not `cues`. S4 measures **decoder state**,
   which lives on `Window` and is reached through `Cue.window_id`; a detector
   handed only cues could implement text signals and nothing else, and INV-6
   requires two independent signals of which exactly one is not textual. The
   card's own §6.2 makes S4 mandatory, so the narrower signature contradicts it.

Both are recorded in the PSM's amendment list rather than left as drift between a
document and a module, which is the shape this project has been blocked for twice.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import (Any, Callable, Iterator, Literal, Mapping, Optional, Protocol,
                    Sequence)

from ..core import editscript, model, progress
from . import candidates as cand
from . import lexicon
from . import score as scoring
from . import soundalike as sa
from . import scriptform, signals, thresholds

#: The default set: the detectors with a control pair from real audio (INV-9).
#: `sparse-cue` joined on 2026-09-27 after its pair held on the real engine --
#: `docs/evidence/sparse_cue_controls_2026-09-27.txt`. `uncovered-span` joined on
#: 2026-10-21 on stored real decodes (I-27's own recording flagged at 00:00-06:43,
#: four continuous ones quiet) -- `docs/evidence/uncovered_span_controls_2026-10-21.txt`.
DEFAULT = ("engine-loop", "sparse-cue", "uncovered-span")

#: Implemented, off, and each names what it is missing (§6.4).
UNCONTROLLED: Mapping[str, str] = {
    "speaker-repeat": "no known-false control: a speaker repeating for emphasis "
                      "and an engine looping are the same text",
    "filler": "no known-false control, and no measured declared list",
}
#: `glossary` left this table on 2026-10-30 with its control pair: seven real
#: errors of a real decode it must flag and seven right cues it must not
#: (`docs/evidence/glossary_controls_2026-10-30.json`, `tests/test_glossary.py`).
#: It is still not in `DEFAULT`: its list is the user's, so it runs when a
#: lexicon is named (`--lexicon`) or it is enabled by name.

#: The two detectors whose candidates are bounded by a user-declared list (INV-7).
DECLARED = ("filler", "glossary")

#: `detectors_failed`'s second element is one of these, never a sentence: the
#: field is machine-read and INV-12 keeps prose out of agent-facing output.
#:
#: **The three are the PIM's, not this module's**, and the citation is here because
#: a verifier could not tell whether the tuple was a transcription or an invention
#: (V-6). PIM §5.5's `detectorsFailed` row and §6.1's `inconclusive` row both give
#: the same list: 「缺輸入、缺可選層、擲出例外」 -- missing input, missing optional
#: layer, threw an exception. Milestone 1 can only produce the third, because the
#: only enabled detector needs nothing but `cues` and `windows`; the other two are
#: written down so that adding a detector with a real input does not invent a
#: fourth vocabulary at the point of failure.
FAILURE_REASONS = ("missing-input", "missing-optional-layer", "exception")

#: The markers `PlanSummary.unverified` always carries. Present-with-zero and
#: absent are different facts (INV-17), so the keys are written unconditionally
#: and this tuple is what makes "unconditionally" checkable.
UNVERIFIED_MARKERS = ("no-token-layer", "no-vad")

#: T-1 trigger (a): the user asks for word-level alignment. Milestone 1 has no
#: `Token` layer, so the request fails naming it.
TOKEN_LAYER = "token"


class DetectError(model.ModelError):
    """A detection request this build cannot honour, said out loud."""


@dataclass(frozen=True, slots=True)
class Verdict:
    """Everything one candidate produced, including the decision to drop it.

    A dropped candidate is a `Verdict` with no finding and no note rather than
    nothing at all: **"judged and rejected" and "never considered" must stay
    distinguishable**, which is the whole of RC-1. Before round 6 an S4-only
    candidate was absent from the output entirely while the document described its
    verdict, and nothing could see the difference.
    """

    #: `finding` / `note` / `dropped` -- what came OUT. Deliberately not
    #: `Scored.disposition`'s words: that one says what INV-6's signal count
    #: LICENSES, and a **deletion proposal is a finding that carries ops**, which
    #: is how `speaker-repeat` can produce findings and no proposals under INV-8.
    #: One vocabulary for the two would have to mean both things at once.
    disposition: Literal["finding", "note", "dropped"]
    code: str
    span: tuple[int, int]
    signals: int
    finding: Optional[model.Finding] = None
    note: Optional[model.Note] = None
    ops: tuple[editscript.EditOp, ...] = ()
    #: Dormant ops: written to the plan's `candidates[]`, never into `script`, so
    #: `accept` with no selection cannot apply them; only `accept --candidate N`
    #: does. A sound-alike is a guess (user ruling 「音近只標」), and this is how a
    #: picker -- a person, or an LLM the user consented to -- chooses among
    #: guesses by NUMBER, never by writing text (INV-12).
    candidates: tuple[editscript.EditOp, ...] = ()

    def __post_init__(self) -> None:
        has = (self.finding is not None, self.note is not None)
        want = {"finding": (True, False), "note": (False, True),
                "dropped": (False, False)}[self.disposition]
        if has != want:
            raise DetectError(
                f"{self.disposition} verdict on {self.span} carries "
                f"finding={has[0]} note={has[1]}; the three dispositions are "
                f"exclusive by INV-6")
        if self.ops and self.disposition != "finding":
            raise DetectError("only a finding carries edit ops")

    @property
    def proposes_deletion(self) -> bool:
        return any(op.op == "delete" for op in self.ops)


#: One yield per `Cue`, carrying the index of the cue just consumed. See the
#: module docstring for why the index is in the protocol.
Step = tuple[int, tuple[Verdict, ...]]


class Detector(Protocol):
    """Yields once per cue; the caller emits progress between yields."""

    name: str
    #: INV-8 as a property of the type. `False` means this detector's verdicts can
    #: never carry an `EditOp`, whatever flag turned it on.
    may_delete: bool

    def scan(self, rec: model.Recording,
             thr: thresholds.Thresholds) -> Iterator[Step]:
        ...


def _closing(cues: Sequence[model.Cue]) -> dict[int, tuple[int, int]]:
    """`last index of a group -> that group's bounds`.

    The grouping itself comes from `candidates.same_text_groups`, not from a copy
    of the predicate: a group's size and text are known only when it closes, which
    is also when a candidate can be nominated, and the QUALIFYING test stays in
    `candidates.run_qualifies`. Both halves of the rule therefore have exactly one
    definition, which is what the streaming walk was nearly the exception to.
    """
    return {b: (a, b) for a, b in cand.same_text_groups(cues)}


def _whole_cue_deletes(rec: model.Recording, a: int, b: int,
                       code: str) -> tuple[editscript.EditOp, ...]:
    """One delete per cue, each spanning that cue's whole text.

    A `Span` is a range inside ONE cue by definition (INV-5), so a nine-cue run is
    nine ops rather than one wide span. The `Finding` anchors on the first cue and
    states the extent as a number in its evidence; the ops are what actually
    describe the removal, and `test_detect.py` asserts the two agree so the anchor
    cannot silently under-delete.
    """
    ops = []
    for k in range(a, b + 1):
        cue = rec.cues[k]
        if not cue.text:
            # RAISES rather than skipping (verifier V-5). Skipping would leave
            # `len(ops)` one short of the finding's `cues` number with nothing to
            # notice it: the finding would claim a 27-cue span while proposing 26
            # deletions. The candidate rule cannot nominate an empty cue today --
            # S2 needs six characters and S3 needs one alphanumeric -- so this is
            # unreachable, and it is the reachable-tomorrow case that gets the
            # loud failure rather than the silent shortfall.
            raise DetectError(
                f"cue {cue.id!r} is in candidate {(a, b)} and has no text; a "
                f"zero-width delete removes nothing, and dropping it would make "
                f"the op count disagree with the finding's own extent")
        ops.append(editscript.EditOp(
            op="delete", span=model.Span(cue.id, 0, len(cue.text)), text=None,
            code=code, origin=f"detector:{code}"))
    return tuple(ops)


class EngineLoop:
    """INV-6: at least two independent signals, or it is not a proposal.

    The candidate rule is §6.25's, walked in cue order: a group of identical
    adjacent cues nominates one run candidate when it qualifies, and otherwise
    each of its members is offered as a within-cue candidate. That ordering is
    what makes the two kinds disjoint **by construction** -- a cue inside a
    qualifying run is never also a within-cue candidate -- so there is no
    precedence rule to maintain (RC-4).

    S4 is read at scoring time and never here: **only repetition nominates**
    (§6.25). S4(a)'s population on the baseline is 205 against repetition's 3, so
    a nominating S4 would emit hundreds of candidates whose only evidence is
    "somebody nearby decoded badly".
    """

    name = "engine-loop"
    may_delete = True

    def scan(self, rec: model.Recording,
             thr: thresholds.Thresholds) -> Iterator[Step]:
        closing = _closing(rec.cues)
        for i in range(len(rec.cues)):
            out: list[Verdict] = []
            if i in closing:
                a, b = closing[i]
                text = rec.cues[a].text
                if cand.run_qualifies(b - a + 1, text):
                    out.append(self._judge(rec, a, b, "run", thr))
                elif cand.run_is_near(b - a + 1, text):
                    # The shoulder claims its members here too, exactly as
                    # `candidates.covered_indices` does in the whole-sequence
                    # spelling -- the streaming walk and the fold must agree on
                    # who owns a cue or the two produce different plans.
                    out.append(self._judge(rec, a, b, "near-run", thr))
                else:
                    for k in range(a, b + 1):
                        if cand.has_repeat_unit(rec.cues[k].text):
                            out.append(self._judge(rec, k, k, "cue", thr))
            yield i, tuple(out)

    def _judge(self, rec: model.Recording, a: int, b: int, kind: cand.Kind,
               thr: thresholds.Thresholds) -> Verdict:
        s = scoring.score(rec, a, b, kind, thr)
        span = model.Span(rec.cues[a].id, 0, len(rec.cues[a].text))
        if s.disposition == "proposal":
            bad, near = signals.s4a_count(rec, a, b)
            finding = model.Finding(
                id=f"{self.name}:{a}-{b}", span=span, code=self.name,
                severity="high", unit="cue",
                evidence=model.Evidence.of(
                    "cue",
                    signals=float(s.n), cues=float(b - a + 1),
                    s4a_bad=float(bad), s4a_neighbours=float(near),
                    own_temperature=signals.s4b_fallback(rec, a, b),
                    # **Which RULE promoted it, not only what was observed**
                    # (G-6). `signals=1` on a finding is INV-6's arithmetic being
                    # overruled by `Scored.stands_alone` (A-25), and until this
                    # field existed a reader had to know that rule to tell that
                    # apart from a bug. Measured on the real recording: all three
                    # of grating's findings score 1 and every one of them came
                    # through this door.
                    stands_alone=float(s.stands_alone)),
                # Action CODES, not sentences: the CLI turns one into prose, and
                # INV-12 keeps free text out of every agent-facing field.
                actions=("delete", "keep"))
            return Verdict("finding", self.name, (a, b), s.n, finding=finding,
                           ops=_whole_cue_deletes(rec, a, b, self.name))
        if s.disposition == "note":
            # Two different facts, two different codes. "One signal held" and
            # "enough signals held but the run was one cue short of the bar" read
            # the same in a count and mean opposite things to someone deciding
            # whether to look: the second is the one that would have been a
            # finding on a different decode of the same audio.
            return Verdict("note", self.name, (a, b), s.n,
                           note=model.Note(id=f"{self.name}:{a}-{b}", span=span,
                                           code=self.name,
                                           reason=("near-threshold" if s.capped
                                                   else "single-signal")))
        return Verdict("dropped", self.name, (a, b), s.n)


class SpeakerRepeat:
    """Off by default, and it may never propose a delete (INV-8).

    Not caution about false positives: a human repeating themselves for emphasis
    and an engine looping are **the same text**, and deleting the first removes
    something the speaker said. So `may_delete` is `False` on the class and
    `_judge` never builds an op -- the rule is a property of the type, not a
    branch someone can invert.
    """

    name = "speaker-repeat"
    may_delete = False

    def scan(self, rec: model.Recording,
             thr: thresholds.Thresholds) -> Iterator[Step]:
        closing = _closing(rec.cues)
        for i in range(len(rec.cues)):
            out: list[Verdict] = []
            if i in closing:
                a, b = closing[i]
                if cand.run_qualifies(b - a + 1, rec.cues[a].text):
                    finding = model.Finding(
                        id=f"{self.name}:{a}-{b}",
                        span=model.Span(rec.cues[a].id, 0,
                                        len(rec.cues[a].text)),
                        code=self.name, severity="warn", unit="cue",
                        evidence=model.Evidence.of("cue",
                                                   cues=float(b - a + 1)),
                        # No `delete` here, and INV-8 is the reason.
                        actions=("read-it-yourself",))
                    out.append(Verdict("finding", self.name, (a, b), 1,
                                       finding=finding))
            yield i, tuple(out)


#: A text unit is one CJK character or one Latin word -- the two scripts this
#: product's corpus is written in, counted the way each is read.
_CJK = re.compile(r"[一-鿿぀-ヿ가-힯]")
_WORD = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?")

#: `sparse-cue`'s two bars. Ordinary speech in this corpus runs at 0.2-0.4
#: seconds per unit; 3.0 is roughly ten times slower than anyone talks, and 15 s
#: is past the real store's p99 cue length (10.7 s over 16,601 cues). **Neither
#: is a boundary found in the data** -- measured 2026-09-27, the band just below
#: (>= 10 s and >= 2 s/unit, 17 cues) still carries the same defect in about half
#: its members -- so these are a cost choice: every real cue AT this bar was
#: checked against the audio and every one had a defect (PSM section 20).
SPARSE_MIN_SECONDS = 15.0
SPARSE_MIN_SECONDS_PER_UNIT = 3.0


def text_units(text: str) -> int:
    return len(_CJK.findall(text)) + len(_WORD.findall(text))


class SparseCue:
    """A long cue with almost no words in it (I-23). Flags; never deletes.

    What it measures is text density, and it claims nothing past that: a cue
    this long with this few words is either **stretched over a pause** (VAD
    splices two speech regions and one real sentence's timestamps cover the
    silence between -- M1 measured one spanning 126.4 s of nobody speaking) or
    **short of words** (25 s of loud audio, one character written). The text is
    often right in the first case and the timing is what is wrong, so a delete
    would remove a sentence someone said: `may_delete` is `False` on the class,
    INV-8's shape, and the action is to go and listen.

    Controls (INV-9), both real audio through the real engine: the M1 `gap` clip's
    two stretched cues and `far20`'s 44 s one-character cue must be flagged; the
    M1 `speech` clip, speech from end to end by construction, must not be.
    """

    name = "sparse-cue"
    may_delete = False

    def scan(self, rec: model.Recording,
             thr: Optional[thresholds.Thresholds]) -> Iterator[Step]:
        for i, cue in enumerate(rec.cues):
            out: list[Verdict] = []
            seconds = cue.end - cue.start
            units = text_units(cue.text)
            per_unit = seconds / max(units, 1)
            if (seconds >= SPARSE_MIN_SECONDS
                    and per_unit >= SPARSE_MIN_SECONDS_PER_UNIT):
                out.append(Verdict(
                    "finding", self.name, (i, i), 2,
                    finding=model.Finding(
                        id=f"{self.name}:{i}",
                        span=model.Span(cue.id, 0, len(cue.text)),
                        code=self.name, severity="warn", unit="cue",
                        evidence=model.Evidence.of(
                            "cue", seconds=round(seconds, 3), units=float(units),
                            seconds_per_unit=round(per_unit, 3)),
                        actions=("listen", "keep"))))
            yield i, tuple(out)


#: I-27: how long a stretch of the recording may pass with no cue at all before
#: it is said out loud. The issue's own figure; the measured case it came from was
#: 403 s (a lecture's first 6 min 43 s), and ordinary pauses in the corpus are far
#: below a minute.
UNCOVERED_MIN_SECONDS = 60.0


def uncovered_spans(cues: Sequence[model.Cue], duration: float,
                    min_seconds: float = UNCOVERED_MIN_SECONDS
                    ) -> list[tuple[float, float, int, str]]:
    """`(start, end, anchor cue index, side)` for every stretch with no cue.

    The head (0 -> first cue), every gap between the furthest cue end so far and
    the next start, and the tail (last end -> duration). `side` says where the
    anchor sits: `before` = the gap ends at the anchor cue's start, `after` = it
    begins at its end (the tail). A recording with no cues has no anchor and
    returns nothing -- that case is I-22's, already answered by `vad_kept`.
    """
    if not cues:
        return []
    order = sorted(range(len(cues)), key=lambda i: (cues[i].start, cues[i].end))
    out: list[tuple[float, float, int, str]] = []
    reach = 0.0
    for i in order:
        if cues[i].start - reach >= min_seconds:
            out.append((reach, cues[i].start, i, "before"))
        reach = max(reach, cues[i].end)
    last = max(order, key=lambda i: cues[i].end)
    if duration - reach >= min_seconds:
        out.append((reach, duration, last, "after"))
    return out


class UncoveredSpan:
    """A minute or more of the recording with no cue at all (I-27). Never deletes.

    It reads the cue TIMELINE, not the engine's VAD output: faster-whisper does
    not return its speech chunks, and `EngineReport` is five fields by contract.
    So it claims only what the timeline shows -- nothing was written here -- and
    names whether VAD was on, because that is the likeliest reason (I-27: a
    lecture's first 403 s, as loud as the rest, dropped whole). The other reasons
    look the same from here: a real silence, or a region the decoder kept and
    wrote nothing for. The action is to listen, and re-decode that span with
    `--no-vad` or a lower `--vad-threshold` if there is speech in it.

    Anchored on the cue next to the gap with an EMPTY character range, because a
    `Span` lives inside a cue and the gap is between them: `before` -> offset 0
    of the cue after it, `after` (the tail) -> the end of the last cue's text.

    Controls (INV-9): the 2026-10-20 lecture recording whose default decode
    starts at 06:43 must be flagged; a continuous lecture decoded end to end
    must not be (`docs/evidence/uncovered_span_controls_2026-10-21.txt`).
    """

    name = "uncovered-span"
    may_delete = False

    def scan(self, rec: model.Recording,
             thr: Optional[thresholds.Thresholds]) -> Iterator[Step]:
        vad = {True: 1.0, False: 0.0}.get(rec.report.vad_enabled, -1.0)
        at: dict[int, list[Verdict]] = {}
        for start, end, i, side in uncovered_spans(rec.cues, rec.duration):
            cue = rec.cues[i]
            offset = 0 if side == "before" else len(cue.text)
            at.setdefault(i, []).append(Verdict(
                "finding", self.name, (i, i), 1,
                finding=model.Finding(
                    id=f"{self.name}:{i}:{side}",
                    span=model.Span(cue.id, offset, offset),
                    code=self.name, severity="warn", unit="cue",
                    evidence=model.Evidence.of(
                        "cue", start=round(start, 3), end=round(end, 3),
                        seconds=round(end - start, 3), vad_enabled=vad),
                    actions=("listen", "keep"))))
        for i in range(len(rec.cues)):
            yield i, tuple(at.get(i, ()))


class ScriptSwitch:
    """Where the transcript left the Chinese script the reader expects.

    One info finding per run of the other script (`scriptform.other_runs`),
    anchored at offset 0 of the run's first cue, with the run's size, time and
    character counts. No edit, ever: the product does not convert scripts, and
    the words in the run may be right. `cues` (by script) is what `run` puts in
    `PlanSummary.script_cues`.

    Controls (INV-9), on stored real decodes: two all-traditional decodes give
    no run, decodes that switched give theirs, and the same text with the
    expected script flipped flags the other side
    (`docs/evidence/script_switch_controls_2026-11-23.txt`).
    """

    name = "script-switch"
    may_delete = False

    def __init__(self, expect: str = scriptform.DEFAULT_SCRIPT) -> None:
        if expect not in scriptform.SCRIPTS:
            raise DetectError(f"script must be one of {scriptform.SCRIPTS}")
        self.expect = expect
        self.cues: dict[str, int] = {}

    def scan(self, rec: model.Recording,
             thr: Optional[thresholds.Thresholds]) -> Iterator[Step]:
        labels = [scriptform.label(c.text) for c in rec.cues]
        self.cues = {k: labels.count(k) for k in ("hant", "hans", "none")}
        at: dict[int, Verdict] = {}
        for first, last, n in scriptform.other_runs(labels, self.expect):
            s = t = 0
            for c in rec.cues[first:last + 1]:
                a, b = scriptform.counts(c.text)
                s, t = s + a, t + b
            cue = rec.cues[first]
            at[first] = Verdict(
                "finding", self.name, (first, last), 1,
                finding=model.Finding(
                    id=f"{self.name}:{first}",
                    span=model.Span(cue.id, 0, 0),
                    code=self.name, severity="info", unit="cue",
                    evidence=model.Evidence.of(
                        "cue", cues=float(n), last=float(last),
                        start=round(cue.start, 3),
                        end=round(rec.cues[last].end, 3),
                        simplified=float(s), traditional=float(t),
                        expect=float(scriptform.SCRIPTS.index(self.expect))),
                    actions=("check", "keep")))
        for i in range(len(rec.cues)):
            yield i, ((at[i],) if i in at else ())


class Declared:
    """`filler` and `glossary`: the declared list is an UPPER BOUND (INV-7).

    An empty list finds nothing; it does not fall back to a built-in list, because
    a built-in list is an undeclared rule wearing a user's clothes. **This rule
    must not be applied to `engine-loop`** -- nobody declares a hallucination in
    advance, so bounding that detector by a list would bound it to zero. `build`
    refuses a `terms` entry for any detector outside `DECLARED`, which is that
    sentence made mechanical.
    """

    may_delete = False

    def __init__(self, name: str, terms: Sequence[str] = ()) -> None:
        self.name = name
        self.terms = tuple(t for t in terms if t.strip())

    def scan(self, rec: model.Recording,
             thr: thresholds.Thresholds) -> Iterator[Step]:
        for i, cue in enumerate(rec.cues):
            out: list[Verdict] = []
            for term in self.terms:
                at = cue.text.find(term)
                if at >= 0:
                    out.append(Verdict(
                        "finding", self.name, (i, i), 1,
                        finding=model.Finding(
                            id=f"{self.name}:{i}:{at}",
                            span=model.Span(cue.id, at, at + len(term)),
                            code=self.name, severity="info", unit="cue",
                            evidence=model.Evidence.of("cue", occurrences=1.0),
                            actions=("remove", "keep"))))
            yield i, tuple(out)


#: `glossary`'s evidence codes. Numbers, because `Evidence` carries numbers only
#: (INV-12); the order is `soundalike.KINDS`, the order the hits are trusted in.
GLOSSARY_KIND = {k: float(i + 1) for i, k in enumerate(sa.KINDS)}
#: Evidence `script`: the cue's language case, read from its own text, because
#: the decode's language map cannot see it -- a mixed zh/en talk measured at
#: `zh`, agreement 1.0, for every determined stretch (RECORD-2cdc7f1c).
GLOSSARY_SCRIPT = {k: float(i + 1) for i, k in enumerate(sa.CASES)}


class Glossary:
    """Where the transcript may have written a lexicon term wrong (PIM §6.4, INV-7).

    The lexicon is the upper bound: every finding names one of its entries, and
    the only text a finding can propose is that entry's declared `term`. An empty
    lexicon finds nothing.

    Two shapes of finding, by user ruling 2026-10-30 (「已知錯寫帶替換，音近只標」):

    - a DECLARED wrong form (`kind` 1): severity `warn`, actions
      `("replace", "keep")`, and one `replace` op carrying the entry's term. The
      op does nothing until `accept`; that is the PIM's 「`glossary` 換」.
    - a SOUND-ALIKE (`kind` 2 homophone, 3 near, 4 latin): severity `info`,
      actions `("check", "keep")`, and no op in the script. It is a guess: 光線
      is also light, 海浪 is also waves, and `accept` with no `--op` takes every
      op in a plan. Its replace goes to `candidates[]` instead, applied only when
      named (`accept --candidate N`).

    Evidence beside `kind`/`entry`/`source`: `script` (the cue's case, 1 zh-only,
    2 mixed, 3 en-only) and, when a character-frequency table was supplied and
    both sides are CJK, `prior` = sum log10 f(term char) - sum log10 f(span
    char). `prior` ranks; it never drops a finding or makes an op -- measured
    AUC 0.68 [0.55-0.79] at telling gold hits from the rest
    (`docs/evidence/glossary_cases_2026-10-30.txt`), which is a hint, not a vote.

    Never deletes (`may_delete` False, INV-8); `may_replace` is what lets its ops
    through `run`'s INV-8 gate, and only `replace` ops.
    """

    name = "glossary"
    may_delete = False
    may_replace = True

    def __init__(self, lex: lexicon.Lexicon,
                 freq: Optional[Mapping[str, int]] = None) -> None:
        self.lexicon = lex
        self.freq = freq

    #: Lexicon terms found written in simplified characters in the last scan
    #: (`soundalike.same_word`). Not findings -- the word is right -- but counted
    #: into `PlanSummary.passed_over["script-variant"]`, so a reader can see the
    #: rule worked and how often (user ruling 2026-11-23: 靜默計數).
    passed_over = 0

    def scan(self, rec: model.Recording,
             thr: Optional[thresholds.Thresholds]) -> Iterator[Step]:
        lex = self.lexicon
        self.passed_over = 0
        for i, cue in enumerate(rec.cues):
            out: list[Verdict] = []
            if lex.entries:
                case = GLOSSARY_SCRIPT[sa.case_of(cue.text)]
                variants: list[tuple[int, int]] = []
                hits = sa.find(cue.text, lex, variants=variants)
                self.passed_over += len(variants)
                for h in hits:
                    entry = lex.entries[h.entry]
                    span = model.Span(cue.id, h.start, h.end)
                    declared = h.kind == "wrong"
                    values = {"kind": GLOSSARY_KIND[h.kind],
                              "entry": float(h.entry),
                              "source": float(lexicon.SOURCES.index(entry.source)),
                              "script": case}
                    got = (sa.prior(cue.text[h.start:h.end], entry.term, self.freq)
                           if self.freq else None)
                    if got is not None:
                        values["prior"] = round(got, 3)
                    finding = model.Finding(
                        id=f"{self.name}:{i}:{h.start}", span=span,
                        code=self.name, severity="warn" if declared else "info",
                        unit="cue", evidence=model.Evidence.of("cue", **values),
                        actions=("replace", "keep") if declared else ("check", "keep"))
                    op = editscript.EditOp(
                        op="replace", span=span, text=entry.term, code=self.name,
                        origin=f"detector:{self.name}")
                    out.append(Verdict("finding", self.name, (i, i), 1,
                                       finding=finding,
                                       ops=(op,) if declared else (),
                                       candidates=() if declared else (op,)))
            yield i, tuple(out)


def _without_op(v: Verdict) -> Verdict:
    """A replace that collides with another detector's op keeps its finding and
    loses its op, and the finding SAYS so (`collides` = 1, action `check`).

    `editscript.merge` refuses overlap on purpose -- a precedence rule would pick
    a winner silently -- so the collision is resolved here, visibly, in the one
    direction that cannot lose text: a cue an `engine-loop` delete removes is not
    also rewritten.
    """
    f = v.finding
    assert f is not None
    values = dict(f.evidence.values)
    values["collides"] = 1.0
    return Verdict("finding", v.code, v.span, v.signals, finding=model.Finding(
        id=f.id, span=f.span, code=f.code, severity=f.severity, unit=f.unit,
        evidence=model.Evidence.of(f.evidence.layer, **values),
        actions=("check", "keep")))


def build(enabled: Sequence[str] = DEFAULT,
          terms: Optional[Mapping[str, Sequence[str]]] = None,
          lex: Optional[lexicon.Lexicon] = None,
          freq: Optional[Mapping[str, int]] = None,
          script: str = scriptform.DEFAULT_SCRIPT) -> list[Detector]:
    """`terms["glossary"]` adds correct forms to `lex` (sound-alikes only, no
    declared wrong forms): `--term glossary=光纖` is the one-word lexicon.
    `freq` is a character-frequency table for `glossary`'s `prior` evidence.
    `script` is the script `script-switch` expects (`hant` or `hans`)."""
    terms = dict(terms or {})
    stray = [k for k in terms if k not in DECLARED]
    if stray:
        raise DetectError(
            f"INV-7 bounds only {DECLARED} by a declared list; {sorted(stray)} "
            f"cannot take one, because nobody declares a hallucination in advance")
    out: list[Detector] = []
    for name in enabled:
        if name == "engine-loop":
            out.append(EngineLoop())
        elif name == "speaker-repeat":
            out.append(SpeakerRepeat())
        elif name == "sparse-cue":
            out.append(SparseCue())
        elif name == "uncovered-span":
            out.append(UncoveredSpan())
        elif name == "script-switch":
            out.append(ScriptSwitch(script))
        elif name == "glossary":
            extra = lexicon.Lexicon(tuple(
                lexicon.Entry(term=t.strip(), source="human")
                for t in terms.get(name, ()) if t.strip()))
            out.append(Glossary(lexicon.merge(
                x for x in (lex, extra) if x is not None), freq))
        elif name in DECLARED:
            out.append(Declared(name, terms.get(name, ())))
        else:
            raise DetectError(f"no such detector: {name!r}")
    return out


def warnings_for(enabled: Sequence[str]) -> list[str]:
    """One line per enabled detector that has no controls (§6.4).

    Printed rather than logged: the user asked for it, so the user is told what it
    is worth. Silence here would let an uncalibrated detector's output look exactly
    like `engine-loop`'s.
    """
    return [f"{name}: {UNCONTROLLED[name]}"
            for name in enabled if name in UNCONTROLLED]


def failure(name: str, reason: str) -> tuple[str, str]:
    """One `detectors_failed` entry, with its reason checked against the closed set.

    A tuple of legal reasons that nothing ever consults is a comment in constant's
    clothing: it documents a vocabulary and permits any other. This function is the
    only way `run` builds an entry, so the set is enforced where the entry is made.
    """
    if reason not in FAILURE_REASONS:
        raise DetectError(
            f"{reason!r} is not one of {FAILURE_REASONS}; `detectorsFailed` carries "
            f"a reason CODE from the PIM's own three (§5.5), because the field is "
            f"machine-read and a sentence there would be free text (INV-12)")
    return (name, reason)


def _counts(keys: Sequence[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for k in keys:
        out[k] = out.get(k, 0) + 1
    return out


def run(rec: model.Recording, *, enabled: Sequence[str] = DEFAULT,
        terms: Optional[Mapping[str, Sequence[str]]] = None,
        lex: Optional[lexicon.Lexicon] = None,
        freq: Optional[Mapping[str, int]] = None,
        expect_script: str = scriptform.DEFAULT_SCRIPT,
        thr: Optional[thresholds.Thresholds] = None,
        word_level: bool = False,
        on_step: Optional[Callable[[str, int, int], None]] = None,
        cancel: Optional[progress.Cancellation] = None,
        run_id: str = "r000") -> tuple[model.Plan, tuple[Verdict, ...]]:
    """The `detect` verb's body: findings, notes, a script and the summary.

    `on_step(detector, done, total)` is called once per cue per detector -- that is
    where INV-22's `detect` phase gets its denominator, and it is the caller's job
    rather than the detector's so that no detector can emit progress the state
    machine did not ask for.

    `cancel` is polled at the SAME point, once per cue. R13 makes `detecting`
    cancellable alongside `queued`/`decoding`/`awaiting-decision`, and a phase with
    a per-cue progress event and no per-cue poll point would answer a cancel only
    when the whole scan finished -- which on the user's own words is the defect
    (「單純反灰然後等待很久」), not a smaller version of it. `Cancelled` propagates:
    §8.2 discards partial output rather than handing back half a plan, and there is
    no half-plan to discard if nothing is returned.

    The verdicts are returned beside the `Plan` because `Plan` has no field for a
    dropped candidate and must not grow one: a drop is a fact about this ROUND's
    judgement, not a statement about the transcript. Handing it back keeps RC-1's
    distinction available to the CLI and to the tests without widening the record
    that goes to disk.
    """
    if word_level:
        # T-1 trigger (a) is live in milestone 1 and the layer is absent, so the
        # request fails naming what is missing. Returning cue-level output here
        # would be the worst outcome: the caller asked for word-level and would
        # be handed something that looks like a compliant answer.
        raise DetectError(
            f"word-level alignment needs the {TOKEN_LAYER!r} layer, which is "
            f"absent in milestone 1 (PIM §2.4 T-1); this request fails rather "
            f"than answering at cue level")

    # A recording with no cues has no thresholds -- `thresholds.of` refuses it,
    # correctly -- and needs none: every detector advances per cue, so none of them
    # is ever called with one. I-22: a silent recording is a round with an empty
    # plan, not a detector failure and not an exception out of this function.
    if thr is None and rec.cues:
        thr = thresholds.of(rec)
    # Optional arguments are passed only when set: tests stand in for `build`
    # with the three-argument shape, and that shape stays the default call.
    extra: dict[str, Any] = {}
    if freq:
        extra["freq"] = freq
    if expect_script != scriptform.DEFAULT_SCRIPT:
        extra["script"] = expect_script
    detectors = build(enabled, terms, lex, **extra)
    total = len(rec.cues)

    verdicts: list[Verdict] = []
    failed: list[tuple[str, str]] = []
    passed_over: dict[str, int] = {}
    script_cues: dict[str, int] = {}
    for det in detectors:
        mine: list[Verdict] = []
        try:
            for done, batch in det.scan(rec, thr):
                mine.extend(batch)
                if on_step is not None:
                    on_step(det.name, done + 1, total)
                if cancel is not None:
                    cancel.poll()
        except progress.Cancelled:
            # NOT caught as a detector failure. A cancelled round is not a round in
            # which a detector broke: `detectors_failed` is BR-10's field and
            # writing a cancel into it would put a user's decision in the record as
            # an engine fault. The whole call fails instead, and the state machine
            # takes `detecting -> cancelled`.
            raise
        except Exception:
            # BR-10: an enabled detector that raises must SAY so. The run
            # continues, because a plan with one detector's findings and another's
            # failure is a legitimate outcome -- the edge `detecting -> proposing`
            # does not ask about detector health, which is why the field exists.
            # Whatever the failed detector produced before raising is discarded:
            # half a scan is not a verdict on the cues it never reached.
            failed.append(failure(det.name, "exception"))
            continue
        # INV-8 as a property of the type. A detector that may not delete may
        # carry no op at all -- unless it declares `may_replace`, and then only
        # `replace` ops (`glossary`: the PIM's 「`glossary` 換」). A delete from
        # either kind is refused here whatever flag turned the detector on.
        allowed = ({"replace"} if getattr(det, "may_replace", False) else set())
        if det.may_delete:
            allowed = {"delete", "replace", "insert"}
        bad = sorted({op.op for v in mine for op in v.ops + v.candidates}
                     - allowed)
        if bad:
            raise DetectError(
                f"INV-8: {det.name} produced {bad} ops; it may produce "
                f"{sorted(allowed) or 'none'}")
        verdicts.extend(mine)
        if isinstance(det, Glossary):
            # Written whenever glossary ran, zero included: absent = not run.
            passed_over["script-variant"] = det.passed_over
        if isinstance(det, ScriptSwitch):
            script_cues = dict(det.cues)

    # Deletes are merged first, so a replace landing in a cue a delete removes
    # is the one that yields -- visibly, through `_without_op`, never by letting
    # `merge` pick (it refuses overlap on purpose).
    script: editscript.EditScript = ()
    order = sorted(range(len(verdicts)),
                   key=lambda k: (not verdicts[k].proposes_deletion, k))
    for k in order:
        v = verdicts[k]
        if not v.ops:
            continue
        try:
            script = editscript.merge(script, v.ops)
        except editscript.ScriptError:
            if v.proposes_deletion:
                raise
            verdicts[k] = _without_op(v)
    # A candidate is kept only where it could be applied beside the WHOLE script:
    # one landing in a cue a delete removes would make `accept --candidate N`
    # fail its overlap check every time, which is an offer that cannot be taken.
    # Candidates may overlap each other; `accept` refuses a selection that does.
    candidates: list[editscript.EditOp] = []
    for v in verdicts:
        for op in v.candidates:
            try:
                editscript.merge(script, (op,))
            except editscript.ScriptError:
                continue
            candidates.append(op)
    findings = tuple(v.finding for v in verdicts if v.finding is not None)
    notes = tuple(v.note for v in verdicts if v.note is not None)

    # BR-5 counts DELETION proposals, so the count is read off the ops rather
    # than off a disposition name: a detector under INV-8 emits findings and no
    # proposals, and one word for both would make that difference invisible here.
    proposals = _counts([v.code for v in verdicts if v.proposes_deletion])
    summary = model.PlanSummary(
        findings=_counts([f.code for f in findings]),
        proposals=proposals,
        notes=_counts([n.reason for n in notes]),
        # Both markers are written unconditionally: a missing key means "this
        # round never reached that gate" and a zero means "the gate ran and found
        # nothing ungated" (INV-17). Milestone 1's proposals are whole-cue deletes
        # from a detector, which T-1 does not send to the word-level gate, and
        # nothing in the shipping path consumes `vad_kept` -- so both are zero,
        # and both are said.
        unverified={m: 0 for m in UNVERIFIED_MARKERS},
        detectors_skipped=tuple(n for n in UNCONTROLLED if n not in enabled),
        detectors_failed=tuple(failed),
        # `multilingual` is named here because PIM v1.4 section 5.5 names it:
        # it decides how `language_detected` may be read (E-3(d)). A pin is not:
        # E-3(f) refuses a pin with detection before any plan exists, so a plan
        # has no collision to show, and the pin itself stays in `_origin.json`.
        engine_health={"vad_enabled": rec.report.vad_enabled,
                       "vad_verdict": rec.report.vad_verdict(),
                       "multilingual": rec.report.multilingual,
                       "degenerated": rec.report.degenerated},
        passed_over=passed_over, script_cues=script_cues)
    plan = model.Plan(run=run_id, findings=findings, notes=notes,
                      script=script, summary=summary,
                      candidates=tuple(candidates))
    return plan, tuple(verdicts)


def _in_scope(plan: model.Plan, marker: str,
              rec: Optional[model.Recording] = None) -> int:
    """How many of this plan's ops the named gate has an opinion about.

    **Not the proposal count**, and that mistake had a whole paragraph warning
    against it before it was made here (§0, under T-3: "marking every detector
    proposal `no-token-layer` would be the opposite error"). Found by an
    independent verifier reading the card against the code (V-1); the first version
    of this function used `sum(summary.proposals.values())`, which on the loop
    decode reported `0/1` -- readable as "one op needed word-level verification and
    passed", when the truth is that **nothing was in scope at all**.

    * `no-token-layer` guards T-2, whose subject is an `advisor:*` `replace`/`delete`
      **inside** a cue (T-1 trigger (b)). Milestone 1 produces no `advisor:*` op and
      only whole-cue deletes, so the population is 0 -- by this arithmetic, not by a
      constant, so the day `Advisor` arrives the number moves on its own.
    * `no-vad` guards any verdict that CONSUMES `vad_kept` (E-2(c)). Milestone 1
      consumes it nowhere -- the boundary snapping uses cue gaps, which is the only
      method with a measured hit -- so this is 0 too, and it is 0 because a search
      found nothing rather than because somebody wrote a zero.
    """
    if marker == "no-token-layer":
        lengths = ({c.id: len(c.text) for c in rec.cues} if rec is not None
                   else {})
        n = 0
        for op in plan.script:
            if not op.origin.startswith("advisor:"):
                continue
            whole = lengths.get(op.span.cue_id)
            # T-1 (b) is intra-cue: a span covering the whole cue is out of scope.
            # With no recording to measure against, the op COUNTS -- over-counting a
            # gate's population overstates how much went unverified, and that is the
            # safe direction for a mark whose whole job is to admit doubt.
            if whole is None or not (op.span.char_start == 0
                                     and op.span.char_end == whole):
                n += 1
        return n
    if marker == "no-vad":
        return 0
    raise DetectError(f"no scope rule for marker {marker!r}")


def unverified_scope(plan: model.Plan, marker: str,
                     rec: Optional[model.Recording] = None) -> tuple[int, int]:
    """`(count, population)`, and the population may legitimately be 0.

    Returned as a pair rather than as an `Optional[Rate]` alone because T-3 asks the
    summary to SAY the population is empty -- "rather than printing a bare `0` a
    reader would take for 「everything passed」". A `None` with no number attached
    would be that bare zero one level up.
    """
    if marker not in plan.summary.unverified:
        raise DetectError(
            f"{marker!r} is absent from this plan's unverified markers, which "
            f"means the round never reached that gate; that is not a zero")
    return plan.summary.unverified[marker], _in_scope(plan, marker, rec)


def unverified_rate(plan: model.Plan, marker: str,
                    rec: Optional[model.Recording] = None
                    ) -> Optional[model.Rate]:
    """The count with the population it counted over, or `None` (INV-1).

    `None` means the population is empty, which is `Rate`'s own rule -- "a rate over
    an empty population is not a rate; report the count alone". In milestone 1 both
    markers are always in that case, and `unverified_scope` is what lets a caller
    say so out loud instead of printing a naked 0.
    """
    count, population = unverified_scope(plan, marker, rec)
    if population == 0:
        return None
    return model.Rate(count, population, "cue")
