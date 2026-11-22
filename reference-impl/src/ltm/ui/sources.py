"""What the page may be asked to decode. PSM section 19, card GUI-3.

**This is the security surface of the whole increment.** Every other part of the
page reads things the store already holds; this is the one place where a string
that arrived over a socket becomes a path the product will open. So it is an
ALLOWLIST -- the offered path must be one of the paths the catalogue itself just
listed -- and never an inspection of the string.

The difference is the whole point. A check that looks for `..`, or compares
prefixes, is a competition between whoever wrote the check and every spelling a
filesystem accepts: `..` in the middle, a symlink, a junction, a UNC share, a
short 8.3 name, a trailing dot, a device path. A membership test in a set the
product built has no spellings to lose to: resolve what arrived, and ask whether
it is one of ours.

**Where candidates come from, and the conservative default.** Two populations,
and a server launched with no flags has only the first:

1. **the store.** Every run already registered through `intake`, with the source
   file it points at. A page that can only re-decode what a person already
   handed the product cannot reach into the filesystem at all.
2. **`--audio-root DIR`**, repeatable, named by whoever launched the server.
   This is how a new recording becomes reachable from the page, and it is opt-in
   because "the page can start decodes" and "the page can see your disk" are two
   different permissions.

**No durations.** The card's own degradation order puts the duration column
second on the list of things to drop, and this is why: the product measures
duration with `ffprobe` at decode time, the store does not record it, and
probing every candidate to draw a list would spend seconds of a person's wait on
a number the list does not need. Size is free from the directory entry, so size
is what the list carries.
"""
from __future__ import annotations

import json
import os
import pathlib
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from ..store import layout

#: Extensions the page offers. A closed set, because the alternative is offering
#: every file in a directory and letting the engine discover it cannot read one.
#: A recording this list has no name for is still decodable from the command
#: line -- the page is a convenience, not the gate.
AUDIO_SUFFIXES = frozenset({
    ".aac", ".m4a", ".m4b", ".mp3", ".wav", ".flac", ".ogg", ".opus", ".wma",
    ".aiff", ".aif", ".mp4", ".mkv", ".mov", ".webm", ".avi",
})

#: How deep an `--audio-root` is walked. Not unbounded: a root aimed at a drive
#: letter would otherwise turn a page load into a full-disk scan, and a person
#: who wants a file four levels down can name a closer root.
MAX_DEPTH = 3

#: Most files a listing will carry. A page with ten thousand rows is not a
#: chooser; the count is reported so the reader knows the list is partial rather
#: than believing they have seen everything.
MAX_ENTRIES = 500


class NotOffered(Exception):
    """A path that is not in the listing. One code, and it is not a diagnosis.

    The page only ever sends back a path the catalogue gave it, so anything else
    is not a mistake this product can explain -- and a refusal that speculated
    would be telling the caller about the filesystem, which is the thing being
    refused.
    """

    code = "not-offered"


@dataclass(frozen=True)
class Source:
    """One candidate, as the page sees it."""

    path: str                 # resolved, absolute
    name: str
    bytes: int
    origin: str               # "store" or "audio-root"
    run: Optional[str] = None  # the run it is already registered as, if any

    def as_row(self) -> dict[str, Any]:
        return {"path": self.path, "name": self.name, "bytes": self.bytes,
                "origin": self.origin, "run": self.run}


def _key(path: os.PathLike[str] | str) -> str:
    """The comparison key for one path.

    `resolve()` first, so every spelling of one file collapses to the file --
    that is what makes this an allowlist rather than a string check. Then
    `normcase`, because Windows is the platform this runs on and
    `D:\\Talks\\a.m4a` and `d:\\talks\\A.M4A` are one recording: a set keyed on
    the raw string would refuse a correct path and send the reader looking for a
    bug in the product.
    """
    return os.path.normcase(str(pathlib.Path(path).resolve()))


