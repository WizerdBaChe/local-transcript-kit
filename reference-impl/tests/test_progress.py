"""The progress contract and the cancel reply. INV-22, PSM section 8.1.

Two of the four §8.1 limits can be checked the moment the types exist, and they
are the two this file covers:

* **(b)** every phase with a denominator reports it -- with the negative control
  the card names: a key-existence check accepts `None` as present.
* **(c')** `accepted` equals "the graph gives this state a cancel edge", and a
  refusal carries a reason -- with all **three** fakes, because a test that only
  asks "is there an ack, and does a refusal carry a reason" is blind in both
  directions at once.

The other two need something that actually emits, and both halves now exist:

* **(a)** the gap ceiling on the DECODE path is `test_engine.py::test_gap_ceiling`
  (both channels, with a no-heartbeat child as its control). The **`detect` path**
  is at the bottom of this file, because it is the one the product itself emits
  rather than the child: 936 cues with nothing between them is the wait R13 is
  about, and the control sleeps so the ceiling can actually be breached.
* **(c)**'s latency, on the same path: a cancel requested mid-scan is answered
  within one event interval, and the control is a scan that polls once at the end.
"""
from __future__ import annotations

import time

import pytest

from fixtures import decodes
from ltm.core import progress
from ltm.core.progress import CancelAck, ProgressEvent, ProgressError
from ltm.detect import detectors

CANCELLABLE = progress.CANCELLABLE
NON_CANCELLABLE_LIVE = progress.STATES - CANCELLABLE - progress.TERMINAL


# --- limit (b): a denominator where one exists ---------------------------

def test_denominator_reported():
    """A phase with a denominator cannot be constructed without one."""
    ok = ProgressEvent(state="decoding", phase="decode", elapsed=12.0,
                       done=412.0, total=2183.68, unit="audio-seconds")
    assert ok.total == 2183.68

    with pytest.raises(ProgressError, match="has a denominator"):
        ProgressEvent(state="decoding", phase="decode", elapsed=12.0)
    with pytest.raises(ProgressError, match="must report progress"):
        ProgressEvent(state="decoding", phase="decode", elapsed=12.0,
                      total=2183.68, unit="audio-seconds")


def test_no_denominator_phases_may_not_invent_one():
    """The other half of (b): `transcribe-call` has no denominator and can have
    none, so a number there is one somebody made up."""
    beat = ProgressEvent(state="decoding", phase="transcribe-call", elapsed=3.1,
                         detail={"estimated_seconds": 6.33})
    assert beat.total is None and beat.detail["estimated_seconds"] == 6.33

    with pytest.raises(ProgressError, match="can have no denominator"):
        ProgressEvent(state="decoding", phase="transcribe-call", elapsed=3.1,
                      done=1.0, total=6.33, unit="audio-seconds")
    assert progress.NO_DENOMINATOR == {"load-model", "transcribe-call"}, (
        "the exemption list is closed; a third entry is a design change")


def test_negative_control_a_key_existence_check_accepts_none():
    """The control §8.1 names for limit (b), and why the check is not `hasattr`.

    The weak predicate is what a reasonable person writes first: "the event has a
    `total` field". It is green on an event whose `total` is `None` for a phase
    that has a denominator -- so it would certify a progress bar with no bar. The
    type refuses to be built at all, which is the difference being asserted.
    """
    fake = {"state": "decoding", "phase": "decode", "elapsed": 12.0,
            "done": None, "total": None, "unit": "audio-seconds"}

    def weak(ev) -> bool:
        return "total" in ev                      # the mistake, spelled out

    assert weak(fake), "the control must actually pass the weak check"
    with pytest.raises(ProgressError):
        ProgressEvent(**fake)


def test_progress_may_not_exceed_its_denominator():
    """A bar had better stall at 99% than reach 103% -- the second teaches the
    user to stop believing the number (E-2's measured 10.00 s overshoot)."""
    with pytest.raises(ProgressError, match="outside the denominator"):
        ProgressEvent(state="decoding", phase="decode", elapsed=1.0,
                      done=2193.68, total=2183.68, unit="audio-seconds")


def test_the_unit_belongs_to_the_phase():
    with pytest.raises(ProgressError, match="counts"):
        ProgressEvent(state="detecting", phase="detect", elapsed=1.0,
                      done=1.0, total=789.0, unit="audio-seconds")
    ok = ProgressEvent(state="detecting", phase="detect", elapsed=1.0,
                       done=1.0, total=789.0, unit="cues", name="engine-loop")
    assert ok.name == "engine-loop"


