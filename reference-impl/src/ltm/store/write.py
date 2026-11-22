"""The run record (`_origin.json`) and the `cite` verb.

Two different kinds of write live here, and the difference is the point:

* **Members are create-only** (`place.py`). A transcript is content; content is
  never overwritten.
* **`_origin.json` is a record and IS rewritten**, atomically, every time the run
  learns something. It is the only file in the store with that property, which is
  why it is the only file whose write lives in this module.

`_origin.json` holds the high-water serial (PSM section 7, property 3), the
decode provenance a contract input may not carry (`EngineReport` has exactly four
fields), the `condition_on_previous_text` audit trail E-1 requires, and the
citation nominations R14 asks for.
"""
from __future__ import annotations

import datetime
import json
import pathlib
import shutil
from typing import Any, Iterable, Mapping

from . import layout, place

#: Bumped to /2 when `decode` became a mapping KEYED BY REVISION (`record_decode`).
#: The version exists so the change is loud: a /1 record's `decode` is one flat
#: mapping, and `decode_record` reading it would return `{}` for every revision --
#: a `show` that prints no stretches and no counts and reports no error. `read_origin`
#: refuses the old shape instead, which is the difference between a migration
#: somebody has to do and a wrong answer nobody notices. Milestone 1 has no
#: migration: there is one record on this machine and it is re-made by re-decoding.
SCHEMA = "ltm/origin/2"

#: The four fields R14 requires of an authorisation record, in the order a
#: failure message lists them. `method`'s domain is closed; an unknown value is
#: as much a defect as a missing field, because the point of the enumeration is
#: that a reader can tell the three routes apart afterwards.
GRANT_FIELDS = ("grantedBy", "grantedAt", "scope", "method")
GRANT_METHODS = ("ui-dialog", "cli-flag", "agent-relayed")

#: Which members a citation copies. `_citable/`'s layout block draws 時軸/ and
#: 全文/, and point 2 argues for copies: "worth citing" means the text will not
#: change under the citation.
CITABLE_KINDS = ("srt", "vtt", "txt")


class StoreError(RuntimeError):
    """A store operation was refused, and the message names what was missing."""


# --- what a member file looks like ---------------------------------------
#
# The renderers live beside `place_round` because they produce the bytes it
# places, and because a caller that rolled its own SRT would be a second dialect
# of this product's own output format -- the two faces of BR-14 must write
# identical files. PSM section 2's file list is a contract, so they are here
# rather than in a module the card does not name (the same reasoning as v4/A-3).


def clock(seconds: float, comma: bool = True) -> str:
    """`HH:MM:SS,mmm`, or `HH:MM:SS.mmm` for WebVTT.

    Truncated to milliseconds rather than rounded: rounding up can push a cue's
    end past the next cue's start and produce an overlap that no player agrees
    about, and this product's whole promise is that the reader can trust the
    positions it is shown.
    """
    if seconds < 0:
        raise StoreError(f"a timecode cannot be negative: {seconds}")
    ms = int(seconds * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{',' if comma else '.'}{ms:03d}"


def _surviving(cues: Iterable[Any], texts: Mapping[str, str] | None):
    """The cues a revision still has text for, in time order.

    A cue whose text is empty after an `EditScript` is OMITTED from the timed
    file -- that is what a deletion proposal means. So the sequence numbers below
    are POSITIONAL and do not match `Cue.id`; nothing needs them to. Every finding
    and every op names the cue id, which is stable across revisions, and that is
    the identifier the reader is ever asked to follow.
    """
    for cue in cues:
        text = (texts.get(cue.id, cue.text) if texts is not None else cue.text)
        if text.strip():
            yield cue, text


def render_srt(cues: Iterable[Any], texts: Mapping[str, str] | None = None) -> str:
    out = []
    for n, (cue, text) in enumerate(_surviving(cues, texts), start=1):
        out.append(f"{n}\n{clock(cue.start)} --> {clock(cue.end)}\n"
                   f"{text.strip()}\n")
    return "\n".join(out)


def render_vtt(cues: Iterable[Any], texts: Mapping[str, str] | None = None) -> str:
    out = ["WEBVTT\n"]
    for _n, (cue, text) in enumerate(_surviving(cues, texts), start=1):
        out.append(f"{clock(cue.start, comma=False)} --> "
                   f"{clock(cue.end, comma=False)}\n{text.strip()}\n")
    return "\n".join(out)


def render_txt(cues: Iterable[Any], texts: Mapping[str, str] | None = None) -> str:
    """One cue per line, no timecodes.

    Not reflowed into paragraphs: a paragraph break is a judgement about the
    speech, and this file is the one place the product hands back the text with
    nothing of its own added. Line n of this file is the nth surviving cue, which
    makes it diffable against the SRT beside it.
    """
    return "".join(f"{text.strip()}\n" for _cue, text in _surviving(cues, texts))


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")


# --- the run record ------------------------------------------------------

def origin_path(root: pathlib.Path, run: str) -> pathlib.Path:
    return layout.run_dir(root, run) / layout.ORIGIN


def new_origin(run: str, filename: str, digest: str, size_bytes: int) -> dict:
    """A run record for a recording nothing has been placed for yet.

    `high_water` starts at -1 rather than 0 because 0 is a serial that has been
    issued (`r000`), and the difference between "no round has run" and "one round
    has run" is exactly the distinction INV-17 keeps asking for.
    """
    return {
        "schema": SCHEMA,
        "run": run,
        "created_at": _now(),
        "source": {"filename": filename,
                   "content_sha256": digest,
                   "bytes": size_bytes},
        "high_water": -1,
        "decode": {},
        "revisions": [],
        "citations": [],
    }


def read_origin(root: pathlib.Path, run: str) -> dict:
    p = origin_path(root, run)
    if not p.is_file():
        raise StoreError(f"this run has no record: {p} is missing. A run "
                         f"directory without {layout.ORIGIN} is not a run.")
    with open(p, encoding="utf-8") as f:
        record = json.load(f)
    if record.get("schema") != SCHEMA:
        raise StoreError(f"unknown record schema {record.get('schema')!r} in {p}; "
                         f"this build reads {SCHEMA!r} only")
    return record


def write_origin(root: pathlib.Path, run: str, record: Mapping[str, Any]) -> pathlib.Path:
    """Rewrite the record atomically. The high-water mark may never fall.

    The refusal is here rather than in the callers because a monotone counter
    guarded at each call site is a counter with as many guards as callers, and
    P-73's failure was precisely that a number nobody watched went backwards.
    """
    p = origin_path(root, run)
    if p.is_file():
        old = read_origin(root, run)
        if record["high_water"] < old["high_water"]:
            raise StoreError(
                f"the high-water mark may only rise: {old['high_water']} -> "
                f"{record['high_water']} in {p}. A serial that falls gets "
                f"re-issued to different content, and nothing downstream can see it."
            )
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".partial")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(record, f, ensure_ascii=False, indent=2, sort_keys=False)
        f.write("\n")
    tmp.replace(p)          # atomic on one volume; a reader sees old or new
    return p


