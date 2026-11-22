"""`ltm agent ...` — the meta commands, which are about the TOOL, not a transcript.

PSM section 18, cards CLI-2 and CLI-4. Everything reachable from here answers a
question an agent has before it can call a verb at all: where is the contract,
is this command installed, how do I wire it into my own configuration. Nothing
here touches a store, a run or a revision.

**Why this is a namespace and not three verbs.** `tests/test_two_faces.py::
test_every_verb_the_parser_offers_is_a_verb_the_product_has` asserts that the
parser's sub-commands are exactly `verbs.IMPLEMENTATIONS`, with no exemption
list -- and PSM section 16.0 (1) already refused to widen it for `serve`. So
`agent` enters that assertion as ONE name, and it is a namespace rather than a
verb: the assertion is re-cut to `choices - {"agent"} == IMPLEMENTATIONS` and the
exception is paid for by a property test which reads this module's AST and
requires that nothing here imports `store.write`, calls a verb, or builds a
`Context`. An exemption list would have been the cheaper edit and is A-8's
ban-list-in-a-costume: whoever adds the second entry is the person the rule was
written to stop.

**The guide is package data and there is exactly one copy** (card CLI-1). An
installed `ltm` has no repository to read, so a contract living only at the
repository root could never be printed by the installed command -- and a copy
made at build time is two copies of one contract, which is the drift this
project has been blocked for twice.
"""
from __future__ import annotations

import argparse
import importlib.resources
import importlib.util
import json
import pathlib
import shutil
import sys
from typing import Any, Optional, Sequence

#: The packaged contract. Reached through `importlib.resources`, so it resolves
#: from a checkout and from a wheel by the same call.
GUIDE = "AGENTS.md"

#: What `register` writes between, and it writes nowhere else. HTML comments
#: because every target this product knows about is markdown, and a marker a
#: reader can see is one they can delete by hand if they change their mind.
BEGIN = "<!-- ltm:begin -->"
END = "<!-- ltm:end -->"


#: `--target` picks a default FILE, and that is all it decides. The mapping is a
#: convenience for the spellings in use on 2026-09-16, not a claim this product
#: maintains about other people's tools -- `--file` overrides it, and the plan
#: always prints the path it resolved to, so nobody has to trust this table.
TARGETS: dict[str, str] = {
    "claude": "CLAUDE.md",
    "codex": "AGENTS.md",
    "droid": "AGENTS.md",
    "agents-md": "AGENTS.md",
}


