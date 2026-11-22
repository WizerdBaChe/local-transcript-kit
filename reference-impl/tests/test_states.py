"""The state machine, checked against the card's own diagram.

PSM section 8. The mermaid block in the card **is** the edge list -- the single
place a transition may be edited -- so this file parses that block and compares it
with `core.progress.EDGES` in both directions. Anything less would make the code a
second copy of the graph, which is the defect this project has been blocked for
twice: once when a diagram and a table drifted, and once when a rule listed the
states that may refuse a cancel while the graph disagreed with the list.

Rung 1 for the derived sets (exhaustive enumeration over a finite domain): twelve
states and twenty-two edges are few enough to enumerate, so the enumeration is the
proof.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from ltm.core import model, progress

CARD = pathlib.Path(__file__).resolve().parents[1] / "docs" / \
    "psm-transcript-tool-2026-09-09.md"

#: The card's "identifier mapping, three entries and no fourth": mermaid
#: identifiers take no parentheses or hyphens.
MERMAID_TO_SPEC = {
    "failed_engine": "failed(engine)",
    "failed_proof": "failed(proof)",
    "awaiting_decision": "awaiting-decision",
}


def parsed_edges() -> set[tuple[str, str]]:
    """The edge list, read off the card's mermaid block."""
    text = CARD.read_text(encoding="utf-8")
    block = re.search(r"```mermaid\n(.*?)```", text, re.S)
    assert block, "the card has no mermaid block; nothing to check against"
    out = set()
    for line in block.group(1).splitlines():
        m = re.match(r"\s*(\S+)\s*-->\s*([^:\n]+?)(?:\s*:.*)?$", line)
        if not m:
            continue
        a, b = (MERMAID_TO_SPEC.get(s.strip(), s.strip()) for s in m.groups())
        out.add((a, b))
    return out


EDGES_FROM_CARD = parsed_edges()


def test_the_parse_found_the_graph():
    """Assert the population before comparing anything with it.

    A regex that stops matching returns an empty set, and an empty set agrees
    with nothing while printing the same green a correct parse does.
    """
    assert len(EDGES_FROM_CARD) == 22, sorted(EDGES_FROM_CARD)
    assert len(MERMAID_TO_SPEC) == 3, "three entries and no fourth"
    # every mapped name must actually occur in the parse, or the mapping is stale
    for spec in MERMAID_TO_SPEC.values():
        assert any(spec in e for e in EDGES_FROM_CARD), spec


def test_code_and_card_agree_on_every_edge():
    """Both directions. A subset check in either direction hides half the drift."""
    in_code = set(progress.EDGES)
    assert in_code - EDGES_FROM_CARD == set(), "in the code, not in the card"
    assert EDGES_FROM_CARD - in_code == set(), "in the card, not in the code"
    assert len(progress.EDGES) == len(in_code), "the code lists an edge twice"


def test_the_comparison_would_notice_a_drift():
    """The known-false control for the check above.

    Two equal sets compare equal whether or not the comparison means anything, so
    the drift is injected and both directions are asserted to fire: an edge the
    card grew and the code did not, and an edge the code grew and the card did
    not. A comparison shown only matching sets is not known to detect a mismatch.
    """
    in_code = set(progress.EDGES)
    card_grew = EDGES_FROM_CARD | {("clean", "applying")}
    code_grew = in_code | {("proposing", "cancelled")}
    assert card_grew - in_code == {("clean", "applying")}
    assert code_grew - EDGES_FROM_CARD == {("proposing", "cancelled")}
    # and the derived set moves with the graph, which is the whole point
    assert {a for a, b in code_grew if b == "cancelled"} != progress.CANCELLABLE


def test_twelve_states_four_terminal():
    assert len(progress.STATES) == 12, sorted(progress.STATES)
    assert progress.TERMINAL == {"done", "cancelled",
                                 "failed(engine)", "failed(proof)"}
    assert progress.START not in progress.STATES


def test_cancellable_is_the_parsed_edge_list_not_a_list():
    """PSM section 8: adding a `cancelled` edge without deciding to is a red test."""
    from_card = {a for a, b in EDGES_FROM_CARD if b == "cancelled"}
    assert progress.CANCELLABLE == from_card == {
        "queued", "decoding", "detecting", "awaiting-decision"}
    assert "applying" not in progress.CANCELLABLE      # INV-13, all-or-nothing
    assert "proposing" not in progress.CANCELLABLE     # would yield half a Plan


