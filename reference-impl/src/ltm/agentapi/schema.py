"""Response shapes, and INV-12 enforced by walking them.

PSM §9. **No `agentapi` response may contain a free-text field able to carry
transcript content.** `show` returns spans, codes, counts and verdicts;
`Finding.evidence` is numbers; `why`-style prose exists only in the CLI, where a
human is reading it and no machine is storing it.

**The rule is a type, not a name allowlist, and that is a v4 amendment (A-8).**
The card said "fails on any `str` field outside the allowlist (`code`, `state`,
`unit`, `path`, ids)". Building it showed the allowlist growing on its first
contact with real responses -- `detector`, `marker`, `language`, `phase`,
`reason_code`, `verb`, `disposition`, `sha256` -- and an allowlist that grows once
per field is a ban list wearing the other costume: whoever adds the ninth entry
is the person who was supposed to be stopped. So:

* a string that is drawn from a closed set is an `Identifier`, whose constructor
  validates it against `core.progress.IDENTIFIER` -- the SAME pattern the progress
  event uses, imported rather than restated;
* the only bare `str` fields allowed are **paths and ids**, both on the card's
  original list, enumerated here in a set of five names plus a rule for their
  plurals -- so `runs` is not a sixth entry somebody had to think of;
* everything else that is a string fails the walk, including inside tuples and
  mapping values.

Transcript text cannot pass `IDENTIFIER`: it has spaces, CJK and punctuation, and
the pattern is `[a-z0-9][a-z0-9._-]{0,63}`. That is the difference between a rule
that refuses text and a list that refuses names somebody thought of.
"""
from __future__ import annotations

import dataclasses
import re
import typing
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, get_args, get_origin, get_type_hints

from ..core.editscript import ORIGIN_PREFIXES
from ..core.progress import IDENTIFIER, STATES

#: Bare `str` is legal for these five names, and for their plurals. A path is on
#: the card's own allowlist (the user has to be able to open the file); the four ids
#: are how a caller names a run, a revision, a finding and a file's content in its
#: next request.
#:
#: **The plural is a RULE, not four more entries** (`_is_id_field` below). `paths`
#: was the only plural the card anticipated, and the second one (`runs`) arrived
#: within a day of the first response being written -- which is the allowlist-growth
#: A-8 is about, in miniature. A container of ids is the same kind of value as one
#: id; letting the name's number decide would make `runs` a different rule from
#: `run`, and whoever adds `revs` next is the person the rule was supposed to stop.
ID_FIELDS = frozenset({"path", "run", "rev", "id", "sha256"})


def _is_id_field(name: str) -> bool:
    """`path`/`paths`, `run`/`runs`, ... One set, both numbers."""
    return name in ID_FIELDS or (name.endswith("s") and name[:-1] in ID_FIELDS)


class SchemaError(ValueError):
    """A response shape breaks INV-12, or a value breaks its own domain."""


@dataclass(frozen=True, slots=True)
class Closed:
    """A string whose value is checked against a closed set at construction.

    Not a `NewType` and not a bare annotation: both are erased at runtime, and the
    walk below has to be able to tell such a field from a free-text one by looking
    at the class. A wrapper is the only form that survives to runtime and the only
    form whose VALUE is checked as well as its type.

    A BASE rather than one type, because there are two closed sets and they are
    different sets: identifiers are matched by a pattern, and state names are
    matched by membership in `progress.STATES`. Sharing a base is what lets the
    walk say "this field's values are constrained" once, instead of naming each
    subclass -- the shape a field-name allowlist got wrong (A-8).
    """

    text: str

    def __str__(self) -> str:
        return self.text


@dataclass(frozen=True, slots=True)
class Identifier(Closed):
    """`[a-z0-9][a-z0-9._-]{0,63}` -- the progress event's own pattern."""

    def __post_init__(self) -> None:
        if not IDENTIFIER.fullmatch(self.text):
            raise SchemaError(
                f"not an identifier: {self.text!r}. Values in agent-facing "
                f"responses come from closed sets ({IDENTIFIER.pattern}); a value "
                f"that does not match is either a bug or a route for transcript "
                f"text (INV-12)")


@dataclass(frozen=True, slots=True)
class StateName(Closed):
    """One of the twelve states, by MEMBERSHIP and not by pattern.

    Three state names carry parentheses (`failed(engine)`, `failed(proof)`), so
    `IDENTIFIER` refuses them -- and widening the pattern to admit them would widen
    it for every other field too, which is a worse trade than having a second closed
    set. Membership in `progress.STATES` is a stronger check than any regex anyway:
    the set is derived from the edge list, so a state that stops existing stops
    being a legal value in the same commit.
    """

    def __post_init__(self) -> None:
        if self.text not in STATES:
            raise SchemaError(
                f"not a state: {self.text!r}; one of {sorted(STATES)}. The set is "
                f"derived from §8's edge list, never listed beside it")