class RegisterError(Exception):
    """A refusal from `register`. Carries a code, like every other refusal.

    Its own class rather than `schema.AgentError` because nothing under this
    namespace may import the agent API -- see the module docstring and the
    property test that enforces it.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def guide_path() -> pathlib.Path:
    return pathlib.Path(str(importlib.resources.files("ltm") / GUIDE))


def guide_text() -> str:
    return guide_path().read_text(encoding="utf-8")


def which() -> dict[str, Any]:
    """Discovery and installation, reported as two separate facts.

    They are different questions and the difference is the one an agent gets
    wrong. A checkout with the package importable and nothing on PATH is the
    ordinary state of this repository, and it has to read as such rather than as
    "not installed, give up": `python -m ltm` works there and `ltm` does not.

    `importable` is computed rather than assumed for the same reason a gate runs
    its own control -- this module IS `ltm`, so the answer is true whenever the
    call succeeds, and the field exists to tell a caller who found nothing on
    PATH that there is still a way in.
    """
    found = shutil.which("ltm")
    return {
        "onPath": found is not None,
        "path": found,
        "interpreter": sys.executable,
        "importable": importlib.util.find_spec("ltm") is not None,
    }


def which_sentences(facts: dict[str, Any]) -> list[str]:
    """One line per fact. The human face of `which`."""
    return [
        f"on PATH      : {'yes, ' + str(facts['path']) if facts['onPath'] else 'no'}",
        f"interpreter  : {facts['interpreter']}",
        f"importable   : {'yes' if facts['importable'] else 'no'}"
        + ("" if facts["onPath"] or not facts["importable"]
           else "  (so `python -m ltm` works here; `ltm` does not)"),
    ]


# --------------------------------------------------------------------------
# register: a block between two markers, and nothing else in the file
# --------------------------------------------------------------------------
def block_body() -> str:
    """What goes between the markers.

    **No date, no version, no measured number.** Re-registering must produce the
    same bytes or `--remove` cannot restore anything and every run shows up as a
    change in the user's own repository; a timestamp in here would make this
    command dirty a file every time it ran. Anything that does change belongs
    behind `ltm agent guide`, which is what the block tells the reader to run.
    """
    return (
        "## local-transcript-maker (`ltm`)\n"
        "\n"
        "A local tool that makes a machine transcript's uncertainty visible.\n"
        "Everything runs on this machine; nothing is uploaded.\n"
        "\n"
        "Read its machine-facing contract before driving it, and do not keep a\n"
        "copy -- ask for it, every time:\n"
        "\n"
        "```\n"
        "ltm agent guide      # the contract (or: python -m ltm agent guide)\n"
        "ltm agent which      # is `ltm` on PATH, and under which interpreter\n"
        "```\n"
        "\n"
        "Never print transcript text into a conversation and never send it\n"
        "anywhere: the recording and its text do not leave this machine.\n"
    )


def _region(eol: str = "\n") -> str:
    region = f"{BEGIN}\n{block_body()}{END}\n"
    return region if eol == "\n" else region.replace("\n", eol)


def _eol_of(text: str) -> str:
    """The line ending the target file already uses.

    Read and written WITHOUT newline translation, and the block is rendered to
    match. Otherwise registering into a CRLF file rewrites every line in it --
    the diff is the whole file, the user's editor blames this command for it, and
    "the two halves survive byte for byte" would be false in the one environment
    this product runs on. `CLAUDE.md`'s Windows notes are about exactly this.
    """
    return "\r\n" if "\r\n" in text else "\n"


def _read(path: pathlib.Path) -> str:
    """The file's bytes as text, with its line endings left alone.

    `open(..., newline="")` and not `Path.read_text(newline=...)`: that keyword
    arrived in 3.13 and this product targets 3.12.
    """
    with path.open("r", encoding="utf-8", newline="") as fh:
        return fh.read()


def _find_markers(text: str) -> Optional[tuple[int, int]]:
    """`(start, end)` of the marked region, or `None`. Raises on a broken pair.

    Refused rather than repaired, in every direction: two blocks, a marker with
    no partner, an END before its BEGIN. A file whose markers are already wrong
    is one where somebody edited by hand, and guessing which half they meant is
    how an editing tool eats work that was not its to touch.
    """
    begins = text.count(BEGIN)
    ends = text.count(END)
    if begins == 0 and ends == 0:
        return None
    if begins != 1 or ends != 1:
        raise RegisterError(
            "markers-unbalanced",
            f"{begins} begin marker(s) and {ends} end marker(s); expected one "
            f"of each. Fix the file by hand -- this command will not guess")
    i = text.index(BEGIN)
    j = text.index(END)
    if j < i:
        raise RegisterError(
            "markers-unbalanced",
            "the end marker comes before the begin marker")
    j += len(END)
    if text[j:j + 2] == "\r\n":                  # the file's own ending, whichever
        j += 2
    elif text[j:j + 1] == "\n":
        j += 1
    return i, j


def plan(path: pathlib.Path, *, remove: bool = False) -> dict[str, Any]:
    """What `register` would do, as a record. Writes nothing.

    `text` is the whole file as it would stand afterwards; `changed` says whether
    that differs from what is there now, which is what makes re-registering a
    no-op rather than a no-op-shaped write.
    """
    exists = path.is_file()
    original = _read(path) if exists else ""
    span = _find_markers(original)
    eol = _eol_of(original)

    before = original
    added_newline = False
    if remove:
        action = "remove"
        after = before if span is None else before[:span[0]] + before[span[1]:]
    elif span is not None:
        action = "update"
        after = before[:span[0]] + _region(eol) + before[span[1]:]
    else:
        action = "create" if not exists else "append"
        if before and not before.endswith(("\n", "\r")):
            # Named in the plan because it is the one byte `--remove` cannot
            # take back: the region is removed exactly, and this newline was
            # never part of it.
            added_newline = True
            before = before + eol
        after = before + _region(eol)

    return {
        "file": str(path),
        "action": action,
        "exists": exists,
        "hasBlock": span is not None,
        "changed": after != original,
        "trailingNewlineAdded": added_newline,
        "text": after,
    }


def apply(path: pathlib.Path, record: dict[str, Any]) -> None:
    """Write the planned text. The only write anything in this module performs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(record["text"])


