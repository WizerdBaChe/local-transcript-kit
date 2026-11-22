"""Every file under `docs/evidence/` is reachable from that folder's own index.

Why a document check lives in the product suite: because the suite is the only
thing in this repository with a habit. `tools/` holds design-time instruments the
author remembers to run; `pytest` runs on every gate round whether anyone
remembered or not, and this defect survived eight rounds of people remembering.

The defect (round 13, R13b-S14): eleven files on disk had never been named in
`docs/evidence/README.md` -- the whole of `verifierH_reruns_2026-09-09/` plus a
second rerun of the transcribe-call cost. The reverse check was clean the entire
time: every file the index names does exist. **Index completeness is only visible
from the disk side**, and a reader who checks the direction that is easy to check
gets told the index is fine.

**The index's own two shorthands are part of the contract, not exceptions to
it.** A row writes `foo_2026-09-16.py ＋ .json ＋ .txt` and names the stem once;
a section names a folder and covers what is inside it. The first version of this
test demanded every file name verbatim and flagged 36 files, 25 of which the
index covers perfectly well -- a check that cries wolf on correct text is one
readers learn to skip, which is the CLAUDE.md gate rule and also how this folder
would end up with a second unread index. So "reachable" is defined the way the
document actually works: the name, or the stem, or a folder on the way to it.

What the index is FOR is the reason this matters: a number in the PIM whose
script is not reachable from the index is a number nobody can find their way
back to.

**The direction that was always clean is deliberately NOT tested.** A reverse
check (does the index name anything that is gone?) was written and thrown away:
the index legitimately mentions `payload.json`, `_origin.json`, a placeholder
`x.py` in a command example and documents in other folders, so it produced four
false positives on a correct index -- the same wolf, pointing the other way.
"""
from __future__ import annotations

import os
import pathlib

HERE = pathlib.Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EVIDENCE = HERE / "docs" / "evidence"
INDEX = EVIDENCE / "README.md"

#: Not evidence: bytecode, and the index itself.
IGNORED_DIRS = {"__pycache__"}


def evidence_files() -> list[pathlib.Path]:
    return sorted(
        p for p in EVIDENCE.rglob("*")
        if p.is_file()
        and p != INDEX
        and not any(part in IGNORED_DIRS for part in p.relative_to(EVIDENCE).parts)
    )


def spellings(p: pathlib.Path) -> tuple[str, ...]:
    """Every string the index may legitimately use to reach this file."""
    rel = p.relative_to(EVIDENCE)
    return (p.name, p.stem) + tuple(part for part in rel.parts[:-1])


def unreachable(text: str, files: list[pathlib.Path]) -> set[str]:
    return {str(p.relative_to(EVIDENCE)) for p in files
            if not any(s in text for s in spellings(p))}


def test_every_evidence_file_is_reachable_from_the_index():
    files = evidence_files()
    assert len(files) > 50, "the folder got smaller than this check expects"
    assert unreachable(INDEX.read_text(encoding="utf-8"), files) == set(), (
        "files on disk the evidence index cannot reach by name, stem or folder. "
        "A script nobody can find from the index is a number nobody can re-run")


def test_the_check_notices_each_file_dropping_out_of_the_index():
    """Positive control, one per file actually present -- and its negative half
    in the same loop.

    Generated from the folder rather than from one hand-written example, so the
    control count grows with the evidence: a fixed control proves the check can
    fire once, this proves it fires for whatever is there now. Blanking every
    spelling of one file may take its siblings with it (they share a stem), so
    the assertion is that the file JOINS the flagged set, having been absent
    from it a line earlier.
    """
    files = evidence_files()
    text = INDEX.read_text(encoding="utf-8")
    clean = unreachable(text, files)
    for p in files:
        name = str(p.relative_to(EVIDENCE))
        assert name not in clean                       # negative half
        wounded = text
        for s in spellings(p):
            wounded = wounded.replace(s, "«deleted»")
        assert name in unreachable(wounded, files), (  # positive half
            f"blanking every spelling of {name} left it looking indexed")