def test_no_event_field_can_carry_a_line_of_transcript():
    """INV-12 enforced where the value is made, not only where it is serialised.

    A walker over response dataclasses cannot see inside a mapping, so `detail`
    is numbers and `name` is an identifier.
    """
    with pytest.raises(ProgressError, match="never\\s+free text"):
        ProgressEvent(state="decoding", phase="load-model", elapsed=0.1,
                      name="我學會了,我學會了")
    with pytest.raises(ProgressError, match="numbers only"):
        ProgressEvent(state="decoding", phase="load-model", elapsed=0.1,
                      detail={"text": "我學會了"})


# --- limit (c'): the ack is read off the graph ---------------------------

def test_ack_matches_the_edge_list():
    """Round 7 RE-1. `accepted` is computed, never listed.

    Both sides, because the rule is an equivalence: every cancellable state
    accepts, and every non-cancellable one refuses **with a reason**. PIM v0.8
    wrote "only `applying` may refuse", and the graph gives four non-terminal
    states no cancel edge -- so for three of them accepting contradicted the graph
    and refusing contradicted the rule, leaving no legal reply at all.
    """
    assert CANCELLABLE == {"queued", "decoding", "detecting", "awaiting-decision"}
    assert NON_CANCELLABLE_LIVE == {"applying", "proposing", "clean", "inconclusive"}

    for state in sorted(CANCELLABLE):
        reply = progress.ack(state)
        assert reply.accepted and reply.reason is None

    for state in sorted(NON_CANCELLABLE_LIVE):
        reply = progress.ack(state)
        assert not reply.accepted
        assert reply.reason, f"{state} refused without saying why"
    assert "INV-13" in progress.ack("applying").reason


def test_the_reply_is_total_over_all_twelve_states():
    """A cancel in a terminal state is a race, not a defect."""
    for state in sorted(progress.STATES):
        reply = progress.ack(state)
        assert isinstance(reply, CancelAck) and reply.state == state
    for state in sorted(progress.TERMINAL):
        assert progress.ack(state).reason == "already finished"


@pytest.mark.parametrize("state,accepted,reason", [
    ("applying", False, None),        # fake 1: a refusal with no reason
    ("detecting", False, "busy"),     # fake 2: a state WITH an edge refusing
    ("proposing", True, None),        # fake 3: a state with NO edge accepting
])
def test_three_fakes_each_go_red(state, accepted, reason):
    """§8.1's (c') control set, and it is three rather than one on purpose.

    A test that only asks "is there an ack, and does a refusal carry a reason"
    passes fakes 2 and 3 -- blind in both directions at once. Any single way for a
    gate to be broken-but-green will eventually happen.
    """
    with pytest.raises(ProgressError):
        CancelAck(state=state, accepted=accepted, reason=reason)


def test_eta_is_optional_and_says_the_quiet_part():
    """Those seconds of silence after the user presses cancel get a number.

    Worst case on the core path is the `transcribe()` call, ~6.3 s on a 36-minute
    file: the between-segments check cannot begin until the generator exists.
    """
    reply = progress.ack("decoding", eta_seconds=6.33)
    assert reply.accepted and reply.eta_seconds == 6.33
    with pytest.raises(ProgressError, match="negative"):
        CancelAck(state="decoding", accepted=True, eta_seconds=-1.0)


# --- the request object --------------------------------------------------

def test_a_refused_request_does_not_set_the_flag():
    """Cancellation is a request; the ack says whether it was accepted.

    `applying` refusing must leave the run running -- a flag set anyway would stop
    a write half way, which is the thing INV-13 exists to prevent.
    """
    c = progress.Cancellation()
    assert not c.requested
    refused = c.request("applying")
    assert not refused.accepted and not c.requested
    accepted = c.request("decoding")
    assert accepted.accepted and c.requested and c.requested_at == "decoding"
    with pytest.raises(progress.Cancelled):
        c.poll()


def test_poll_is_silent_until_a_request_is_accepted():
    c = progress.Cancellation()
    c.poll()                     # must not raise
    c.request("proposing")       # refused
    c.poll()                     # still must not raise


# --- limit (a) and (c) on the DETECT path -------------------------------

LOUD = decodes.in_repo()[decodes.COND_TRUE]      # 936 cues, one real loop

#: How long the sleeping control spends per cue. 936 x 1.5 ms is about 1.4 s, so a
#: whole-sequence scan breaches the one-second ceiling and a per-cue one does not --
#: the control has to be able to go red or the ceiling is untested.
SLOW = 0.0015


