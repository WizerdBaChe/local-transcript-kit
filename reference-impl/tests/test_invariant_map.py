"""§10's invariant-to-test map, checked against the suite that exists.

Deferred from step 5 to step 6 on purpose (ledger row): a checker written while
half the tests were unwritten would have needed an allowance list of "not yet",
and an allowance list is where a map goes to stop being checked. **This ships with
no allowance list.** Every `file.py::test_name` the map quotes must exist.

The map is the document's claim that every invariant has a gate and every gate
names its invariant. A row quoting a test that was renamed is worse than a row with
no test at all: the first says the invariant is covered.

Two directions, and both matter:

* every id the map names exists in the suite (this is the one that rots);
* every INV-n in the invariant list has a row -- with the milestone-2 ones marked
  and named, because 「no test on purpose」 and 「nobody wrote one」 must not share a
  spelling (INV-17's shape, applied to the document).

The known-false controls are at the bottom: an id that does not exist must be
caught, and an id that does must not be.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

HERE = pathlib.Path(__file__).resolve().parent
CARD = HERE.parent / "docs" / "psm-transcript-tool-2026-09-09.md"

#: `test_x.py::test_y` and the bare `::test_y` continuation form the map uses when
#: several ids come from one file.
_QUALIFIED = re.compile(r"`(test_[a-z0-9_]+\.py)::(test_[a-z0-9_]+)`")
_BARE = re.compile(r"`::(test_[a-z0-9_]+)`")
_ROW = re.compile(r"^\| *(?:\*\*)?(INV-\d+|BR-\d+|E-\d+|T-\d+|RB-\d+)")


def _section_10() -> str:
    text = CARD.read_text("utf-8")
    start = text.index("## 10. Invariant-to-test map")
    return text[start:text.index("## 11. Build order", start)]


def test_names_in_the_map() -> None:
    """The map quotes at least this many ids, or the parser is reading nothing."""
    assert len(_ids()) >= 30, "the map parser found almost nothing; check the regex"


def _ids() -> list[tuple[str, str]]:
    """`(file, test)` for every id in §10, resolving the bare `::name` form.

    A bare id belongs to the last file named on the SAME table row, which is how the
    map is written -- so the resolution is per line, never across the whole section:
    letting it carry over would silently attach an id to a file it was never
    written under, and the checker would then verify the wrong thing and pass.
    """
    out: list[tuple[str, str]] = []
    for line in _section_10().splitlines():
        current = None
        for match in re.finditer(r"`(test_[a-z0-9_]+\.py)::(test_[a-z0-9_]+)`"
                                 r"|`::(test_[a-z0-9_]+)`", line):
            if match.group(1):
                current = match.group(1)
                out.append((current, match.group(2)))
            elif current is not None:
                out.append((current, match.group(3)))
    return out


def _tests_in(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text("utf-8"))
    return {n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name.startswith("test_")}


def test_every_test_the_map_names_exists():
    """No allowance list. A row naming a test that is gone is a false claim of cover.

    This is the check the project deferred rather than weakened: written in step 5 it
    would have carried "not yet" entries for every step-6 test, and the day those
    landed nobody would have removed them.
    """
    missing = []
    for filename, name in _ids():
        path = HERE / filename
        if not path.exists():
            missing.append(f"{filename} (no such file)")
        elif name not in _tests_in(path):
            missing.append(f"{filename}::{name}")
    assert not missing, (
        "PSM §10 names test(s) that do not exist:\n  "
        + "\n  ".join(sorted(set(missing)))
        + "\nA map row quoting a missing test claims an invariant is covered when "
          "it is not. Fix the row or restore the test -- never add an allowance.")


def test_every_invariant_has_a_row_and_the_absent_ones_say_why():
    """Both directions. A milestone-2 invariant has no test ON PURPOSE (§0).

    So the row must exist and must be marked -- 「deliberately not yet」 and
    「forgotten」 are different facts, which is INV-17's own argument turned on the
    document that states it.
    """
    section = _section_10()
    rows = {m.group(1): line
            for line in section.splitlines()
            if (m := _ROW.match(line))}
    for n in range(1, 23):
        assert f"INV-{n}" in rows, f"INV-{n} has no row in §10"
    for n in (10, 19, 21):
        row = rows[f"INV-{n}"]
        assert "M2" in row or "〔M2〕" in row, (
            f"INV-{n} is a milestone-2 invariant with no milestone-1 test; the row "
            f"must SAY so, or an empty cell reads as an oversight")
        assert "test_" not in row


# --- the controls ---------------------------------------------------------

def test_negative_control_the_checker_catches_a_missing_test():
    """A checker never shown a violation is not known to detect one."""
    assert "test_this_was_never_written" not in _tests_in(HERE / "test_detect.py")
    assert "test_absent_gate_marks_unverified" in _tests_in(HERE / "test_detect.py")


def test_negative_control_a_bare_id_does_not_cross_rows():
    """The resolution rule itself, on text that would break a looser one.

    A `::name` with no file named earlier ON ITS OWN LINE must be dropped, not
    attached to whatever file the previous row happened to mention. A checker that
    got this wrong would look for the right test in the wrong file and either fail
    loudly or -- worse, if the name exists in both -- pass for the wrong reason.
    """
    line_pairs = []
    for line in ["| INV-x | `test_detect.py::test_a`, `::test_b` |",
                 "| INV-y | `::test_c` |"]:
        current = None
        for match in re.finditer(r"`(test_[a-z0-9_]+\.py)::(test_[a-z0-9_]+)`"
                                 r"|`::(test_[a-z0-9_]+)`", line):
            if match.group(1):
                current = match.group(1)
                line_pairs.append((current, match.group(2)))
            elif current is not None:
                line_pairs.append((current, match.group(3)))
    assert line_pairs == [("test_detect.py", "test_a"), ("test_detect.py", "test_b")]