#: What may follow one of `ORIGIN_PREFIXES`: a detector code, or a model name with
#: an optional `@promptVersion`. `@` is here and NOT in `IDENTIFIER` on purpose --
#: widening the general pattern for one field's punctuation widens it for every
#: field, which is the allowlist-growth failure A-8 is about.
PROVENANCE_TAIL = re.compile(r"|[a-z0-9][a-z0-9._@-]{0,63}")


@dataclass(frozen=True, slots=True)
class Provenance(Closed):
    """Who proposed an edit: `human`, `detector:<code>`, `advisor:<model>@<ver>`.

    **A third closed set, not a wider `IDENTIFIER`.** `origin` carries a colon and
    (in milestone 2) an `@`, so it cannot pass the general pattern -- and the
    answer to that is never to loosen the general pattern, for `StateName`'s
    reason: `failed(engine)` got its own set rather than putting parentheses into
    everybody's field.

    **The prefixes are imported from `core.editscript`, never restated.** That
    module's `EditOp.__post_init__` already owns this value domain (PIM §5.3); a
    second copy here would be a second truth, and the day milestone 2 adds a
    prefix only one of the two would learn about it.
    """

    def __post_init__(self) -> None:
        prefix = next((p for p in ORIGIN_PREFIXES if self.text.startswith(p)), None)
        if prefix is None or not PROVENANCE_TAIL.fullmatch(self.text[len(prefix):]):
            raise SchemaError(
                f"not a provenance: {self.text!r}. It must be one of "
                f"{ORIGIN_PREFIXES} followed by {PROVENANCE_TAIL.pattern!r} -- a "
                f"value that is not is either a bug or a route for transcript "
                f"text (INV-12)")


def ident(text: str) -> Identifier:
    return Identifier(text)


def provenance(text: str) -> Provenance:
    return Provenance(text)


def idents(items) -> tuple[Identifier, ...]:
    return tuple(Identifier(x) for x in items)


def state(text: str) -> StateName:
    return StateName(text)


# --- the INV-12 walk -----------------------------------------------------

def _offending(name: str, annotation: Any) -> list[str]:
    """Every place a bare `str` could carry a value inside this annotation."""
    if annotation is str:
        return [] if _is_id_field(name) else [name]
    origin = get_origin(annotation)
    if origin is None:
        if (dataclasses.is_dataclass(annotation)
                and not (isinstance(annotation, type)
                         and issubclass(annotation, Closed))):
            return [f"{name}.{bad}" for bad in offending_fields(annotation)]
        return []
    args = get_args(annotation)
    if origin is typing.Union or str(origin) == "typing.Union":
        return [b for a in args for b in _offending(name, a)]
    if origin in (dict, Mapping) or "Mapping" in str(origin):
        # A mapping's KEY may be a str: keys are codes and ids, and they are
        # validated at construction (see `check_mapping_keys`). Its VALUE may not.
        return _offending(name, args[1]) if len(args) == 2 else []
    return [b for a in args for b in _offending(name, a)]


def offending_fields(cls: type) -> list[str]:
    hints = get_type_hints(cls)
    out: list[str] = []
    for f in dataclasses.fields(cls):
        out.extend(_offending(f.name, hints[f.name]))
    return out


def check_response(cls: type) -> None:
    bad = offending_fields(cls)
    if bad:
        raise SchemaError(
            f"{cls.__name__} carries free-text field(s) {bad}: an agent-facing "
            f"response may hold numbers, `Identifier`s, paths and ids only "
            f"(INV-12). A field that must say something in prose belongs in the "
            f"CLI, where a human reads it and nothing stores it.")


def check_mapping_keys(mapping: Mapping[str, Any]) -> None:
    """Mapping keys are codes, so they are identifiers too.

    Enforced at construction, like `ProgressEvent.detail`'s numbers: the walk above
    can see a field's TYPE but never the keys a caller put in it, and
    `Mapping[str, int]` keyed by a line of transcript would satisfy every static
    check in this file.
    """
    for key in mapping:
        Identifier(key)


# --- the responses -------------------------------------------------------

