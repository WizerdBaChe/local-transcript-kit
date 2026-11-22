"""Where a file came from: a URL and a short note, recorded on the intake marker.

PSM card P-2 (2026-11-22). Generic on purpose -- R10's ordinary door: the caller
may be a person, an agent that ran a downloader, or a phone sync, and nothing here
knows any of them. Nothing here touches the network either: whether a URL exists
is not this product's question.

Named `came_from`, not "provenance", because `agentapi.schema.Provenance` already
means something else in this codebase (who proposed an edit). Two meanings under
one word is the drift R17 asks us to avoid.

The list is APPEND-ONLY and deduplicated on `(url, note)`: the same recording can
arrive from two places, and a later plain intake must not erase what an earlier
one recorded (the marker file is rewritten on every intake).

Pure functions, except `on_marker`, which only reads.
"""
from __future__ import annotations

import datetime
import json
import pathlib
import re
from typing import Optional, Sequence

#: The marker key. A list of `{"url", "note", "at"}`, newest last.
KEY = "came_from"
URL_MAX = 2048
NOTE_MAX = 200
#: `http` or `https`, `://`, then a non-empty host. A regex and NOT
#: `urllib.parse`: the layering gate (INV-18, `tests/test_layering.py`) refuses
#: every `urllib` import in product code, the pure half included, so that "this
#: package contains no HTTP client" stays checkable by an import graph. Whitespace
#: and control characters are refused before this runs.
_SHAPE = re.compile(r"(?i)https?://[^/?#@]+(?:[/?#].*)?\Z")


class Refused(ValueError):
    """`subject` is the AgentError subject: `from-url` or `from-note`."""

    def __init__(self, subject: str) -> None:
        super().__init__(subject)
        self.subject = subject


def _blank(value: Optional[str]) -> bool:
    return value is None or not value.strip()


def _has_control(text: str) -> bool:
    return any(ord(c) < 0x20 or ord(c) == 0x7F for c in text)


def clean_url(value: Optional[str]) -> Optional[str]:
    """The URL as given, or None when blank; `Refused("from-url")` otherwise."""
    if value is None or _blank(value):
        return None
    url = value.strip()
    if (len(url) > URL_MAX or _has_control(url)
            or any(c.isspace() for c in url)):
        raise Refused("from-url")
    if not _SHAPE.match(url):
        raise Refused("from-url")
    return url


def clean_note(value: Optional[str]) -> Optional[str]:
    """The note, stripped, or None when blank; `Refused("from-note")` otherwise."""
    if value is None or _blank(value):
        return None
    note = value.strip()
    if len(note) > NOTE_MAX or _has_control(note):
        raise Refused("from-note")
    return note


def on_marker(marker: pathlib.Path) -> list[dict]:
    """The entries a marker holds; `[]` for a missing or unreadable marker.

    For the human faces only. A face that cannot read the record says nothing
    about where the file came from rather than failing the verb it is printing.
    """
    try:
        rows = json.loads(marker.read_text("utf-8")).get(KEY, [])
    except (OSError, ValueError, AttributeError):
        return []
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def describe(rows: Sequence[dict]) -> list[str]:
    """One prose line per entry: `source: <url>  <note>`."""
    out = []
    for r in rows:
        parts = [p for p in (r.get("url"), r.get("note")) if p]
        if parts:
            out.append("source: " + "  ".join(parts))
    return out


def merged(existing: Sequence[dict], url: Optional[str], note: Optional[str],
           *, now: Optional[datetime.datetime] = None) -> list[dict]:
    """`existing` plus one entry for `(url, note)` unless it is already there.

    Both None means "nothing to record" and returns the list unchanged, which is
    what keeps a plain re-intake from erasing the history.
    """
    rows = [dict(r) for r in existing if isinstance(r, dict)]
    if url is None and note is None:
        return rows
    if any(r.get("url") == url and r.get("note") == note for r in rows):
        return rows
    at = (now or datetime.datetime.now(datetime.timezone.utc)).isoformat(
        timespec="seconds")
    rows.append({"url": url, "note": note, "at": at})
    return rows