def next_serial(root: pathlib.Path, run: str) -> str:
    """`max(record high water, highest on disk) + 1`. Both sources, always."""
    record = read_origin(root, run)
    return place.next_serial(record["high_water"],
                             place.serials_on_disk(root, run))


def place_round(root: pathlib.Path, run: str, stem: str,
                payloads: Mapping[str, str | bytes],
                source_rev: str | None = None,
                lang: str | None = None,
                face: str | None = None) -> tuple[str, dict[str, pathlib.Path]]:
    """One round: allocate a serial, place its member set, then raise the mark.

    Order is the recovery story (see `place.place_set`): files first, record
    last. A crash in between leaves files no record claims, the mark has not
    risen, and the next round proposes the same serial and is refused with the
    collision named -- loud and repairable. The reverse order leaves a record
    pointing at nothing, which no consumer can detect.

    A round with no transcript in it still gets here: PSM section 7 case (3)
    places only `finding` and `plan`, and **consumes a serial anyway**, because a
    serial counts events in the run rather than transcripts produced.

    `face` says which surface asked for the round -- `"human"`, `"agent"`, or
    absent. **Absent is a third value and is not read as `"human"`**: rounds
    written before 2026-09-09 predate the field, and the honest answer for them is
    "not recorded", not a default that happens to flatter the record. That is also
    why the schema is not bumped for this: an unknown key is unambiguous, whereas
    the `/1` -> `/2` bump was forced by a change that would have been MISREAD.
    """
    rev = next_serial(root, run)
    paths = place.place_set(root, run, rev, stem, payloads, lang)
    record = read_origin(root, run)
    record["high_water"] = layout.serial_number(rev)
    entry = {
        "serial": rev,
        "source": source_rev,
        "members": sorted(payloads.keys()),
        "created_at": _now(),
    }
    if face is not None:
        entry["face"] = face
    record["revisions"].append(entry)
    write_origin(root, run, record)
    return rev, paths


def record_decode(root: pathlib.Path, run: str, *, revision: str,
                  **provenance: Any) -> dict:
    """Decode provenance, **per revision**, including E-1's audit trail.

    E-1: `condition_on_previous_text` is passed explicitly and defaults to
    `False`; **if it is turned on, the record says so.** "Never passed" and
    "passed False" must stay distinguishable -- the first is a defect, the second
    a decision -- so an absent key here is not the same as `false`.

    **Keyed by revision, and that is a step-7 repair.** The first version merged
    every decode's provenance into one mapping, so a second decode of the same
    recording -- the ordinary way to compare `--no-vad` against the default --
    overwrote the first one's settings while both revisions stayed on disk. A
    revision's verdicts hang on the version they were measured on (§7 case 2), and
    that includes the settings they were measured under: a plan whose provenance
    has been replaced by a later round's is a record that lies about itself.
    """
    record = read_origin(root, run)
    if not isinstance(record["decode"], dict):
        raise StoreError(f"{layout.ORIGIN}'s decode record is not a mapping")
    record["decode"][revision] = dict(provenance)
    write_origin(root, run, record)
    return record