def check_values(obj: Any) -> None:
    """Every mapping key inside a response is an identifier, however deep.

    Recursive, because `ShowResponse.stretches` is a tuple OF mappings: a check
    that only looked at top-level fields would pass a mapping keyed by a line of
    transcript one level down, and INV-12 is about what can travel, not about how
    many layers of container it travels in.
    """
    if isinstance(obj, Mapping):
        check_mapping_keys(obj)
        for value in obj.values():
            check_values(value)
    elif isinstance(obj, (tuple, list)):
        for item in obj:
            check_values(item)
    elif dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        for f in dataclasses.fields(obj):
            check_values(getattr(obj, f.name))


#: NOT `slots=True`, and the reason is a language trap rather than a preference:
#: a `slots=True` dataclass is a NEW class object, so zero-argument `super()` in a
#: subclass method raises `TypeError` at runtime. These types are validated through
#: inheritance, so slots would break the validation it is supposed to be beside.
@dataclass(frozen=True)
class Response:
    """Base: every response says which verb produced it."""

    verb: Identifier

    def __post_init__(self) -> None:
        check_values(self)


@dataclass(frozen=True)
class IntakeResponse(Response):
    run: str
    path: str
    sha256: str
    bytes: float
    #: 1.0 when this content already had a run and intake returned that one rather
    #: than making a second (v1.0 §9.2 / I-19). It is a FIELD and not a silence
    #: because 「你交進來的東西已經有一份成果」 is something the caller has to be
    #: able to act on -- a person wants to know before decoding again, and an agent
    #: cannot infer it from a run id that looks exactly like a fresh one.
    recognised: float = 0.0
    #: How far that run already got: the high-water serial, or -1.0 for a run with
    #: no round yet. -1.0 rather than 0.0, because `r000` exists and 「no round
    #: yet」 must not be spelled the same as 「the decode round」.
    high_water: float = -1.0
    #: How many `came_from` entries the run's intake marker holds after this call
    #: (PSM P-2). A COUNT, never the URL or the note: those are free text, and
    #: free text does not travel in an agent response (INV-12). They are on the
    #: marker, where the human faces read them.
    came_from_count: float = 0.0


@dataclass(frozen=True)
class DecodeResponse(Response):
    """What one decode produced -- **including what it found** (v1.0 §7.1 ④).

    `decode` runs detection inside itself and writes `_findings/` and `_plans/`,
    and until this amendment its response said not one word about the result: the
    caller got cue counts and four paths and had to issue a SECOND verb to learn
    whether anything was wrong. Measured: that wait was 7 minutes 41 seconds on a
    65.6-minute recording (`docs/first-run-2026-09-10.md` §2). **A fact already
    measured should not require the caller to ask again.**

    The five summary fields are the same five `DetectResponse` carries and are
    built from the same `plan_obj.summary`, so the two verbs cannot drift into
    describing one round two ways.
    """

    run: str
    rev: str
    counts: Mapping[str, float]          # cues, windows, stretches, slots
    duration: float
    paths: tuple[str, ...]
    health: Mapping[str, float]          # vad_enabled/degenerated as 0.0/1.0
    findings: Mapping[str, float]        # by code
    proposals: Mapping[str, float]       # by code
    notes: Mapping[str, float]           # by reason
    ops: float                           # total proposed edits; the LIST is `plan`
    detectors_skipped: tuple[Identifier, ...]
    detectors_failed: tuple[tuple[Identifier, Identifier], ...]
    #: (start, end) seconds of every window decoded at temperature 1.0 (I-25).
    #: `health["degenerated"]` says THAT it happened; this says WHERE, because a
    #: fallback is not a detector trigger on its own and the reader has no other
    #: way to find it. Same reason `stretch_table` exists beside the count.
    degenerated_spans: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class DetectResponse(Response):
    run: str
    rev: str
    findings: Mapping[str, float]
    proposals: Mapping[str, float]
    notes: Mapping[str, float]
    unverified: Mapping[str, float]
    detectors_skipped: tuple[Identifier, ...]
    detectors_failed: tuple[tuple[Identifier, Identifier], ...]
    paths: tuple[str, ...]
    #: Recognised and deliberately not reported, by reason (`script-variant`).
    passed_over: Mapping[str, float] = field(default_factory=dict)
    #: Cues by Chinese script (`hant`, `hans`, `none`); empty unless
    #: `script-switch` ran.
    script_cues: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class SpanRef:
    """Where a finding is, in source coordinates (INV-5). Numbers and an id."""

    id: str
    #: **The location, undegraded** (PIM v1.0 §7.1 ②). The row on disk has carried
    #: `cue_id` since the first plan was written and `_span_ref()` used to drop it,
    #: which left `id` -- a string assembled FOR A HUMAN, `"engine-loop:1644-1649"`
    #: -- as the only locator a caller had. Anything wanting the position then had
    #: to split that string, so the display format became the API and the next
    #: change to it would break every caller silently.
    #:
    #: **"Count the lines in `全文/`" is not the alternative it looks like**: that
    #: file is one line per SURVIVING cue, so the moment anyone accepts a deletion
    #: the line numbers and the cue ids stop agreeing and nothing announces it. A
    #: locator that only works before the user has made a decision is the last
    #: thing this product should offer.
    #:
    #: It is an `Identifier` and not a bare `str` because cue ids are machine-made
    #: (`c1644`, `runner.py`) and therefore drawn from a closed set -- which is
    #: also what keeps INV-12 intact: a position may travel, transcript text may
    #: not, and `IDENTIFIER` is what tells them apart.
    cue_id: Identifier
    code: Identifier
    unit: Identifier
    char_start: float
    char_end: float
    evidence: Mapping[str, float]
    #: Set on notes, `None` on findings and on rounds older than A-24. It is
    #: `Note.reason` -- a closed set of three -- so it travels as an `Identifier`
    #: and cannot carry transcript text, which is what lets it be here at all.
    #: Without it `plan` could say a note existed but not which KIND, and the
    #: kinds are different instructions: `near-threshold` asks the reader to go
    #: and look, `single-signal` says one signal held, `no-action` says there was
    #: nothing to propose. The `detect` response counts them by reason already;
    #: this is the same fact at the row a reader actually opens.
    reason: Optional[Identifier] = None


