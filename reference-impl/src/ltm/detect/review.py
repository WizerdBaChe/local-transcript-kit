"""The consent-gated LLM pick over `glossary` candidates -- everything EXCEPT the LLM.

User ruling 2026-10-30: LLM correction is off by default; an agent may run it only
after asking the user and getting an explicit yes, then with Claude Sonnet at
medium effort, after the frequency pass. **This product holds no LLM client and no
credential** (R6): the calling Claude session dispatches the model. This module is
the part that must be the same every time, so it lives here, versioned and tested:

- `pack(plan)` -- one item per dormant candidate in a plan: the cue, its two
  neighbours, the span, the lexicon's proposed text, and the evidence. Also the
  character count, which the consent question must state (the text LEAVES the
  machine -- R1 is superseded for this step only, by that ruling).
- `prompt(pack)` -- the instruction, `PROMPT_VERSION` stamped.
- `picks(reply, pack)` -- the model's answer parsed into candidate NUMBERS. Any
  number not in the pack, any non-JSON, any extra key is refused: the model picks
  from a closed set and never writes transcript text. That is what differs from the
  pre-milestone-1 Advisor trials (PIM §5.4.6): those asked for a rewrite.

The numbers go to `accept --candidate N ... --picked-by advisor:<model>@<version>`,
which writes a NEW revision; nothing is edited in place.

usage:
    python -m ltm.detect.review pack <plan.json> <out.json>
    python -m ltm.detect.review prompt <pack.json>
    python -m ltm.detect.review picks <pack.json> <reply.txt>
    python -m ltm.detect.review learn <run>/_edits/<rev>.json <lexicon.json>
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from typing import Any, Mapping

FORMAT = "ltm-review-pack/1"
PROMPT_VERSION = "glossary-pick-1"

#: Evidence codes, named for the model (the plan stores numbers).
_KIND = {1: "declared-wrong", 2: "homophone", 3: "near-homophone", 4: "english-sound-alike"}
_SCRIPT = {1: "zh-only", 2: "mixed", 3: "en-only"}


class ReviewError(ValueError):
    """A pack or a reply this build cannot use, said with what broke."""


def pack(plan: Mapping[str, Any], run: str = "", rev: str = "") -> dict[str, Any]:
    cues = plan["recording"]["cues"]
    at = {c["id"]: i for i, c in enumerate(cues)}
    found = {(f["cue_id"], f["char_start"], f["char_end"]): f
             for f in plan.get("findings", ()) if f.get("code") == "glossary"}
    items = []
    for n, op in enumerate(plan.get("candidates", ())):
        i = at[op["cue_id"]]
        text = cues[i]["text"]
        s, e = int(op["char_start"]), int(op["char_end"])
        ev = (found.get((op["cue_id"], op["char_start"], op["char_end"])) or {}
              ).get("evidence", {})
        items.append({
            "candidate": n,
            "before": cues[i - 1]["text"] if i > 0 else "",
            "text": text,
            "after": cues[i + 1]["text"] if i + 1 < len(cues) else "",
            "span": [s, e],
            "heard": text[s:e],
            "proposed": op["text"],
            "kind": _KIND.get(int(ev.get("kind", 0)), "unknown"),
            "script": _SCRIPT.get(int(ev.get("script", 0)), "unknown"),
            **({"prior": ev["prior"]} if "prior" in ev else {}),
        })
    chars = sum(len(x["before"]) + len(x["text"]) + len(x["after"]) for x in items)
    return {"format": FORMAT, "prompt_version": PROMPT_VERSION, "run": run,
            "rev": rev, "chars": chars, "items": items}


def prompt(p: Mapping[str, Any]) -> str:
    if p.get("format") != FORMAT:
        raise ReviewError(f"not a {FORMAT} pack")
    lines = [
        f"[{PROMPT_VERSION}] You are checking a machine transcript of a spoken talk "
        "(Taiwan Mandarin, often with English technical terms). Each item below is a "
        "place where a term list suspects the speech recogniser wrote a term wrong: "
        "`heard` is what the transcript says at `span` of `text`; `proposed` is the "
        "term the list offers. `before`/`after` are the neighbouring lines.",
        "",
        "For each item decide: would `proposed` be what the speaker actually said? "
        "Pick it only when the context makes it clearly right; when `heard` is an "
        "ordinary word that fits (光線 as light, not fibre), or you are unsure, do "
        "not pick it. `prior` > 0 means the proposed characters are commoner in "
        "written Chinese; it is a weak hint, not evidence.",
        "",
        'Answer with ONE JSON object and nothing else: {"picks": [<candidate numbers>]}. '
        "Use only candidate numbers from the items. Do not write any other text, and "
        "do not suggest words that are not `proposed`.",
        "",
    ]
    for x in p["items"]:
        lines.append(json.dumps(x, ensure_ascii=False))
    return "\n".join(lines) + "\n"


_OBJ = re.compile(r"\{.*\}", re.S)


def picks(reply: str, p: Mapping[str, Any]) -> tuple[int, ...]:
    m = _OBJ.search(reply)
    if not m:
        raise ReviewError("no JSON object in the reply")
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise ReviewError(f"reply is not JSON: {e}") from e
    if not isinstance(obj, dict) or set(obj) != {"picks"}:
        raise ReviewError(f"reply must be exactly {{'picks': [...]}}, got keys {sorted(obj) if isinstance(obj, dict) else type(obj).__name__}")
    got = obj["picks"]
    allowed = {x["candidate"] for x in p["items"]}
    if not isinstance(got, list) or not all(isinstance(n, int) and not isinstance(n, bool) for n in got):
        raise ReviewError("picks must be a list of integers")
    stray = sorted(set(got) - allowed)
    if stray:
        raise ReviewError(f"picks {stray} are not candidates in this pack")
    return tuple(sorted(set(got)))


def learn(edit: Mapping[str, Any], lex: Mapping[str, Any] | None,
          where: str = "") -> tuple[dict[str, Any], int]:
    """Fold an accepted edit round's glossary replacements back into a lexicon.

    Every `glossary` op in the edit record becomes `term` = what was inserted,
    `wrong` += what it removed (from the journal, the only place that says what
    was there). `source` is `advisor` when the op's `picked_by` was a model and
    `human` otherwise, so a reader can tell a model's judgement from a person's.
    Next run, those wrong forms are DECLARED: a replace op, found without any
    model (0 tokens). Returns the new lexicon and how many wrong forms it gained.

    The target is the caller's choice. The default the CLI uses is the talk's own
    file (`_詞表/talks/<run>.learned.json`, not read unless named): 光線 -> 光纖 was
    right in this talk and is light in the next one, so promoting a learned form
    to the standing lists in `_詞表/` is a person's decision, not this function's.
    """
    from . import lexicon as lx
    out = dict(lex or {"format": lx.FORMAT, "name": "learned", "entries": []})
    if out.get("format") != lx.FORMAT:
        raise ReviewError(f"target is not an {lx.FORMAT} lexicon")
    entries = [dict(e) for e in out.get("entries", [])]
    by_term = {e["term"]: e for e in entries}
    who = {(o["cue_id"], o["char_start"]): o.get("picked_by") or "human"
           for o in edit.get("ops", ()) if o.get("code") == "glossary"}
    gained = 0
    for j in edit.get("journal", ()):
        key = (j["cue_id"], j["char_start"])
        if key not in who:
            continue
        term, wrong = j.get("inserted") or "", j.get("removed") or ""
        if not term or not wrong or term == wrong:
            continue
        src = "advisor" if str(who[key]).startswith("advisor:") else "human"
        e = by_term.get(term)
        if e is None:
            e = {"term": term, "wrong": [], "source": src}
            if where:
                e["note"] = f"learned from {where}"
            entries.append(e); by_term[term] = e
        if wrong not in e.setdefault("wrong", []):
            e["wrong"].append(wrong); gained += 1
    out["entries"] = entries
    lx.parse(out, "<learned>")          # what is written must load
    return out, gained


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] == "pack":
        src = pathlib.Path(argv[1])
        data = json.loads(src.read_text(encoding="utf-8"))
        out = pack(data, run=src.parent.parent.name, rev=src.stem)
        pathlib.Path(argv[2]).write_text(
            json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"{len(out['items'])} candidates, {out['chars']} characters of transcript")
        return 0
    if len(argv) == 2 and argv[0] == "prompt":
        sys.stdout.write(prompt(json.loads(pathlib.Path(argv[1]).read_text(encoding="utf-8"))))
        return 0
    if len(argv) == 3 and argv[0] == "picks":
        p = json.loads(pathlib.Path(argv[1]).read_text(encoding="utf-8"))
        got = picks(pathlib.Path(argv[2]).read_text(encoding="utf-8"), p)
        print(" ".join(f"--candidate {n}" for n in got))
        return 0
    if len(argv) == 3 and argv[0] == "learn":
        edit_path, target = pathlib.Path(argv[1]), pathlib.Path(argv[2])
        edit = json.loads(edit_path.read_text(encoding="utf-8"))
        old = json.loads(target.read_text(encoding="utf-8")) if target.exists() else None
        run = edit_path.parent.parent.name
        new, gained = learn(edit, old, where=f"{run}/{edit_path.stem}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(new, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"{gained} wrong forms added to {target}")
        return 0
    sys.stderr.write(__doc__ or "")
    return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(sys.argv[1:]))
