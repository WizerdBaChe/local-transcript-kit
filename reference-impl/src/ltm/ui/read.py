"""The one place a round's own record is read for a person to look at.

**Text is what it was built for and `stretches_of` is why the first line of this
docstring now says 「record」 rather than 「text」** -- see that function: the
trust table and the words come out of the same file, for the same revision,
because `docs/gui-contract.json` says the human face has no second place to look.


**Why this is a module and not three lines inside `cli.py`.** INV-12 keeps
transcript text out of every `agentapi` response, which is the right rule and
which left the product unable to answer the first question a reader asks: 「哪一
句？」 Measured on three real recordings, `plan` printed
`[engine-loop] engine-loop:1644-1649 in cue 0-1  cues=6` and not one character of
what was actually said; to see the repeated 「好」 you had to open
`全文/….txt` and count to line 1645 (`docs/first-run-2026-09-10.md` §4).

So the text has to reach a human somehow, and the danger is that it reaches one
by being sprinkled through whatever surface wants it -- the CLI today, the
browser page tomorrow, each with its own idea of where the text lives. Then INV-12
is a rule about one module while the product has three text readers.

`docs/gui-contract.json` therefore fixes it: **one implementation, here, not in
the verb table, and no surface assembles text from `全文/` or `時軸/` itself.**

**It reads the plan record, not the rendered transcript**, and that is the
`joinRule` rather than a preference:

* the string a finding names is `cues[cue_id].text[char_start:char_end]`, in the
  source coordinates INV-5 fixes -- the plan record carries the exact `Cue` text
  the round was measured against;
* `全文/*.txt` is a RENDERING, one line per SURVIVING cue. Locating anything by
  offset into it works only until someone accepts a deletion, after which the
  line numbers and the cue ids disagree and nothing says so. A locator that
  expires the first time the user makes a decision is the last thing a product
  about 「哪裡不該相信」 should hand anybody.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from typing import Mapping, Optional

from ..store import layout

#: How much of the surrounding cue to show on each side of a span. Small on
#: purpose: this is a locator, not the transcript. A reader who wants the whole
#: thing opens the file, and the path is already printed.
CONTEXT = 12

#: What replaces the part of a cue that was trimmed away for width.
ELLIPSIS = "…"


class ReadError(ValueError):
    """The round's record is not where it should be, or does not describe itself."""


@dataclass(frozen=True)
class Excerpt:
    """One span, with just enough around it to be recognised.

    `before`/`span`/`after` stay separate rather than pre-joined because the
    caller decides the emphasis -- a terminal draws it one way and a browser page
    another, and a module that returned one formatted string would be making that
    decision for both.
    """

    cue_id: str
    start: float
    end: float
    before: str
    span: str
    after: str
    truncated_left: bool
    truncated_right: bool

    @property
    def one_line(self) -> str:
        """The whole excerpt with the span marked, newlines flattened.

        A cue can contain a line break and a terminal row cannot, so the flatten
        happens HERE -- once -- rather than in each caller that forgets.
        """
        left = (ELLIPSIS if self.truncated_left else "") + self.before
        right = self.after + (ELLIPSIS if self.truncated_right else "")
        return f"{left}[{self.span}]{right}".replace("\n", " ")


def plan_path(root: pathlib.Path, run: str, rev: str) -> pathlib.Path:
    return layout.run_dir(root, run) / layout.PLANS / f"{rev}.json"


def _record(root: pathlib.Path, run: str, rev: str) -> Mapping[str, object]:
    path = plan_path(root, run, rev)
    if not path.is_file():
        raise ReadError(f"no plan record for {run} {rev}: {path}")
    return json.loads(path.read_text("utf-8"))


