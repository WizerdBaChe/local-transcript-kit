"""The disk vocabulary. Constants, never settings.

**The output root is configurable and the names below it are not** (R8, and
`CLAUDE.md`'s store table). A user may point the root anywhere; two products
writing the same folder name under a root the user happened to aim at both is
exactly the collision R8 exists to prevent, and a name that can be set can be
set to the other product's.

`校讀` names what the product does -- say where the reader should distrust the
text -- rather than where the text came from. It is deliberately not named after
a verb the other product also owns.

This module is the only place these names appear, so the R8 check has one file to
read rather than a codebase to search.
"""
from __future__ import annotations

import os
import pathlib
import re
from typing import Iterable

from ..core.ids import run_stem

# --- the root, and the one thing about it that is a setting ---------------
#: The environment variable that moves the root without a flag on every call.
ROOT_VAR = "LTM_ROOT"
#: The folder name under the home directory when nothing says otherwise.
ROOT_NAME = "TranscriptDesk"


def default_root() -> pathlib.Path:
    """`$LTM_ROOT` when set and non-blank, else `<home>/TranscriptDesk`.

    PSM card P-1 (2026-11-22). Until then this was the literal path of one
    machine's output drive -- right there, and an error on any machine without
    that drive. It is a FUNCTION, read at call time, because a value captured at
    import is right only for whoever imported first.
    """
    value = os.environ.get(ROOT_VAR, "").strip()
    if value:
        return pathlib.Path(value).expanduser()
    return pathlib.Path.home() / ROOT_NAME


CONFIG_HOME_VAR = "APPDATA"
CONFIG_DIR_NAME = "local-transcript-maker"

# --- everything below the root: constants -------------------------------
STORE = "校讀"          # the runs
TIMED = "時軸"          # SRT/VTT, anything with timecodes
PLAIN = "全文"          # plain text
ORIGIN = "_origin.json"  # the run marker
INTAKE = "_intake"      # arbitrary audio and subtitle files come in here
DROPBOX = "_錄音"       # recordings a PERSON put there; the product only reads it
CITABLE = "_citable"    # a revision someone decided is worth citing
LEXICON = "_詞表"       # correction lexicons a PERSON keeps; `glossary` reads them
EDITS = "_edits"        # the EditScript that produced a revision
FINDINGS = "_findings"  # what a detection round found
PLANS = "_plans"        # the Plan a round left behind
GRANT = "_grant.json"   # the human authorisation record (R14, INV-20)
ENGINE_LOG = "_engine.log"  # the speech child's stderr, last decode of the run

#: Path length ceiling, checked BEFORE writing (PSM section 7, property 2).
#: 260 is Windows' classic limit; long-path support may raise it, but refusing at
#: the conservative number is the right default because the alternative failure
#: is a half-written set. NEVER truncate a name to fit: a shortened name is a
#: silent overwrite.
MAX_PATH = 260

HASH_CHARS = 8
_SERIAL = re.compile(r"r(\d{3,})")


def config_home() -> pathlib.Path:
    """`%APPDATA%\\local-transcript-maker`, and nothing of the other product's."""
    base = os.environ.get(CONFIG_HOME_VAR)
    if not base:                      # not Windows, or a stripped environment
        return pathlib.Path.home() / ("." + CONFIG_DIR_NAME)
    return pathlib.Path(base) / CONFIG_DIR_NAME


def run_dir_name(filename: str, digest: str) -> str:
    """`<stem>-<hash8>`.

    The hash is of the CONTENT (`core.ids.content_hash`), so the same talk copied
    elsewhere resolves to the same run and two recordings that happen to share a
    filename do not collide.
    """
    if len(digest) < HASH_CHARS or not re.fullmatch(r"[0-9a-f]+", digest):
        raise ValueError(f"not a hex digest: {digest!r}")
    return f"{run_stem(filename)}-{digest[:HASH_CHARS]}"


def run_stem_of(run: str) -> str:
    """`<stem>-<hash8>` back to `<stem>`, the file-name half of the convention.

    Here rather than in the callers for the same reason `member_paths` takes kinds
    instead of templates: `<stem>-<hash8>` is one convention and it gets one place
    to be edited. Every verb that places a member set needs the stem, and each of
    them writing `run.rsplit("-", 1)[0]` would be four copies of a rule about
    naming -- the drift R8 erodes by.

    A stem may itself contain `-` (and CJK, and spaces): only the LAST segment is
    the hash, and it is a hash only if it looks like one. A run id that does not
    end in one is refused rather than silently returned whole, because the value
    would then be used to name files under a directory that does not match it.
    """
    stem, _, tail = run.rpartition("-")
    if not stem or len(tail) != HASH_CHARS or not re.fullmatch(r"[0-9a-f]+", tail):
        raise ValueError(
            f"not a run id: {run!r}. A run id is `<stem>-<hash8>` "
            f"(`run_dir_name`), and the stem is what names the files inside it")
    return stem


