"""`place_set`: a set of files lands whole, or not at all. INV-13.

PSM section 7's three properties, and none of them is optional:

1. **All or nothing.** If any name in the set already exists, the whole set fails
   and nothing lands. A half-written set produces a record whose serial does not
   match the files it describes -- worse than the overwrite it was meant to
   prevent.
2. **Length computed before writing.** Over the limit, the whole set is refused
   and the offending member is NAMED. Never truncate: a shortened name is a
   silent overwrite.
3. **Serials are never reused.** `max(high water in the record, highest on disk)
   + 1`, and the high-water mark only ever rises. Scanning alone re-issues `r002`
   after someone deletes `r002`, and then the edit record for `r002` describes
   different content -- a mismatch no downstream consumer can see. **Scanning
   cannot see subtraction** (P-73); the high-water mark is what sees it.
"""
from __future__ import annotations

import pathlib
from typing import Iterable, Mapping

from . import layout


class PlaceError(RuntimeError):
    """A set was refused. Nothing was placed."""


def next_serial(high_water: int, on_disk: Iterable[str]) -> str:
    """The next serial, from BOTH sources, never from either alone.

    `high_water` is the number recorded in `_origin.json`; `on_disk` is what a
    scan can see. A scan alone cannot see a deletion, and a record alone cannot
    see a file someone placed by hand.
    """
    seen = []
    for name in on_disk:
        try:
            seen.append(layout.serial_number(name))
        except ValueError:
            continue                  # not a serial; not ours to interpret
    return layout.serial(max([high_water, *seen], default=-1) + 1)


def serials_on_disk(root: pathlib.Path, run: str) -> set[str]:
    """Every serial any member directory mentions.

    Deliberately over-inclusive: a serial is consumed if ANY of its members
    exists, so a leftover `_plans/r003.json` still blocks `r003` from being
    re-issued to different content.
    """
    d = layout.run_dir(root, run)
    out: set[str] = set()
    for sub in (layout.TIMED, layout.PLAIN, layout.EDITS, layout.FINDINGS,
                layout.PLANS):
        folder = d / sub
        if not folder.is_dir():
            continue
        for f in folder.iterdir():
            for part in f.name.split("."):
                try:
                    layout.serial_number(part)
                except ValueError:
                    continue
                out.add(part)
    return out


def check_lengths(paths: Mapping[str, pathlib.Path],
                  max_path: int = layout.MAX_PATH) -> None:
    over = sorted(((len(str(p)), kind, p) for kind, p in paths.items()
                   if len(str(p)) > max_path), reverse=True)
    if over:
        n, kind, p = over[0]
        raise PlaceError(
            f"the whole set is refused: member {kind!r} would be {n} characters, "
            f"over the {max_path} limit -- {p}. Nothing was written, and no name "
            f"was shortened to fit."
        )


def place_set(root: pathlib.Path, run: str, rev: str, stem: str,
              payloads: Mapping[str, str | bytes], lang: str | None = None,
              max_path: int = layout.MAX_PATH) -> dict[str, pathlib.Path]:
    """Place every member or none. Create-only: an existing name is a refusal.

    Ordering, and it is a decision rather than an accident: temporary files are
    written first and renamed into place afterwards, and the caller updates the
    record LAST. A crash between renames therefore leaves files that no record
    claims -- and because the high-water mark has not risen, the next round
    proposes the same serial, finds those files, and refuses the whole set with
    a collision named. Loud, and recoverable by hand. The alternative ordering
    (record first) would leave a record pointing at files that do not exist,
    which nothing downstream can detect.
    """
    paths = layout.member_paths(root, run, rev, stem, payloads.keys(), lang)
    check_lengths(paths, max_path)

    existing = {kind: p for kind, p in paths.items() if p.exists()}
    if existing:
        raise PlaceError(
            "the whole set is refused: these members already exist -- "
            + ", ".join(f"{k} -> {v}" for k, v in sorted(existing.items()))
            + ". This store is create-only; nothing was overwritten."
        )

    written: list[pathlib.Path] = []
    temps: list[pathlib.Path] = []
    try:
        for kind, path in paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".partial")
            if tmp.exists():
                raise PlaceError(
                    f"a leftover temporary file is in the way: {tmp}. Another "
                    f"run may be placing this set; nothing was written."
                )
            data = payloads[kind]
            if isinstance(data, bytes):
                tmp.write_bytes(data)
            else:
                # LF, explicitly. The store is read by both products' worth of
                # tools and by the user's editor; a platform-dependent newline
                # would make one recording's files differ from another's for no
                # reason a reader could see.
                with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                    f.write(data)
            temps.append(tmp)
        for kind, path in paths.items():
            tmp = path.with_name(path.name + ".partial")
            tmp.replace(path)
            temps.remove(tmp)
            written.append(path)
    except Exception:
        for p in written:
            p.unlink(missing_ok=True)
        for p in temps:
            p.unlink(missing_ok=True)
        raise
    return paths
