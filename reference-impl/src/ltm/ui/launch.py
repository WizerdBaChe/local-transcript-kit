"""`python -m ltm.ui.launch` -- the entry a person double-clicks.

**Still not a verb, and still not an `ltm` subcommand.** PSM section 16.0 (1)
refused `ltm serve` because `tests/test_two_faces.py` asserts that the parser's
subcommands ARE the verb table, and a CLI-only allowlist is the shape A-8
refuses. Nothing here reopens that: this is a module entry point like
`ltm.ui.serve`, unreachable from the verb table, and an agent that walks the
verb table still cannot find a way to open a browser window on somebody's screen.

**What it adds over `python -m ltm.ui.serve` is exactly one decision**: what
`--audio-root` should be when nobody typed one. That flag is the difference
between a page that can re-decode what `intake` already registered and a page
that can reach into the filesystem, and `serve` is right to have no default for
it -- a server started by a script must not silently acquire a permission. A
person who double-clicked an icon is not a script, and answering "nothing is
offered" to that person is a dead end they have no way out of.

So the rule here is:

  * **paths handed in** -- dropped onto the launcher, or typed -- are what the
    page may decode from. Every one of them is named on stdout before the server
    starts, because "the page can see your disk" is a permission and a
    permission that is granted silently is not one;
  * **nothing handed in** falls back to `layout.dropbox(root)`, created at that
    moment and not before;
  * **a FILE handed in** is reported and its folder is used. Explorer makes
    dropping a file at least as easy as dropping a folder, and `--audio-root`
    takes folders -- so the widening happens either way and the only question is
    whether the person is told. They are told;
  * **a path that is not there** is named and skipped, never silently dropped.

Every argument is treated as a path. There is no flag pass-through and that is
deliberate: `--port 8080` would arrive as a flag and a folder called `8080`, and
a launcher that half-understands flags is worse than one that understands none.
`python -m ltm.ui.serve` is still there for every other combination, and the
README says so.
"""
from __future__ import annotations

import pathlib
import sys
from typing import Optional, Sequence

from ..agentapi import verbs
from ..store import layout
from . import serve


def resolve(args: Sequence[str], root: pathlib.Path
            ) -> tuple[list[pathlib.Path], list[str]]:
    """Turn what arrived into folders the page may decode from, plus what to say.

    Returns `(audio_roots, notes)`. **Never raises on a bad path**: a launcher
    that a person double-clicked has no terminal to report a traceback into, and
    a folder that was renamed since the shortcut was made is an ordinary Tuesday,
    not a crash.

    The fallback folder is created HERE rather than by the caller, because the
    decision to create it and the decision to use it are the same decision.
    """
    roots: list[pathlib.Path] = []
    notes: list[str] = []
    for raw in args:
        text = str(raw).strip().strip('"')
        if not text:
            continue
        try:
            path = pathlib.Path(text).expanduser().resolve()
        except OSError as e:                                    # noqa: PERF203
            notes.append(f"  not usable as a path, skipped: {text} ({e})")
            continue
        if path.is_dir():
            roots.append(path)
        elif path.is_file():
            notes.append(f"  {path.name} is a file; the page takes folders, so "
                         f"it can see all of {path.parent}")
            roots.append(path.parent)
        else:
            notes.append(f"  not there any more, skipped: {path}")

    seen: set[pathlib.Path] = set()
    unique = [p for p in roots if not (p in seen or seen.add(p))]

    if not unique:
        box = layout.dropbox(root)
        if args:
            notes.append("  nothing handed in could be used, so the drop folder "
                         "is what the page gets")
        try:
            box.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            notes.append(f"  could not make {box} ({e}); the page will start "
                         f"with nothing to offer")
            return [], notes
        unique = [box]
    return unique, notes


def main(argv: Optional[Sequence[str]] = None,
         root: Optional[pathlib.Path] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = root or verbs.Context().root
    roots, notes = resolve(args, root)
    if not args:
        print("No folder was handed in, so the page gets the drop folder:")
    for line in notes:
        print(line)
    for p in roots:
        print(f"  recordings folder: {p}")
    print("  Put recordings in there, or drop a folder onto this launcher to "
          "use that one instead.")
    print()
    sys.stdout.flush()
    return serve.main([a for p in roots for a in ("--audio-root", str(p))])


if __name__ == "__main__":
    sys.exit(main())