def decode_record(record: Mapping[str, Any], revision: str) -> dict:
    """One revision's decode provenance, or `{}` if that round was not a decode.

    An edit or a declined round has none, and `{}` is the honest answer: it was
    never decoded, so there is nothing to say about how. That is different from a
    decode whose provenance is missing, which cannot happen -- `record_decode` runs
    in the same call as `place_round`.
    """
    return dict(record.get("decode", {}).get(revision, {}))


# --- the cite verb -------------------------------------------------------

def check_grant(grant: Mapping[str, Any], run: str, rev: str) -> None:
    """The checkable half of INV-20, and deliberately nothing more.

    What this can check: the record exists, its fields are complete, its `scope`
    names this exact revision. What it CANNOT check: whether `method` is honest.
    R6 says this product never logs in and holds no credentials, so it has no way
    to tell "a human clicked confirm" from "an agent wrote `ui-dialog`". The
    mechanism is an **audit trail, not a gate**: one dishonest relay gets one
    revision and never a standing pass, every entry is timestamped and therefore
    countable afterwards, and a `method` the person denies is a contradiction
    that can be put to them.
    """
    if not isinstance(grant, Mapping):
        raise StoreError("cite requires an authorisation record (R14); none was "
                         "passed. Missing fields: " + ", ".join(GRANT_FIELDS))
    missing = [f for f in GRANT_FIELDS
               if not str(grant.get(f) or "").strip()]
    if missing:
        raise StoreError(
            "cite refused: the authorisation record is missing "
            + ", ".join(missing)
            + f". Nothing was written to {layout.CITABLE}/ -- silence would make "
              f"'not authorised' and 'not cited' look identical on disk."
        )
    if grant["method"] not in GRANT_METHODS:
        raise StoreError(
            f"cite refused: method {grant['method']!r} is not one of "
            f"{list(GRANT_METHODS)}. The enumeration is what lets a later reader "
            f"tell the three routes apart."
        )
    want = f"{run}/{rev}"
    if grant["scope"] != want:
        raise StoreError(
            f"cite refused: scope {grant['scope']!r} does not name the revision "
            f"being cited ({want!r}). A grant covers a single revision -- never a "
            f"run, never standing."
        )


def cite(root: pathlib.Path, run: str, rev: str, stem: str,
         grant: Mapping[str, Any], lang: str | None = None,
         kinds: Iterable[str] = CITABLE_KINDS) -> pathlib.Path:
    """Copy a revision's text into `_citable/<run>.<rev>/` beside its grant.

    Copies, not links (PSM section 7, point 2): "worth citing" means the text
    will not change under the citation, and a link back into the run folder
    changes with the next edit. The originals stay where they are -- citing is
    not a move, so the serial chain is not broken in two places -- and
    `_origin.json` records who nominated which revision, and when.

    `_grant.json` lands in the same directory as the content it authorises (point
    3): separating them guarantees an eventual entry with content and no record.
    """
    check_grant(grant, run, rev)

    # Found, not reconstructed (I-18). `lang` stays in the signature because a
    # caller that knows the language may still say so, but the lookup no longer
    # depends on the reader and the writer having guessed the same default.
    sources = (layout.member_paths(root, run, rev, stem, kinds, lang) if lang
               else layout.members_on_disk(root, run, rev, stem, kinds))
    present = {k: p for k, p in sources.items() if p.is_file()}
    if not present:
        raise StoreError(
            f"cite refused: revision {rev} of {run} has no text to cite "
            f"(looked for {', '.join(sorted(kinds))}). A declined round produces "
            f"a conclusion, not a transcript, and there is nothing to quote."
        )

    dest = layout.citable_dir(root, run, rev)
    if dest.exists():
        raise StoreError(
            f"cite refused: {dest} already exists. One entry per "
            f"<run>/<revision>; re-citing would overwrite the grant that is "
            f"already on record."
        )

    grant_file = dest / layout.GRANT
    copied: list[pathlib.Path] = []
    try:
        for kind, src in present.items():
            target = dest / src.parent.name / src.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            copied.append(target)
        payload = dict(grant)
        payload["citedAt"] = _now()
        payload["members"] = sorted(present)
        with open(grant_file, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        copied.append(grant_file)
    except Exception:
        for p in copied:
            p.unlink(missing_ok=True)
        shutil.rmtree(dest, ignore_errors=True)
        raise

    record = read_origin(root, run)
    record["citations"].append({
        "scope": grant["scope"],
        "grantedBy": grant["grantedBy"],
        "grantedAt": grant["grantedAt"],
        "method": grant["method"],
        "citedAt": payload["citedAt"],
    })
    write_origin(root, run, record)
    return dest