class Slow:
    """`engine-loop`, with the cost a real detector would have on a longer file.

    Not a fake detector: it delegates to the shipping one and sleeps between cues.
    The sleep is what makes the ceiling measurable -- 936 cues of real detection take
    milliseconds, so on this project's own data a stopwatch cannot fail (the same
    reason A-1 gave for putting the cue INDEX in the protocol).
    """

    name = "engine-loop"
    may_delete = True

    def __init__(self, per_cue: float = SLOW) -> None:
        self.per_cue = per_cue
        self.inner = detectors.EngineLoop()

    def scan(self, rec, thr):
        for i, batch in self.inner.scan(rec, thr):
            time.sleep(self.per_cue)
            yield i, batch


class SlowWholeSequence:
    """The negative control: the same work, one yield at the end.

    This is the interface the card originally specified (`-> Iterator[Finding]`),
    and it is here to be REJECTED: it does the same total amount of work and emits
    nothing until it is finished, which is a 1.4-second silence in the middle of a
    phase that claims to report progress.
    """

    name = "engine-loop"
    may_delete = True

    def scan(self, rec, thr):
        out = []
        for i, batch in detectors.EngineLoop().scan(rec, thr):
            time.sleep(SLOW)
            out.extend(batch)
        yield len(rec.cues) - 1, tuple(out)


def _gaps(times: list[float]) -> list[float]:
    return [b - a for a, b in zip(times, times[1:])]


def test_detect_never_goes_quiet_for_a_second(monkeypatch):
    """INV-22 limit (a) on the path the PRODUCT emits, with a control that breaches it.

    The decode path's ceiling is `test_engine.py::test_gap_ceiling`, where the child
    is what emits. Here the product is: `detectors.run` calls `on_step` once per cue,
    and nothing else stands between the user and 936 cues of silence.

    Two-sided, and the negative side is the whole point: the same detector doing the
    same work with one yield at the end must FAIL this, or the check is measuring
    that detection is fast rather than that it reports.
    """
    monkeypatch.setattr(detectors, "build",
                        lambda enabled, terms=None, lex=None: [Slow()])
    seen: list[float] = [time.monotonic()]
    detectors.run(LOUD, on_step=lambda *_a: seen.append(time.monotonic()))
    assert len(seen) == len(LOUD.cues) + 1
    worst = max(_gaps(seen))
    assert worst < progress.MAX_GAP_SECONDS, worst

    monkeypatch.setattr(detectors, "build",
                        lambda enabled, terms=None, lex=None: [SlowWholeSequence()])
    quiet: list[float] = [time.monotonic()]
    detectors.run(LOUD, on_step=lambda *_a: quiet.append(time.monotonic()))
    assert len(quiet) == 2, "the control must emit once, at the end"
    assert max(_gaps(quiet)) > progress.MAX_GAP_SECONDS, (
        "the control did not breach the ceiling, so this machine ran the sleeping "
        "scan in under a second and the check above proves nothing")


def test_a_cancel_during_detecting_is_answered_within_one_interval(monkeypatch):
    """INV-22 limit (c) on the detect path. R13 put `detecting` in the set.

    The latency asserted is one EVENT interval, not a fixed number of milliseconds:
    the poll sits beside the emission, so the worst case is however long one cue
    takes. Measured against `SLOW` here, and the control is a scan that polls once at
    the end -- which answers after the whole file rather than after one cue.
    """
    monkeypatch.setattr(detectors, "build",
                        lambda enabled, terms=None, lex=None: [Slow()])
    cancel = progress.Cancellation()
    asked_at: list[float] = []

    def step(_name, done, _total):
        if done == 100 and not asked_at:
            assert cancel.request("detecting").accepted
            asked_at.append(time.monotonic())

    with pytest.raises(progress.Cancelled):
        detectors.run(LOUD, on_step=step, cancel=cancel)
    answered_after = time.monotonic() - asked_at[0]
    assert answered_after < progress.MAX_GAP_SECONDS, answered_after
    # ...and it stopped where it was asked to, not at the end of the file.
    assert answered_after < len(LOUD.cues) * SLOW

    # The control: one poll at the end. The same request waits out the whole scan,
    # which is the behaviour R13 calls 「空等」 -- so the number above is a property of
    # where the poll is, not of how fast the machine is.
    monkeypatch.setattr(detectors, "build",
                        lambda enabled, terms=None, lex=None: [SlowWholeSequence()])
    late = progress.Cancellation()
    late.request("detecting")
    started = time.monotonic()
    with pytest.raises(progress.Cancelled):
        detectors.run(LOUD, cancel=late)
    assert time.monotonic() - started > progress.MAX_GAP_SECONDS