def plan_sentences(record: dict[str, Any]) -> list[str]:
    what = {
        "create": "create it and add the block",
        "append": "add the block at the end",
        "update": "replace the block that is already there",
        "remove": "remove the block",
    }[record["action"]]
    lines = [f"{record['file']}: {what}"]
    if not record["changed"]:
        lines.append("  nothing to do -- the file already says exactly this")
    if record["trailingNewlineAdded"]:
        lines.append("  (the file does not end with a newline; one is added, and "
                     "--remove will not take that one byte back)")
    return lines


# --------------------------------------------------------------------------
# the parser
# --------------------------------------------------------------------------
def add_parser(sub) -> None:
    """Attach the `agent` namespace to the CLI's sub-parsers."""
    p = sub.add_parser(
        "agent",
        help="meta commands about the tool itself (guide, which, register)",
        description="Commands about this tool rather than about a transcript. "
                    "Nothing here reads or writes a store.")
    inner = p.add_subparsers(dest="thing", required=True)

    g = inner.add_parser("guide", help="print the machine-facing contract")
    g.add_argument("--path", action="store_true",
                   help="print where the contract is instead of printing it")
    _json_flag(g)

    w = inner.add_parser("which", help="is this command installed, and where")
    _json_flag(w)

    r = inner.add_parser(
        "register",
        help="add a pointer to this tool in your own agent configuration",
        description="Writes a block between two markers in a file YOU name, and "
                    "touches nothing else in it. The product never calls this "
                    "itself: the only callers are a person setting up their own "
                    "agent, and an agent that was told to set itself up.")
    r.add_argument("--target", default="agents-md", choices=sorted(TARGETS),
                   help="which file to default to (--file overrides it)")
    r.add_argument("--file", default=None,
                   help="the file to write in, if not the target's default")
    r.add_argument("--remove", action="store_true",
                   help="take the block out again")
    r.add_argument("--dry-run", action="store_true",
                   help="print the plan and write nothing")
    _json_flag(r)


def _json_flag(p: argparse.ArgumentParser) -> None:
    """`--json` after the sub-command as well as before it.

    `default=argparse.SUPPRESS` and not `False`: with a default, argparse writes
    the sub-parser's value over the top-level one, so `ltm --json agent which`
    would silently come back as prose. Suppressed, the attribute is only set when
    the flag is actually typed, and the two spellings agree.
    """
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                   help="print the answer as JSON on stdout")


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------
def run(ns: argparse.Namespace) -> int:
    """Handle `ltm agent <thing>`. Returns the process exit code.

    Data on stdout, sentences on stderr for anything that is not the answer --
    the same convention the verbs use, not a second one.
    """
    as_json = bool(getattr(ns, "json", False))

    if ns.thing == "guide":
        path = guide_path()
        if getattr(ns, "path", False):
            print(json.dumps({"guidePath": str(path)}) if as_json else path)
            return 0
        text = guide_text()
        if as_json:
            print(json.dumps({"guide": text, "guidePath": str(path),
                              "which": which()}, ensure_ascii=False))
        else:
            sys.stdout.write(text)
        return 0

    if ns.thing == "which":
        facts = which()
        if as_json:
            print(json.dumps(facts))
        else:
            print("\n".join(which_sentences(facts)))
        return 0

    if ns.thing == "register":
        path = pathlib.Path(ns.file if ns.file else TARGETS[ns.target])
        try:
            record = plan(path, remove=ns.remove)
        except RegisterError as e:
            if as_json:
                print(json.dumps({"code": e.code, "file": str(path),
                                  "detail": e.detail}))
            else:
                print(f"{e.code}: {e.detail}", file=sys.stderr)
            return 2

        if not ns.dry_run and record["changed"]:
            apply(path, record)

        # The plan is the answer in both forms. `text` is dropped from the JSON:
        # it is the whole file, the caller has it on disk, and a response that
        # embeds a copy of the thing it just wrote is the second copy again.
        if as_json:
            print(json.dumps({k: v for k, v in record.items() if k != "text"}
                             | {"dryRun": bool(ns.dry_run)}))
        else:
            print("\n".join(plan_sentences(record)
                            + (["  (dry run: nothing was written)"]
                               if ns.dry_run else [])))
        return 0

    raise AssertionError(f"unreachable: argparse offered {ns.thing!r}")