@dataclass(frozen=True)
class OpRef:
    """One proposed edit, as position and code -- never the text it removes.

    **PIM v1.0 §5.5 / §7.1 ③.** `PlanResponse.ops` used to be a count, while the
    verb that adopts a plan is `accept --op N` and N indexes this very list. A
    summary that says 「21 proposed edits」 beside an interface that eats an index
    is a summary missing a field: measured on a real recording, neither face could
    say what the third proposal would delete (`docs/first-run-2026-09-10.md` §4).

    **Order IS the index**, so there is no `index` field: a number stored beside
    the position it is derived from is a second copy that can disagree with the
    first. `accept` enumerates the same `script[]` this list is built from.

    **Why this does not breach INV-12.** Every slot here is a position or a code
    -- the kind of edit, which cue, the character range, which rule proposed it,
    and who. None of it is transcript text: `delete` needs none, and if milestone
    2 ever adds `replace`, the replacement text still does NOT come here (that is
    content, and content leaves only through §7.1's `emit-text`). The line is not
    「list vs count」, it is 「position and code vs content」 -- INV-12's own line.
    """

    op: Identifier
    cue_id: Identifier
    char_start: float
    char_end: float
    code: Identifier
    #: `detector:engine-loop` today, `human` once a person proposes one, and
    #: `advisor:<model>@<promptVersion>` in milestone 2 -- see `Provenance`. This
    #: field is why that type exists: building `OpRef` was the first time an
    #: `origin` had to survive the INV-12 walk, and it does not match `IDENTIFIER`.
    origin: Provenance


@dataclass(frozen=True)
class PlanResponse(Response):
    run: str
    rev: str
    findings: tuple[SpanRef, ...]
    notes: tuple[SpanRef, ...]
    #: The proposals themselves (v1.0), not `len()` of them. See `OpRef`.
    ops: tuple[OpRef, ...]
    unverified: Mapping[str, float]
    #: Dormant replaces, one per sound-alike finding, at the finding's own span;
    #: order is the index `accept --candidate N` takes. Never applied by an
    #: `accept` that does not name them. Position and code only, like `ops`.
    candidates: tuple[OpRef, ...] = ()


@dataclass(frozen=True)
class AcceptResponse(Response):
    """What adoption produced. **A v4 amendment (A-10), and it is a real gap.**

    The card gave `accept` no response of its own, so the first build returned the
    source revision's `PlanResponse` -- which cannot say which revision was
    written. `accept` places a new member set under a new serial, and a caller that
    is not told the serial has to go and look for it, guessing at the naming
    convention. `source` and `rev` are both here because a revision chain is the
    record (`r002.source == "r001"`), and a declined round has the two differing
    with `ops` at zero, which is exactly §7 case (3) said in numbers.
    """

    run: str
    rev: str
    #: An `Identifier`, not a fifth entry in `ID_FIELDS`: a serial is `r001`, which
    #: the pattern already accepts, so the type says what the value is instead of a
    #: name being added to a list. That is A-8's whole argument, applied to the
    #: first field written after it.
    source_rev: Identifier
    ops: float
    declined: float                      # 1.0 when the round changed nothing
    paths: tuple[str, ...]