def serial(n: int) -> str:
    """`r000`, `r001`, ... A serial counts EVENTS in a run, not transcripts."""
    if n < 0:
        raise ValueError(f"a serial cannot be negative: {n}")
    return f"r{n:03d}"


def serial_number(text: str) -> int:
    m = _SERIAL.fullmatch(text)
    if not m:
        raise ValueError(f"not a serial: {text!r}")
    return int(m.group(1))


def run_dir(root: pathlib.Path, run: str) -> pathlib.Path:
    return root / STORE / run


def engine_log(root: pathlib.Path, run: str) -> pathlib.Path:
    """Where a decode's stderr goes: inside the run, never beside the audio.

    The runner's own default derives it from the audio path, which put a file in
    the person's folder on every decode (I-26). One file per run, overwritten by
    the next decode -- it is the place a FAILURE sends a reader (A-18), so the
    latest decode is the one it has to describe. A run folder holding only this
    file is not a run: listings key on `_origin.json`.
    """
    return run_dir(root, run) / ENGINE_LOG


def citable_dir(root: pathlib.Path, run: str, rev: str) -> pathlib.Path:
    """One entry per `<run>/<revision>`, not per run (R14, PIM section 9.1).

    The serial is in the directory NAME because the authorisation is per
    revision: r001 and r002 of one recording are two separate grants, and a
    per-run folder would quietly turn one grant into a standing one.
    """
    return root / CITABLE / f"{run}.{rev}"


def intake_dir(root: pathlib.Path) -> pathlib.Path:
    return root / INTAKE


def lexicon_dir(root: pathlib.Path) -> pathlib.Path:
    """The correction lexicons `glossary` is bounded by (INV-7), one JSON file each.

    Under the output root by user ruling (2026-10-30, 「輸出根目錄 _詞表/」): a
    lexicon is the user's data about their own field -- speakers' names, a talk's
    slide terms -- so it follows the transcripts, not the tool. A person writes
    these files; the product only reads them, and never creates the folder.
    """
    return root / LEXICON


def lexicon_files(root: pathlib.Path) -> list[pathlib.Path]:
    """Every `*.json` directly in `_詞表/`, by name. No folder, no files."""
    d = lexicon_dir(root)
    return sorted(p for p in d.glob("*.json") if p.is_file()) if d.is_dir() else []


#: Under `_詞表/`: character-frequency CSVs the user put there (NAER 字頻; all
#: rights reserved, so the product reads them and never ships one).
CHAR_FREQ = "_字頻"


def char_freq_files(root: pathlib.Path) -> list[pathlib.Path]:
    """Every `*.csv` directly in `_詞表/_字頻/`, by name."""
    d = lexicon_dir(root) / CHAR_FREQ
    return sorted(p for p in d.glob("*.csv") if p.is_file()) if d.is_dir() else []


def dropbox(root: pathlib.Path) -> pathlib.Path:
    """Where a person puts recordings so the page can offer them.

    **Not `_intake`, and the difference is the direction.** `_intake` is the
    product's own ledger -- a pointer file per recording someone already handed
    it, and it never holds audio. This is the opposite: a folder a PERSON fills,
    which the product only ever reads, and which exists because a double-click
    launcher has to be able to offer something on a machine where nobody has
    typed a path yet.

    It sits UNDER the output root rather than beside it because R8's property
    says a shipping file may name no path outside this product's own tree and
    its own output root. A launcher that invented a folder somewhere else would
    be that rule's first exception, and the first exception is how R8 erodes.

    Nothing creates it but the launcher, and only when it is about to be used:
    an empty folder that appears on a machine where nobody asked for one is a
    product making a decision on disk without being asked.
    """
    return root / DROPBOX


MEMBER_KINDS = ("srt", "vtt", "txt", "edit", "finding", "plan")

#: The label `langmap` produces when no label had majority support from two
#: independent signals (BR-4). It is a MEASUREMENT -- 「這段分不出來」 -- and it is
#: the one value that must never reach a filename.
UNDETERMINED = "undetermined"