def cues_of(root: pathlib.Path, run: str, rev: str) -> Mapping[str, dict]:
    """`cue_id` -> the cue row the round was measured against.

    Keyed by id and not by position: `accept` removes text from cues but never
    removes a cue, so an index would be stable today and quietly wrong the first
    time that stops being true.
    """
    path = plan_path(root, run, rev)
    data = _record(root, run, rev)
    try:
        rows = data["recording"]["cues"]
    except (KeyError, TypeError) as e:
        raise ReadError(
            f"{path} has no recording to read text from. A plan record written "
            f"by this product always carries the cues its findings were measured "
            f"against (INV-5); one that does not is from another tool or is "
            f"truncated") from e
    return {row["id"]: row for row in rows}


def stretches_of(root: pathlib.Path, run: str, rev: str) -> tuple[dict, ...]:
    """The language stretches THIS round was measured against.

    **Why this is here and not taken from the `show` verb.** `show` reads
    `_origin.json`'s per-revision decode provenance, so it can answer only for a
    round that was a decode. Measured on the real store on 2026-09-11: the run a
    reader is most likely to open is at `r012`, a **detect** round, and `show`
    returns an empty stretch table for it -- so 「這份錄音整體可不可信」 came back
    blank on exactly the revision whose findings the reader was looking at. Two
    sources for one fact, and the one the page happened to ask was empty.

    The round's own plan record is not a second source, it is THE source:
    `docs/gui-contract.json`'s `readSource` says all four layers are in that one
    file and 「the human face has no second place to look」, and `verbs.
    _recording_json` writes the stretches into every plan record for INV-6's
    reason. So the trust table and the findings come out of the same record, for
    the same revision, and cannot disagree with each other.

    This widens `read.py` from 「the one text reader」 to 「the one reader of a
    round's record for a person」. That is the widening the contract already
    describes; the alternative -- the page parsing `_plans/*.json` itself -- is
    the one the contract forbids by name.
    """
    path = plan_path(root, run, rev)
    data = _record(root, run, rev)
    try:
        rows = data["recording"]["stretches"]
    except (KeyError, TypeError) as e:
        raise ReadError(
            f"{path} carries no language stretches. Every plan record this "
            f"product writes carries all four layers (PSM §7, A-11); one that "
            f"does not is from another tool or is truncated") from e
    return tuple(rows)


def report_of(root: pathlib.Path, run: str, rev: str) -> Mapping[str, object]:
    """The engine's report for THIS round: `vad_kept`, `vad_enabled` and the rest.

    Here for I-22 and for `stretches_of`'s reason: a round with no words has to
    say WHY, the answer rests on VAD's two fields, and they are in the same record
    as everything else the page shows. An empty mapping from a record that has no
    report is not a guess -- every consumer reads a missing field as E-2's
    `not-reported` branch, which is what it is.
    """
    data = _record(root, run, rev)
    try:
        report = data["recording"]["report"]
    except (KeyError, TypeError):
        return {}
    return report if isinstance(report, Mapping) else {}


def excerpt(cues: Mapping[str, dict], cue_id: str,
            char_start: float, char_end: float,
            context: int = CONTEXT) -> Optional[Excerpt]:
    """The actual words at a span, or `None` when the round cannot show them.

    **`None` rather than a placeholder string.** A caller has to be able to tell
    「the text is X」 from 「this record cannot say」, and a sentinel string that
    reads like transcript makes those two the same value -- the collapse this
    product exists to refuse. The caller prints its own sentence for `None`.
    """
    row = cues.get(cue_id)
    if row is None:
        return None
    text = row.get("text")
    if not isinstance(text, str):
        return None
    s, e = int(char_start), int(char_end)
    s = max(0, min(s, len(text)))
    e = max(s, min(e, len(text)))
    left, right = max(0, s - context), min(len(text), e + context)
    return Excerpt(
        cue_id=cue_id, start=float(row.get("start", 0.0)),
        end=float(row.get("end", 0.0)),
        before=text[left:s], span=text[s:e], after=text[e:right],
        truncated_left=left > 0, truncated_right=right < len(text))


def clock(seconds: float) -> str:
    """`h:mm:ss` for a person who is about to go and listen to that moment.

    Seconds-since-start is what the record holds and what a machine wants; a
    reader looking for 3925.0 in a 65-minute recording wants `1:05:25`.
    """
    total = int(seconds)
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
