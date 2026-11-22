"""The correction lexicon: the declared list `glossary` is bounded by (INV-7).

One entry is one CORRECT form a transcript of this field should contain, the wrong
forms it is already known to come out as, and where the entry came from. A lexicon
is a JSON file; several files are merged in the order given (a domain list, then a
per-talk list of slide terms and names), and a later file may add wrong forms to a
term an earlier one declared.

    {
      "format": "ltm-lexicon/1",
      "name": "photonics-zh",
      "entries": [
        {"term": "光纖", "wrong": ["光鮮"], "keep": ["送光線"], "source": "domain"},
        {"term": "COMSOL", "wrong": ["Council"], "source": "slide"},
        {"term": "Kerr", "case": true, "sound": false, "source": "slide"}
      ]
    }

- `term` (required): the correct form.
- `wrong`: forms known to stand for it. A hit is a finding that names the entry.
- `keep`: contexts in which a wrong form or a sound-alike is RIGHT (「送光線」 is
  light, not fibre). They shield what is inside them (term-match block terms).
- `case`: compare Latin letters case-sensitively. Default false.
- `sound`: also look for sound-alikes of `term` (CJK homophones and near-homophones,
  Latin words with the same consonant skeleton). Default true.
- `source`: one of `SOURCES`. Where the entry came from is what a reader weighs
  when deciding whether to believe it, so it is a closed vocabulary, not prose.
- `note`: free text for the human who maintains the file. Never read by code and
  never copied into a plan or a response (INV-12).

**The lexicon is the upper bound, not a hint.** An empty lexicon finds nothing,
and nothing here falls back to a built-in list: a built-in list is an undeclared
rule wearing a user's clothes (INV-7, `detectors.Declared`).
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

FORMAT = "ltm-lexicon/1"

#: Where an entry came from. `slide`: on the talk's own slides. `domain`: a field
#: list written before the transcript was read. `human`: a person corrected a
#: transcript and the correction was kept. `reference`: a published term list.
#: `advisor`: a model the user consented to picked the correction (appended last
#: so the evidence codes of the first four never move).
SOURCES = ("slide", "domain", "human", "reference", "advisor")

_KEYS = {"term", "wrong", "keep", "case", "sound", "source", "note"}


class LexiconError(ValueError):
    """A lexicon file this build cannot read, said with the entry that broke it."""


@dataclass(frozen=True, slots=True)
class Entry:
    term: str
    wrong: tuple[str, ...] = ()
    keep: tuple[str, ...] = ()
    case: bool = False
    sound: bool = True
    source: str = "domain"


@dataclass(frozen=True, slots=True)
class Lexicon:
    entries: tuple[Entry, ...] = ()
    names: tuple[str, ...] = field(default=())

    def __len__(self) -> int:
        return len(self.entries)

    @property
    def keep(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(k for e in self.entries for k in e.keep))


def _strings(value: object, where: str, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise LexiconError(f"{where}: {key!r} must be a list of strings")
    return tuple(v.strip() for v in value if v.strip())


def parse(data: Mapping[str, object], where: str = "<lexicon>") -> Lexicon:
    if data.get("format") != FORMAT:
        raise LexiconError(f"{where}: 'format' must be {FORMAT!r}, "
                           f"not {data.get('format')!r}")
    raw = data.get("entries")
    if not isinstance(raw, list):
        raise LexiconError(f"{where}: 'entries' must be a list")
    out: list[Entry] = []
    for i, row in enumerate(raw):
        at = f"{where} entry {i}"
        if not isinstance(row, dict):
            raise LexiconError(f"{at}: an entry is an object")
        stray = set(row) - _KEYS
        if stray:
            raise LexiconError(f"{at}: unknown keys {sorted(stray)}")
        term = row.get("term")
        if not isinstance(term, str) or not term.strip():
            raise LexiconError(f"{at}: 'term' is required")
        source = row.get("source", "domain")
        if source not in SOURCES:
            raise LexiconError(f"{at}: 'source' must be one of {SOURCES}")
        for flag in ("case", "sound"):
            if flag in row and not isinstance(row[flag], bool):
                raise LexiconError(f"{at}: {flag!r} must be true or false")
        wrong = _strings(row.get("wrong"), at, "wrong")
        if term.strip() in wrong:
            raise LexiconError(f"{at}: {term!r} is listed as its own wrong form")
        out.append(Entry(term=term.strip(), wrong=wrong,
                         keep=_strings(row.get("keep"), at, "keep"),
                         case=row.get("case", False), sound=row.get("sound", True),
                         source=source))
    name = data.get("name")
    return Lexicon(tuple(out), (name,) if isinstance(name, str) else ())


def load(path: str | pathlib.Path) -> Lexicon:
    p = pathlib.Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise LexiconError(f"{p}: cannot read a lexicon: {e}") from e
    if not isinstance(data, dict):
        raise LexiconError(f"{p}: a lexicon is a JSON object")
    return parse(data, str(p))


def merge(lexicons: Iterable[Lexicon]) -> Lexicon:
    """One entry per (term, case); the first occurrence wins `sound` and `source`,
    wrong forms and keep contexts accumulate. A per-talk file can therefore teach
    a domain term a new wrong form without restating it.

    `case` is part of the key because it governs how the WRONG forms match:
    「Language」 (case-sensitive) and 「Line with」 (not) both stand for
    `linewidth`, and folding them into one entry would let the first file's flag
    decide for the other's forms -- measured: it turned 「Language」 insensitive
    and flagged seven lower-case 「language」 in another transcript."""
    order: list[tuple[str, bool]] = []
    by: dict[tuple[str, bool], Entry] = {}
    names: list[str] = []
    for lex in lexicons:
        names.extend(lex.names)
        for e in lex.entries:
            key = (e.term, e.case)
            if key not in by:
                order.append(key)
                by[key] = e
                continue
            old = by[key]
            by[key] = Entry(old.term,
                               tuple(dict.fromkeys(old.wrong + e.wrong)),
                               tuple(dict.fromkeys(old.keep + e.keep)),
                               old.case, old.sound, old.source)
    return Lexicon(tuple(by[k] for k in order), tuple(names))


def load_all(paths: Sequence[str | pathlib.Path]) -> Lexicon:
    return merge(load(p) for p in paths)


def load_freq(paths: Sequence[str | pathlib.Path]) -> dict[str, int]:
    """Character -> count, summed over CSV files whose header names a `字` column
    and a `字頻` column (the NAER 常用語詞調查 layout; counts may carry thousands
    separators). The files are the USER's: NAER reserves all rights, so this
    product reads them from `<root>/_詞表/_字頻/` and never ships one.
    A file without both columns is refused, naming the file."""
    import csv
    out: dict[str, int] = {}
    for p in paths:
        p = pathlib.Path(p)
        with p.open(encoding="utf-8-sig", newline="") as fh:
            rows = csv.reader(fh)
            head = [h.strip() for h in next(rows, [])]
            if "字" not in head or "字頻" not in head:
                raise LexiconError(f"{p}: needs columns 字 and 字頻, has {head}")
            ci, fi = head.index("字"), head.index("字頻")
            for row in rows:
                if len(row) <= max(ci, fi) or len(row[ci].strip()) != 1:
                    continue
                n = row[fi].replace(",", "").strip()
                if n.isdigit():
                    ch = row[ci].strip()
                    out[ch] = out.get(ch, 0) + int(n)
    return out