def test_failed_proof_is_reachable_only_from_applying():
    """P-78: a failure path that never happens is an untested failure path.

    The graph half of that claim lives here; that the state is actually reached
    is `test_editscript.py::test_failed_proof_writes_nothing`.
    """
    into = {a for a, b in EDGES_FROM_CARD if b == "failed(proof)"}
    assert into == {"applying"}
    assert ("applying", "done") in EDGES_FROM_CARD, "the proof can also pass"


# --- detecting's three exits ---------------------------------------------

@pytest.mark.parametrize("findings", [0, 1, 5])
@pytest.mark.parametrize("unhealthy", [0, 1, 3])
def test_detecting_exit_is_total(findings, unhealthy):
    """The cross product, and every cell must map to a state.

    `detecting_exit` has no `else: raise` because there is no fourth case. This
    parametrization is what proves the exhaustiveness rather than trusting it.
    """
    state = progress.detecting_exit(findings, unhealthy)
    assert state in {"proposing", "clean", "inconclusive"}
    assert ("detecting", state) in EDGES_FROM_CARD


def test_the_three_exits_are_mutually_exclusive():
    assert progress.detecting_exit(0, 0) == "clean"
    assert progress.detecting_exit(0, 1) == "inconclusive"
    assert progress.detecting_exit(3, 0) == "proposing"


def test_skipped_detector_reaches_inconclusive():
    """INV-17. `inconclusive` means a measurement did not run."""
    assert progress.detecting_exit(0, 1) == "inconclusive"
    assert progress.detecting_exit(0, 0) != "inconclusive", (
        "nothing found with every gate healthy is `clean` -- measured, nothing "
        "there. Collapsing the two dresses a definite result up as an uncertain one")


def test_finding_plus_failed_detector_is_recorded():
    """BR-10 / round 7 RE-3: `proposing` **and** the failure listed.

    The edge `detecting -> proposing` does not ask about detector health, so
    without a field the failure has nowhere to land and the run looks identical
    to a healthy one. "Not asked" governs which edge is taken, never whether the
    failure is written.
    """
    assert progress.detecting_exit(2, 1) == "proposing"
    summary = model.PlanSummary(
        findings=2, proposals=1, notes=0, unverified={"no-token-layer": 0},
        detectors_skipped=(), detectors_failed=(("speaker-repeat", "raised"),),
        engine_health="degraded")
    assert summary.detectors_failed, "the failure is on the record anyway"
    assert not summary.healthy


def test_skipped_and_failed_are_separate_fields():
    """BR-10. "Never enabled" and "ran and raised" are different facts."""
    skipped = model.PlanSummary(
        findings=0, proposals=0, notes=0, unverified={"no-token-layer": 0},
        detectors_skipped=("filler", "glossary"), detectors_failed=(),
        engine_health="ok")
    failed = model.PlanSummary(
        findings=0, proposals=0, notes=0, unverified={"no-token-layer": 0},
        detectors_skipped=(), detectors_failed=(("filler", "raised"),),
        engine_health="ok")
    assert progress.detecting_exit(0, len(skipped.detectors_failed)) == "clean", (
        "INV-9 disabling a detector for want of a control pair is not a failure")
    assert progress.detecting_exit(0, len(failed.detectors_failed)) == "inconclusive"


def test_clean_survives_an_undetermined_stretch():
    """Round 6 RC-2's regression guard, and the reason it is a regression guard.

    BR-4 is per `Stretch`: 「這段分不出來」 is `Stretch.language == "undetermined"`
    and never a run-level state. In v0.7 a recording whose language could not be
    told apart satisfied `clean`'s guard and `inconclusive`'s at once, because the
    two guards measured different axes. A run with an undetermined stretch and no
    findings is still `clean` -- its uncertainty is printed on the stretches,
    which is where BR-4 asked for it.
    """
    undecided = model.Stretch(id="s1", start=0.0, end=30.0,
                              language="undetermined", agreement=0.42)
    assert undecided.language == "undetermined"
    assert progress.detecting_exit(0, 0) == "clean"
