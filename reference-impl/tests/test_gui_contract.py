"""`docs/gui-contract.json` and the face it describes, compared both ways.

PSM section 19, card GUI-6. The card says "extend `tests/test_gui_contract.py`";
there was no such file -- until v6 the contract described the route table in
PROSE (`exposure.where`) and the only machine-checkable statement about it lived
in `serve.py`. That was survivable while the face had three read-only routes. It
stopped being survivable the day the face grew four JSON routes in one commit,
which is the exact change INV-23 names as the one nobody blocks at review.

**The direction that finds things is contract -> server.** A route added to
`serve.py` with no row here is the change nobody wrote down; the reverse -- a row
here for a route that does not exist -- is a documentation error, real but
quieter. Round 13 measured the same asymmetry on the evidence index, where the
reverse direction was clean throughout, so both are asserted and the noisy one is
named.

Every field in a row is DERIVABLE from the server: the method from
`POST_ROUTES`, `carriesText` from `TEXT_ROUTES`, `body` from `JSON_ROUTES`. That
is what makes this a comparison rather than a second description of the same
thing -- and it is why the mutation controls below can be generated from the
contract's own rows instead of hand-written.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

from ltm.ui import serve

HERE = pathlib.Path(__file__).resolve().parent
DOCS = HERE.parent / "docs"
CONTRACT_PATH = DOCS / "gui-contract.json"
MAPPING_PATH = DOCS / "gui-mapping-2026-09-10.md"


@pytest.fixture(scope="module")
def contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def mapping() -> str:
    return MAPPING_PATH.read_text(encoding="utf-8")


def from_server(path: str) -> dict:
    """The row the server would write for itself. One place, used by every test."""
    return {
        "path": path,
        "method": "POST" if path in serve.POST_ROUTES else "GET",
        "carriesText": path in serve.TEXT_ROUTES,
        "body": "json" if path in serve.JSON_ROUTES else "html",
    }


def rows_of(contract: dict) -> dict[str, dict]:
    return {r["path"]: {k: r[k] for k in ("path", "method", "carriesText", "body")}
            for r in contract["routes"]}


# --------------------------------------------------------------------------
# both directions
# --------------------------------------------------------------------------
def test_every_route_the_server_answers_has_a_row(contract):
    """contract <- server. The direction that finds a route nobody wrote down."""
    missing = sorted(set(serve.ROUTES) - set(rows_of(contract)))
    assert not missing, (
        f"the server answers routes the contract does not name: {missing}. "
        f"Add a row to {CONTRACT_PATH.name} and bump contractVersion -- the "
        f"seam changed.")


def test_every_row_names_a_route_the_server_answers(contract):
    """contract -> server. The quieter direction, asserted anyway."""
    stale = sorted(set(rows_of(contract)) - set(serve.ROUTES))
    assert not stale, f"the contract names routes that do not exist: {stale}"


@pytest.mark.parametrize("path", sorted(serve.ROUTES))
def test_each_row_agrees_with_the_server_field_by_field(path, contract):
    """Not just presence: the method, the text flag and the body kind.

    A row that exists but says GET for a POST route is worse than a missing row,
    because it reads as checked.
    """
    row = rows_of(contract).get(path)
    assert row is not None, f"no row for {path}"
    assert row == from_server(path)


@pytest.mark.parametrize("field,wrong", [
    ("method", "POST"),
    ("carriesText", True),
    ("body", "json"),
])
def test_a_row_that_disagrees_with_the_server_is_caught(field, wrong, contract):
    """The positive control, generated FROM the contract's own rows.

    Mutating one field of every row that does not already carry the wrong value,
    so the control count grows with the route table instead of certifying the one
    route its author picked. The mutation is chosen to be a value the server
    could plausibly have -- `POST`, text-bearing, JSON -- because a nonsense
    value would be caught by anything.
    """
    rows = rows_of(contract)
    mutated = [p for p, row in rows.items() if row[field] != wrong]
    assert mutated, f"nothing to mutate for {field}; this control is not running"
    for path in mutated:
        row = dict(rows[path])
        row[field] = wrong
        assert row != from_server(path), (
            f"{path} with {field}={wrong!r} still matched the server, so the "
            f"comparison is not reading {field}")


def test_a_route_the_contract_does_not_know_is_caught(contract):
    """The other positive control: the check must notice an EXTRA server route.

    Without it, `set(ROUTES) - set(rows)` returning empty would look identical
    on a working comparison and on one whose `rows_of` came back with every path
    in the world.
    """
    pretend = set(serve.ROUTES) | {"/api/transcript"}
    assert sorted(pretend - set(rows_of(contract))) == ["/api/transcript"]


# --------------------------------------------------------------------------
# what the route table may not become
# --------------------------------------------------------------------------
def test_exactly_one_route_carries_text_and_the_contract_names_it(contract):
    """INV-23 stated as a count, which is the form that survives a new route.

    "The text routes are enumerated" is satisfied by a list of seven. What the
    invariant is actually protecting is that the list stays SHORT and that each
    member had to be argued for.
    """
    bearing = [r["path"] for r in contract["routes"] if r["carriesText"]]
    assert bearing == ["/run"]
    assert set(bearing) == set(serve.TEXT_ROUTES)


def test_no_json_route_carries_text_in_the_contract_either(contract):
    """The disjointness `serve.py` asserts at import time, said here too.

    Two places, on purpose: the assertion in the module stops the server from
    starting, and this stops the CONTRACT from being written the wrong way --
    which is how the widening would arrive, as a row somebody edited rather than
    as code somebody wrote.
    """
    both = [r["path"] for r in contract["routes"]
            if r["carriesText"] and r["body"] == "json"]
    assert not both, f"a JSON route is marked text-bearing: {both}"
    assert not (serve.JSON_ROUTES & serve.TEXT_ROUTES)


def test_every_row_says_what_the_route_is_for(contract):
    """A table of paths with no `what` is a list, not a contract."""
    thin = [r["path"] for r in contract["routes"] if len(r.get("what", "")) < 40]
    assert not thin, f"these rows say nothing about what the route does: {thin}"


# --------------------------------------------------------------------------
# the version and the change log
# --------------------------------------------------------------------------
def test_the_change_log_has_a_row_for_the_current_contract_version(contract,
                                                                  mapping):
    """`changeDiscipline`'s own rule, checked instead of remembered.

    "Bump contractVersion and add a row to the change log" is two actions, and
    the second is the one that gets forgotten -- the version number is in the
    file being edited and the log is in another document.
    """
    version = contract["contractVersion"]
    rows = re.findall(r"^\|\s*(\d+)\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|",
                      mapping, re.MULTILINE)
    logged = {int(n) for n, _ in rows}
    assert version in logged, (
        f"contractVersion is {version} but the change log in "
        f"{MAPPING_PATH.name} stops at {max(logged)}")
    assert logged == set(range(1, version + 1)), (
        f"the change log skips a version: {sorted(logged)}")


def test_the_mapping_document_covers_the_new_controls(mapping):
    """The G-rows the card asks for: the panel's controls are named somewhere.

    Named where a reader of the seam will find them, which is the mapping
    document -- the contract file is for the machine and the PSM card is for
    whoever built it.
    """
    for control in ("/api/intake", "/api/decode", "/api/progress",
                    "/api/cancel", "--audio-root", "--read-only"):
        assert control in mapping, f"{control} is nowhere in {MAPPING_PATH.name}"


def test_there_is_exactly_one_machine_readable_copy_of_this_seam():
    """`changeDiscipline`: do not restate this interface anywhere else.

    Narrow on purpose, because the rule is about a SECOND SOURCE and not about
    a second mention: prose may name a route as often as it likes -- the mapping
    document does, and the test above requires it to. What may not exist is
    another file a program could read the seam out of, because that is the pair
    that drifts without either half looking wrong.

    So: no other JSON under `docs/` may carry `contractVersion` or a `routes`
    array. The control is that this test can see the one that is allowed.
    """
    carriers = []
    for doc in sorted(DOCS.rglob("*.json")):
        try:
            loaded = json.loads(doc.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(loaded, dict):
            continue
        if "contractVersion" in loaded or "routes" in loaded:
            carriers.append(doc)
    assert carriers == [CONTRACT_PATH], (
        f"the seam is machine-readable from more than one file: "
        f"{[str(p) for p in carriers]}")
