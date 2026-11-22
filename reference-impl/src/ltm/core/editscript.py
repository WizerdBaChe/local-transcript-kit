"""Edits as a script that can be proved to revert, never as replacement text.

PSM section 5. The product's whole promise rests on this file: it may propose
that text be REMOVED, and it may only write the result after proving in memory
that the removal reverts exactly (INV-3, INV-4). A tool that hands back rewritten
prose asks the reader to trust a second machine; a tool that hands back a
deletion script asks them to check one.

Coordinates are SOURCE coordinates, always (INV-5). `merge` sorts and rejects
overlap; it never rebases one script onto another's output, because a rebased
span cannot be pointed back at the transcript the reader is looking at.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Mapping, Optional, Sequence

from .model import ModelError, Span

Op = Literal["delete", "replace", "insert"]

#: `origin` values milestone 1 produces. The `advisor:<model>@<promptVersion>`
#: form is accepted by the value domain and never produced here, so milestone 2
#: changes no on-disk record format (PSM section 0).
ORIGIN_PREFIXES = ("detector:", "human", "advisor:")


class ScriptError(ModelError):
    """A script that cannot be applied as written."""


@dataclass(frozen=True, slots=True)
class EditOp:
    op: Op
    span: Span
    text: Optional[str]
    code: str
    origin: str

    def __post_init__(self) -> None:
        if self.op == "delete" and self.text is not None:
            raise ScriptError("a delete carries no replacement text")
        if self.op in ("replace", "insert") and self.text is None:
            raise ScriptError(f"{self.op} needs text")
        if self.op == "insert" and len(self.span) != 0:
            raise ScriptError("an insert is a zero-width span")
        if self.op == "delete" and len(self.span) == 0:
            raise ScriptError("a zero-width delete removes nothing")
        if not self.code:
            raise ScriptError("every op names the rule that proposed it")
        if not any(self.origin.startswith(p) for p in ORIGIN_PREFIXES):
            raise ScriptError(
                f"origin must start with one of {ORIGIN_PREFIXES}: "
                f"{self.origin!r} (PIM section 5.3)"
            )


EditScript = tuple[EditOp, ...]


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """What `apply` removed, and from where.

    This is what makes `revert` TOTAL rather than best-effort: reverting does
    not re-derive anything, it puts back a string it was handed.
    """

    cue_id: str
    char_start: int
    char_end: int
    removed: str
    inserted: str


Journal = tuple[JournalEntry, ...]


def _by_cue(script: Iterable[EditOp]) -> dict[str, list[EditOp]]:
    out: dict[str, list[EditOp]] = {}
    for op in script:
        out.setdefault(op.span.cue_id, []).append(op)
    return out


def _check_disjoint(ops: Sequence[EditOp]) -> None:
    """Overlap is a programming error, not a runtime condition.

    The state machine never reaches `applying` with an overlapping script,
    because `merge` refuses to build one -- so this raises rather than
    resolving a conflict by precedence. A precedence rule here would silently
    pick a winner, and the reader would never learn there were two.
    """
    ordered = sorted(ops, key=lambda o: (o.span.char_start, o.span.char_end))
    for a, b in zip(ordered, ordered[1:]):
        # Two inserts at the same point do not overlap in text, but their order
        # is undefined -- which is the same defect one step later.
        if b.span.char_start < a.span.char_end or (
            len(a.span) == 0 and len(b.span) == 0
            and a.span.char_start == b.span.char_start
        ):
            raise ScriptError(
                f"overlapping ops in cue {a.span.cue_id!r}: "
                f"[{a.span.char_start},{a.span.char_end}) and "
                f"[{b.span.char_start},{b.span.char_end})"
            )


def apply(source: Mapping[str, str], script: EditScript) -> tuple[dict[str, str], Journal]:
    """Apply in source coordinates, right to left, and journal every change.

    Right to left inside each cue is what keeps the spans in SOURCE
    coordinates: no earlier edit can shift a later one's offsets, so nothing
    has to be rebased and INV-5 holds by construction rather than by care.
    """
    result = dict(source)
    entries: list[JournalEntry] = []
    for cue_id, ops in _by_cue(script).items():
        if cue_id not in result:
            raise ScriptError(f"script names cue {cue_id!r}, which is not here")
        _check_disjoint(ops)
        text = result[cue_id]
        for op in sorted(ops, key=lambda o: o.span.char_start, reverse=True):
            s, e = op.span.char_start, op.span.char_end
            if e > len(text):
                raise ScriptError(
                    f"span [{s},{e}) runs past cue {cue_id!r} (len {len(text)})"
                )
            removed = text[s:e]
            inserted = "" if op.op == "delete" else (op.text or "")
            text = text[:s] + inserted + text[e:]
            entries.append(JournalEntry(cue_id, s, e, removed, inserted))
        result[cue_id] = text
    # Journalled in application order (right to left per cue); revert walks it
    # backwards, so the two orders are each other's inverse by construction.
    return result, tuple(entries)


def revert(result: Mapping[str, str], journal: Journal) -> dict[str, str]:
    out = dict(result)
    for entry in reversed(journal):
        text = out[entry.cue_id]
        s = entry.char_start
        e = s + len(entry.inserted)
        if text[s:e] != entry.inserted:
            raise ScriptError(
                f"journal does not describe this text at cue {entry.cue_id!r}"
            )
        out[entry.cue_id] = text[:s] + entry.removed + text[e:]
    return out


def merge(a: EditScript, b: EditScript) -> EditScript:
    """Union in source coordinates. Not composition.

    Composition would mean rebasing `b` onto `a`'s output, and a rebased span
    no longer points at the transcript the reader is reading (INV-5).
    """
    merged = tuple(a) + tuple(b)
    for ops in _by_cue(merged).values():
        _check_disjoint(ops)
    return tuple(sorted(merged, key=lambda o: (o.span.cue_id, o.span.char_start)))


def prove(source: Mapping[str, str], script: EditScript) -> bool:
    """INV-4: the proof runs in memory BEFORE anything is written.

    A false return sends the run to `failed(proof)` and **nothing is written**.
    It returns a bool rather than raising because "this script does not revert"
    is a legitimate outcome of a legitimate run, not a bug -- the state machine
    has an edge for it.
    """
    try:
        applied, journal = apply(source, script)
    except ScriptError:
        return False
    return revert(applied, journal) == dict(source)
