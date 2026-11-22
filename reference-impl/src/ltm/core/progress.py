"""Progress, cancellation, and the state machine they both read.

PSM sections 8 and 8.1. This module exists because of one user sentence --
「單純反灰然後等待很久…更多時間是空等」 -- which is not a complaint about speed.
It is a complaint about running with no way to tell what it is doing.

**`EDGES` is the single place a transition may be edited.** Everything else here
is derived from it: the twelve state names, the four terminal states, and the set
that may be cancelled. A list beside a graph is a copy, and this project has been
blocked twice for exactly that shape -- once when a diagram and a table drifted,
and once when a rule said "only `applying` may refuse" while the graph gave four
non-terminal states no cancel edge, leaving three of them with no legal reply.

Placement note, and it is a deviation from the card's own module table: PSM
section 2 lists this file as `ProgressEvent Cancellation`, and puts it in build
step 6 beside `ui/`. The edge list is here anyway, because section 8.1 derives
`CANCELLABLE` from it inside `CancelAck.__post_init__`, and any other home for the
edges would be a second place a transition can be edited -- the defect this
module's whole design is a reaction to. The type also had to be built with step 4
rather than step 6: `engine/channel.py` cannot emit an event type that does not
exist yet. `test_progress.py` and `test_states.py` stay in step 6 as written.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Mapping, Optional

#: The state graph. 22 edges, and `"[*]"` is mermaid's start/end pseudo-state --
#: kept rather than dropped, so that "which states are terminal" is also read off
#: the graph instead of listed a second time.
#:
#: Spec names, not mermaid identifiers. The three that differ (`failed(engine)`,
#: `failed(proof)`, `awaiting-decision`) differ only because mermaid identifiers
#: take no parentheses or hyphens; code and every downstream document use these.
START = "[*]"

EDGES: tuple[tuple[str, str], ...] = (
    (START, "queued"),
    ("queued", "decoding"),
    ("queued", "cancelled"),
    ("decoding", "detecting"),                 # payload produced
    ("decoding", "failed(engine)"),
    ("decoding", "cancelled"),
    ("detecting", "proposing"),                # findings exist
    ("detecting", "clean"),                    # none, every enabled detector ran
    ("detecting", "inconclusive"),             # none, one could not run
    ("detecting", "cancelled"),
    ("clean", "awaiting-decision"),
    ("inconclusive", "awaiting-decision"),
    ("proposing", "awaiting-decision"),
    ("awaiting-decision", "applying"),         # accepted
    ("awaiting-decision", "done"),             # all declined
    ("awaiting-decision", "cancelled"),
    ("applying", "done"),                      # INV-4 proof passed, files written
    ("applying", "failed(proof)"),             # proof failed, nothing written
    ("done", START),
    ("cancelled", START),
    ("failed(engine)", START),
    ("failed(proof)", START),
)

#: Twelve states, read off the edge list.
STATES: frozenset[str] = frozenset(s for e in EDGES for s in e) - {START}

#: A state with an edge to the end pseudo-state. Four of them.
TERMINAL: frozenset[str] = frozenset(s for s, t in EDGES if t == START)

#: **Derived, never listed** (PSM section 8.1, round 7 RE-1). A state that gains
#: or loses a cancel edge changes the reply rule in the same commit.
CANCELLABLE: frozenset[str] = frozenset(s for s, t in EDGES if t == "cancelled")

#: The two phases that have no denominator and can have none. `total is None` is
#: legal only here, which is what stops limit (b) being satisfied by a field that
#: is always `None`.
NO_DENOMINATOR = frozenset({"load-model", "transcribe-call"})

#: phase -> the unit its denominator counts, `None` for the two above.
#:
#: `language-slots` is a **card amendment (PSM v4, A-7)**, and the reason is the
#: user's complaint rather than tidiness: §5.5.1 prices the per-slot
#: `detect_language` pass at 21.7 s against a 189.8 s decode -- 11% -- and §8.1's
#: phase table had no row for it. An unlisted phase is 21.7 s during which the
#: interface can only keep saying `decode 100%`, which is 「單純反灰然後等待很久」
#: with a full progress bar on top. It has a denominator (the slot count is known
#: before it starts), so INV-22 limit (b) applies to it.
PHASE_UNITS: Mapping[str, Optional[str]] = {
    "load-model": None,
    "transcribe-call": None,
    "decode": "audio-seconds",
    "language-slots": "slots",
    "detect": "cues",
    "apply": "ops",
}

#: INV-22 limit (a). One second between events, in every phase.
MAX_GAP_SECONDS = 1.0

#: An identifier, not a sentence. `ProgressEvent.name` carries "which model" and
#: "which detector"; this pattern is what keeps that field from becoming a place
#: transcript text could travel (INV-12's shape, applied at the source rather
#: than only at the API boundary).
#:
#: Public, because `agentapi.schema` enforces the same rule on every string an
#: agent can read back and the two must be ONE pattern: a second regex beside this
#: one is a second definition of "not transcript text", and they would differ the
#: first time either is widened.
IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


class ProgressError(ValueError):
    """An event or a reply broke the contract of section 8.1."""


class Cancelled(Exception):
    """Raised at a poll point once cancellation has been requested."""


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """One heartbeat. INV-22.

    `detail` carries NUMBERS only, and `name` is an identifier: between them
    there is no field on this type that could carry a line of transcript. That is
    INV-12 enforced where the value is created rather than only where it is
    serialised -- the API's own walker cannot see inside a mapping.
    """

    state: str
    phase: str
    elapsed: float
    done: Optional[float] = None
    total: Optional[float] = None
    unit: Optional[str] = None
    name: Optional[str] = None
    detail: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ProgressError(f"not a state: {self.state!r}")
        if self.phase not in PHASE_UNITS:
            raise ProgressError(f"not a phase: {self.phase!r}; "
                                f"one of {sorted(PHASE_UNITS)}")
        if self.elapsed < 0:
            raise ProgressError(f"elapsed cannot be negative: {self.elapsed}")
        if self.total is None:
            if self.phase not in NO_DENOMINATOR:
                raise ProgressError(
                    f"phase {self.phase!r} has a denominator and must report it "
                    f"(INV-22 limit (b)); total=None is legal only for "
                    f"{sorted(NO_DENOMINATOR)}")
        else:
            if self.phase in NO_DENOMINATOR:
                raise ProgressError(
                    f"phase {self.phase!r} can have no denominator, so a total "
                    f"of {self.total} is a number somebody invented")
            if self.total <= 0:
                raise ProgressError(f"a denominator must be positive: {self.total}")
            if self.done is None:
                raise ProgressError(f"phase {self.phase!r} reports a total and "
                                    f"must report progress against it")
            if self.done < 0 or self.done > self.total:
                raise ProgressError(
                    f"{self.done} of {self.total} is outside the denominator. "
                    f"A progress bar had better stall at 99% than reach 103% -- "
                    f"the second teaches the user to stop believing the number.")
            if self.unit != PHASE_UNITS[self.phase]:
                raise ProgressError(
                    f"phase {self.phase!r} counts {PHASE_UNITS[self.phase]!r}, "
                    f"not {self.unit!r}")
        if self.name is not None and not IDENTIFIER.fullmatch(self.name):
            raise ProgressError(
                f"name is an identifier (which model, which detector), never "
                f"free text: {self.name!r}")
        for key, value in self.detail.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ProgressError(
                    f"detail carries numbers only; {key!r} is {type(value).__name__}. "
                    f"A free-text detail field is a route for transcript content "
                    f"(INV-12).")


@dataclass(frozen=True, slots=True)
class CancelAck:
    """The reply to a cancel request. There is no third response.

    PSM section 8.1 limit (c)/(c'). "Pressed it, nothing happened, then it
    vanished" is the other half of the complaint this module exists for, so a
    cancel is either accepted or refused **with a reason**, and which one is not a
    judgement call: it is `state in CANCELLABLE`, computed from the edge list.

    A cancel arriving in a terminal state is refused with "already finished" -- a
    race, not a defect, and it is what makes this reply a total function over all
    twelve states.
    """

    state: str
    accepted: bool
    reason: Optional[str] = None
    eta_seconds: Optional[float] = None

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ProgressError(f"not a state: {self.state!r}")
        if self.accepted != (self.state in CANCELLABLE):
            raise ProgressError(
                f"state {self.state!r} is "
                f"{'cancellable' if self.state in CANCELLABLE else 'not cancellable'} "
                f"per the section 8 edge list, so accepted must be "
                f"{self.state in CANCELLABLE}, not {self.accepted}. The edge list "
                f"decides this, never a list of state names kept beside it.")
        if (self.reason is None) != self.accepted:
            raise ProgressError(
                "a refusal carries a reason and an acceptance carries none; "
                f"state={self.state!r} accepted={self.accepted} reason={self.reason!r}")
        if self.eta_seconds is not None and self.eta_seconds < 0:
            raise ProgressError(f"eta cannot be negative: {self.eta_seconds}")


def ack(state: str, eta_seconds: Optional[float] = None) -> CancelAck:
    """The reply for any of the twelve states. Total by construction.

    The refusal reasons are here, in one place, because each of them is an
    explanation the user reads: `applying` refuses because of INV-13's
    all-or-nothing, `proposing` because a mid-way cancel yields half a `Plan`,
    `clean`/`inconclusive` because there is nothing left running, and a terminal
    state because it already finished.
    """
    if state in CANCELLABLE:
        return CancelAck(state, True, None, eta_seconds)
    if state in TERMINAL:
        return CancelAck(state, False, FINISHED[1], None)
    return CancelAck(state, False, _REFUSALS[state][1], None)


#: state -> (code, sentence). **One table, two projections**: the sentence is what
#: a person reads and the code is what an agent gets, because INV-12 keeps prose
#: out of every agent-facing field. Two tables would be a copy, and the copy would
#: be the one nobody updates -- this project has been blocked twice for that shape.
_REFUSALS: Mapping[str, tuple[str, str]] = {
    "applying": ("atomic-write",
                 "files are being written as one set; stopping half way would "
                 "leave a revision whose record does not match its files "
                 "(INV-13)"),
    "proposing": ("half-plan",
                  "the plan is being assembled from results already computed; "
                  "stopping now would hand back half a plan"),
    "clean": ("nothing-running",
              "nothing is running -- the transcript was measured and nothing "
              "was found"),
    "inconclusive": ("nothing-running",
                     "nothing is running -- a measurement did not run, which is "
                     "recorded rather than retried"),
}

#: The terminal-state answer, in the same two projections.
FINISHED = ("already-finished", "already finished")


def refusal_code(state: str) -> Optional[str]:
    """The identifier form of a refusal, for the agent face. `None` if accepted."""
    if state in CANCELLABLE:
        return None
    return FINISHED[0] if state in TERMINAL else _REFUSALS[state][0]


def detecting_exit(findings: int, unhealthy: int) -> str:
    """`detecting`'s three exits: mutually exclusive AND exhaustive.

    A total function, with no `else: raise`, because there is no fourth case.
    Note what the first branch does NOT do: a detector failure is still recorded
    in `PlanSummary.detectors_failed` when findings exist (round 7 RE-3).
    "Not asked" governs which edge is taken, never whether the failure is
    written -- `detectors_failed` is a field, not a state.
    """
    if findings > 0:
        return "proposing"
    return "clean" if unhealthy == 0 else "inconclusive"


class Cancellation:
    """A flag polled between segments and between slots (PSM section 8.2).

    Deliberately not a `bool` passed around: the request arrives on another
    thread (the channel reader) while the poll happens on the worker, and a
    plain attribute makes that race invisible. `threading.Event` is the standard
    library's answer and this product depends on nothing else.

    Cancellation is a REQUEST, and the reply says whether it was accepted. The
    two are separate on purpose: `requested` becoming true does not mean the run
    stopped, and the `CancelAck` is what tells the user which it was.
    """

    __slots__ = ("_event", "_at_state")

    def __init__(self) -> None:
        self._event = threading.Event()
        self._at_state: Optional[str] = None

    def request(self, state: str) -> CancelAck:
        """Record the request and answer it in one call, within one interval."""
        reply = ack(state)
        if reply.accepted:
            self._at_state = state
            self._event.set()
        return reply

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    @property
    def requested_at(self) -> Optional[str]:
        """The state the accepted request arrived in, for the record."""
        return self._at_state

    def poll(self) -> None:
        """Raise at a poll point. Callers do not test a flag by hand."""
        if self._event.is_set():
            raise Cancelled(f"cancelled in {self._at_state}")

    def wait(self, timeout: float) -> bool:
        """Sleep, but wake immediately on a cancel. Used by the heartbeat."""
        return self._event.wait(timeout)