@dataclass(frozen=True)
class RunRef:
    """One run and how far it has got. `id` and a number, and nothing else.

    **Not a mapping keyed by the run id**, which is what this was first: a run id is
    `<stem>-<hash8>` and the stem is the user's filename, so it can be CJK or hold a
    space -- and every mapping key in an agent-facing response is validated as an
    `Identifier` (`check_mapping_keys`), which such an id cannot pass. Widening that
    check would have been the wrong repair: keys are codes, and the reason they are
    validated is that a mapping keyed by a line of transcript satisfies every static
    check in this file.

    The better shape was there anyway. `runs` plus `revisions` was two containers
    that had to agree with each other -- a list beside a graph, which this project
    has been blocked for twice. One record cannot disagree with itself.
    """

    run: str
    high_water: float


@dataclass(frozen=True)
class RunsResponse(Response):
    runs: tuple[RunRef, ...]


@dataclass(frozen=True)
class ShowResponse(Response):
    run: str
    rev: str
    counts: Mapping[str, float]
    stretches: tuple[Mapping[str, float], ...]   # start/end/agreement, and
    languages: tuple[Identifier, ...]            # the label beside it
    paths: tuple[str, ...]


@dataclass(frozen=True)
class OpenResponse(Response):
    path: str
    exists: float


@dataclass(frozen=True)
class CiteResponse(Response):
    run: str
    rev: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class CancelResponse(Response):
    state: StateName
    accepted: float                      # 1.0 / 0.0, not a bool: `detail`-style
    reason_code: Optional[Identifier]    # the sentence is the CLI's business
    eta_seconds: Optional[float]


@dataclass(frozen=True)
class ErrorResponse(Response):
    code: Identifier
    subject: Optional[Identifier] = None

    #: The closed set. `human-only` and `unknown-verb` are DIFFERENT codes on
    #: purpose (§9, V-10): "not implemented yet" and "deliberately a human action"
    #: must not look identical to the caller.
    CODES = ("human-only", "unknown-verb", "bad-argument", "no-such-run",
             "no-such-revision", "token-layer-absent", "grant-missing",
             "engine-failed", "cancelled")

    def __post_init__(self) -> None:
        Response.__post_init__(self)      # explicit: see the note on `Response`
        if self.code.text not in self.CODES:
            raise SchemaError(
                f"{self.code.text!r} is not one of {self.CODES}; a new failure "
                f"mode gets a name in this tuple, so that a caller can branch on "
                f"it rather than on a message")


#: Every response type the agent face may return. `verbs.py` is asserted against
#: this tuple, so a new response class that nobody registered here is a test
#: failure rather than an unchecked type -- the "forgot to register it" hole is the
#: one an allowlist walk cannot see by itself.
RESPONSES = (IntakeResponse, DecodeResponse, DetectResponse, PlanResponse,
             AcceptResponse, RunsResponse, ShowResponse, OpenResponse,
             CiteResponse, CancelResponse, ErrorResponse)


class AgentError(Exception):
    """A refusal with a code from `ErrorResponse.CODES`, never a bare message.

    `subject` says WHICH KIND of thing was wrong (`audio-missing`, `proof-failed`,
    a verb name, a revision serial) and never a user value. It is validated here,
    at the raise, rather than at `response()`: a run id can be CJK or hold spaces,
    so a call site passing one would otherwise turn a clean refusal into a
    `SchemaError` from three frames away -- and the tempting repair for that is to
    drop the check. The caller already knows which run it asked about; the value
    adds nothing to the answer and is the one place this API could be talked into
    echoing text back (INV-12).
    """

    def __init__(self, code: str, subject: Optional[str] = None) -> None:
        super().__init__(code if subject is None else f"{code}: {subject}")
        if subject is not None and not IDENTIFIER.fullmatch(subject):
            raise SchemaError(
                f"{subject!r} is not an identifier, so it cannot be an error "
                f"subject. A subject names the KIND of thing that was wrong; a "
                f"run id, a path or a line of transcript is not one (INV-12)")
        self.code = code
        self.subject = subject

    def response(self, verb: str) -> ErrorResponse:
        return ErrorResponse(verb=ident(verb), code=ident(self.code),
                             subject=ident(self.subject) if self.subject else None)