def _walk(root: pathlib.Path, depth: int = MAX_DEPTH) -> Iterable[pathlib.Path]:
    if depth < 0 or not root.is_dir():
        return
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return                                  # unreadable: not an error here
    for entry in entries:
        try:
            if entry.is_dir():
                yield from _walk(entry, depth - 1)
            elif entry.suffix.lower() in AUDIO_SUFFIXES:
                yield entry
        except OSError:
            continue


class Catalogue:
    """The candidates, and the allowlist they define.

    Rebuilt on every call rather than cached: a file the user just dropped into
    an audio root has to appear without restarting the server, and a file that
    has gone away must stop being offered. The walk is bounded by `MAX_DEPTH`
    and `MAX_ENTRIES`, so "rebuild it" costs a directory listing.
    """

    def __init__(self, root: pathlib.Path,
                 audio_roots: Sequence[pathlib.Path] = ()) -> None:
        self.root = pathlib.Path(root)
        self.audio_roots = tuple(pathlib.Path(p) for p in audio_roots)

    # --- listing ----------------------------------------------------------
    def _from_store(self) -> list[Source]:
        out: list[Source] = []
        intake = layout.intake_dir(self.root)
        if not intake.is_dir():
            return out
        for marker in sorted(intake.glob("*.json")):
            try:
                record = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            source = record.get("source")
            if not source:
                continue
            p = pathlib.Path(source)
            if not p.is_file():
                # The media is not copied (R10), so a recording can move or go
                # away between runs. Dropping the row is right: it is a path
                # this product cannot open, and offering it would produce a
                # decode that fails on the reader's behalf.
                continue
            out.append(Source(path=str(p.resolve()), name=p.name,
                              bytes=p.stat().st_size, origin="store",
                              run=record.get("run")))
        return out

    def _from_roots(self) -> list[Source]:
        out: list[Source] = []
        for root in self.audio_roots:
            for p in _walk(root):
                try:
                    out.append(Source(path=str(p.resolve()), name=p.name,
                                      bytes=p.stat().st_size,
                                      origin="audio-root"))
                except OSError:
                    continue
        return out

    def sources(self) -> list[Source]:
        """Every candidate, store first, one row per file."""
        seen: dict[str, Source] = {}
        for source in self._from_store() + self._from_roots():
            seen.setdefault(_key(source.path), source)
        ordered = sorted(seen.values(), key=lambda s: (s.origin != "store",
                                                       s.name.lower()))
        return ordered[:MAX_ENTRIES]

    def listing(self) -> dict[str, Any]:
        rows = self.sources()
        return {
            "sources": [s.as_row() for s in rows],
            "count": len(rows),
            "truncated": len(rows) >= MAX_ENTRIES,
            "audioRoots": [str(p) for p in self.audio_roots],
            # Said out loud, because an empty list has two very different causes
            # and a reader staring at a blank panel deserves to know which.
            "note": ("no --audio-root was named, so only recordings already "
                     "registered with `ltm intake` are offered"
                     if not self.audio_roots else None),
        }

    # --- the allowlist ----------------------------------------------------
    def resolve(self, offered: str) -> pathlib.Path:
        """The path, if it is one of ours. `NotOffered` otherwise.

        Nothing about `offered` is inspected. It is resolved and looked up in the
        set this catalogue just built -- so `..`, a UNC share, a symlink into a
        root, a device path and a drive letter are all handled by the same line,
        without this function knowing any of those words.
        """
        try:
            key = _key(offered)
        except (OSError, ValueError):
            raise NotOffered(offered) from None
        for source in self.sources():
            if _key(source.path) == key:
                return pathlib.Path(source.path)
        raise NotOffered(offered)


def from_settings(root: pathlib.Path,
                  audio_roots: Optional[Sequence[str]] = None) -> Catalogue:
    return Catalogue(root, [pathlib.Path(p) for p in (audio_roots or ())])
