"""`AGENTS.md`'s enumerations against the shipped constants.

`AGENTS.md` is the machine-facing contract: an agent reads it to learn what codes
it may receive and what phases it may see. A list there that has stopped matching
the code is therefore a PRODUCT defect, not a documentation typo -- the caller
writes a handler for eleven failure codes and the twelfth arrives unhandled.

Round 13 found exactly that (R13a-S2): `EngineFailure.CODES` shipped twelve
members while the sentence in `AGENTS.md` listed eleven -- and the same file gave
the missing one, `compute-unsupported`, a table row of its own ten lines above.
One file contradicting itself, across two gate rounds, because nothing read the
two against each other.

The same round blocked the PIM for the sibling defect in a different enumeration
(`progress.PHASE_UNITS` six phases against a five-row table, R13a-BLOCK-7). The
shape is the one this repository has now recorded a dozen times: **a summary not
derived from its detail is a second copy, and it rots.** This test is the derived
half for the enumerations that are cheap to derive.

**Controls, both sides, and generated from the artifact rather than imagined.**
The negative control is the file as it ships (nothing missing). The positive
control deletes each member in turn from a copy of the text and requires the
check to name exactly that member -- so the control count grows with the
enumerations instead of certifying the one case an author thought of.

**The file this reads is the PACKAGED one** (card CLI-1, 2026-09-16), reached
through `importlib.resources` rather than by walking up from `__file__`. The
first version read `<repo>/AGENTS.md`, which proved a fact about a checkout; the
guide an INSTALLED `ltm agent guide` prints is the one that has to match the
constants shipping beside it, and those are the same file only because the move
made them so. The repo root now holds a pointer, and the last test here is what
keeps it a pointer.
"""
from __future__ import annotations

import importlib.resources
import os
import pathlib
import re

import pytest

from ltm.agentapi import schema, verbs
from ltm.core import progress
from ltm.engine.contract import EngineFailure

HERE = pathlib.Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
#: The packaged contract -- what `ltm agent guide` prints, from wherever `ltm`
#: was imported. `as_file` rather than `.read_text()` on the traversable so the
#: PATH is available to the pointer test below.
AGENTS = pathlib.Path(str(importlib.resources.files("ltm") / "AGENTS.md"))
POINTER = HERE / "AGENTS.md"

#: name of the constant -> the members `AGENTS.md` must spell in backticks.
ENUMERATIONS = {
    "EngineFailure.CODES": tuple(EngineFailure.CODES),
    "ErrorResponse.CODES": tuple(schema.ErrorResponse.CODES),
    "progress.PHASE_UNITS": tuple(progress.PHASE_UNITS),
    "AGENT_VERBS": tuple(verbs.AGENT_VERBS),
}


def missing(text: str, members: tuple[str, ...]) -> set[str]:
    """Members the document never spells as `member`.

    Backticks and not bare substrings for the reason `test_layering.py` gives
    about mfp's vocabulary: `apply`, `decode` and `silent` are ordinary words,
    and a bare substring scan would pass on prose that merely uses them.
    """
    return {m for m in members if f"`{m}`" not in text}


@pytest.fixture(scope="module")
def text() -> str:
    return AGENTS.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted(ENUMERATIONS))
def test_every_member_is_named_in_agents_md(name: str, text: str):
    assert missing(text, ENUMERATIONS[name]) == set(), (
        f"{name} has members `AGENTS.md` does not name. The document is the "
        f"contract an agent reads; a member it omits is a code that arrives "
        f"unhandled")


@pytest.mark.parametrize("name", sorted(ENUMERATIONS))
def test_the_check_notices_a_member_going_missing(name: str, text: str):
    """The positive control, one per member of every enumeration.

    A check that has only ever returned "nothing missing" is not known to be
    reading anything. Deleting a member from the text must produce exactly that
    member -- not an empty set (blind) and not a larger one (over-matching,
    which would mean one member's name is a substring of another's spelling).
    """
    members = ENUMERATIONS[name]
    assert members, f"{name} is empty; this control would prove nothing"
    for member in members:
        wounded = text.replace(f"`{member}`", "«deleted»")
        assert missing(wounded, members) == {member}, (
            f"deleting `{member}` did not produce exactly that member")


# --------------------------------------------------------------------------
# one copy: the packaged file, and a root pointer that stays a pointer (CLI-1)
# --------------------------------------------------------------------------
def headings(text: str) -> set[str]:
    """The guide's own `## n. Title` headings -- its table of contents."""
    return {h.strip() for h in re.findall(r"^##\s+(.+)$", text, re.M)}


def test_the_packaged_guide_is_there_and_the_root_file_points_at_it(text: str):
    """The move's own acceptance: one copy, and the pointer names where it is.

    `importlib.resources` resolved it, so the first assertion is about the
    PACKAGING (a `package-data` entry that stopped matching would fail here, not
    at someone's install). The second is the pointer: a root file that names a
    path nobody maintains is how the next reader ends up with two guides.
    """
    assert AGENTS.is_file(), (
        f"the packaged contract is missing: {AGENTS}. `ltm agent guide` prints "
        f"this file, so its absence is a broken command, not a missing doc")
    assert len(text) > 5000, "the packaged guide is suspiciously short"

    relative = AGENTS.resolve().relative_to(HERE.resolve()).as_posix()
    pointer = POINTER.read_text(encoding="utf-8")
    assert relative in pointer, (
        f"the root pointer does not name {relative!r}; it names something else "
        f"or nothing, and a reader following it lands nowhere")


def test_the_root_pointer_carries_no_contract_text_of_its_own(text: str):
    """The half that keeps "one copy" true a month from now.

    Nothing stops someone answering a question by adding the answer to the root
    file, and then there are two contracts disagreeing by however much has been
    added. The guide's section headings are the cheap tell: a root file growing
    contract text grows the structure with it.

    Positive control: the pointer with one real heading pasted into it must be
    caught -- otherwise this test is a `return True` about a file that happens to
    be short.
    """
    toc = headings(text)
    assert len(toc) >= 8, sorted(toc)          # the population, before using it

    pointer = POINTER.read_text(encoding="utf-8")
    borrowed = {h for h in toc if h in pointer}
    assert borrowed == set(), (
        f"the root pointer has started carrying the contract: {sorted(borrowed)}")

    for heading in sorted(toc):
        wounded = pointer + f"\n## {heading}\n\nsome contract text.\n"
        assert {h for h in toc if h in wounded} == {heading}, (
            f"pasting {heading!r} into the pointer did not produce exactly that "
            f"heading, so the check above is not reading what it claims to")