def filename_language(languages: Iterable[str]) -> str | None:
    """The one language this revision's artifacts may claim, or `None`.

    **A filename is a claim, and this product's entire position is that it does
    not make claims it did not measure** (PIM v1.0 §9.1). Until 2026-09-11 `lang`
    was a default parameter spelled `"zh"` that no caller ever passed, so every
    file this product had ever written was called `.zh.` -- including a bilingual
    discussion whose own record says `language_detected: "en"` and whose stretches
    were `undetermined` at agreement 0.311 (measured 2026-09-10, I-18). On the
    Chinese recordings it happened to be right, which is precisely why nobody
    noticed.

    A revision's artifacts are ONE file each, so they may carry a language only
    when there is one language to carry: every stretch names the same one. Any
    `undetermined`, a mix of two named languages, or nothing measured at all, and
    the segment disappears **together with its separator** -- `<stem>.r000.srt`.

    Silence is the honest form of "I could not tell". A `.mixed.` or `.und.` code
    would be a new claim in the same slot, and a reader who has to learn a
    vocabulary to discover that nothing was determined has been told twice.
    """
    labels = set(languages)
    if len(labels) == 1 and UNDETERMINED not in labels:
        return next(iter(labels))
    return None


def member_paths(root: pathlib.Path, run: str, rev: str, stem: str,
                 members: Iterable[str],
                 lang: str | None = None) -> dict[str, pathlib.Path]:
    """Resolve a set of member KINDS to absolute paths.

    The member set is decided by the ROUND, not fixed (PSM section 7): a decode
    round places transcript plus finding plus plan, an edit round places
    transcript plus the script, and a declined round places finding plus plan and
    no transcript at all -- "I looked and decided not to change it" is a
    conclusion that has to land on disk, or it looks like "I never ran".

    The caller passes kinds, not filename templates. PSM section 7 illustrates
    the call with templates (`"<stem>.{r}.zh.srt"`), and putting them in callers
    would put the naming convention in as many places as there are callers --
    which is the drift R8 is most likely to erode by. The convention lives here,
    with the vocabulary it belongs to.
    """
    d = run_dir(root, run)
    # The separator goes with the segment (§9.1): `.zh.srt` or `.srt`, never
    # `..srt` and never a placeholder code standing in for "not measured".
    tag = f".{lang}" if lang else ""
    slots = {
        "srt": d / TIMED / f"{stem}.{rev}{tag}.srt",
        "vtt": d / TIMED / f"{stem}.{rev}{tag}.vtt",
        "txt": d / PLAIN / f"{stem}.{rev}{tag}.txt",
        "edit": d / EDITS / f"{rev}.json",
        "finding": d / FINDINGS / f"{rev}.json",
        "plan": d / PLANS / f"{rev}.json",
    }
    kinds = tuple(members)
    unknown = set(kinds) - set(slots)
    if unknown:
        raise ValueError(f"unknown member kinds: {sorted(unknown)}")
    return {kind: slots[kind] for kind in kinds}


#: `<stem>.<rev>.<ext>` or `<stem>.<rev>.<lang>.<ext>`, and nothing else. A glob
#: (`<stem>.<rev>*`) would be shorter and wrong: serials widen past three digits
#: at r1000, and `r100*` matches `r1000`. The optional group is the language
#: segment, which is a code and therefore has no dot in it.
def _member_pattern(stem: str, rev: str, ext: str) -> re.Pattern[str]:
    return re.compile(
        re.escape(f"{stem}.{rev}") + r"(?:\.[A-Za-z][A-Za-z-]{0,7})?"
        + re.escape(f".{ext}") + r"\Z")


def members_on_disk(root: pathlib.Path, run: str, rev: str, stem: str,
                    members: Iterable[str]) -> dict[str, pathlib.Path]:
    """The member files a revision ACTUALLY left, whatever they ended up called.

    **Reconstructing the name is what broke** (I-18): every reader that rebuilt
    `<stem>.<rev>.zh.<ext>` from a default parameter agreed with every writer that
    did the same, and both were wrong together. Now that the language segment is
    written only when it was measured, the same revision can be `.zh.srt` or
    `.srt` depending on what its own stretches said -- and a reader that guesses
    would find a revision from before this change and miss one from after it, or
    the reverse. So the reader reads the directory.

    Only kinds whose name carries a language segment need this; `edit`, `finding`
    and `plan` are `<rev>.json` and are returned from `member_paths` unchanged.
    """
    d = run_dir(root, run)
    fixed = member_paths(root, run, rev, stem, members)
    out: dict[str, pathlib.Path] = {}
    for kind, path in fixed.items():
        if kind not in ("srt", "vtt", "txt"):
            out[kind] = path
            continue
        pattern = _member_pattern(stem, rev, kind)
        folder = path.parent
        hits = sorted(p for p in folder.glob(f"*.{kind}")
                      if pattern.fullmatch(p.name)) if folder.is_dir() else []
        if hits:
            out[kind] = hits[0]
    return out
