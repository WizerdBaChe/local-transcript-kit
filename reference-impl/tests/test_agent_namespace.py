"""`ltm agent guide|which`. PSM section 18, cards CLI-3 and CLI-4.

These commands are what a caller runs BEFORE it can call a verb: where is the
contract, and is this command installed. Two properties are worth more than the
rest of this file put together, and each has a test whose failure would be a real
defect rather than a changed string:

* **`onPath` and `importable` must be able to disagree.** A checkout with the
  package importable and nothing on PATH is the ordinary state of this
  repository; if `which` reported one number for both, the field pair would be
  decoration and a caller could not tell "not installed, use `python -m ltm`"
  from "not here at all".
* **`--json` means the same thing on both sides of the sub-command.** argparse
  writes a sub-parser's default over the top-level value, so `ltm --json agent
  which` returning prose is one keyword argument away at all times. It is a
  silent failure -- the command still exits 0.
"""
from __future__ import annotations

import json
import os
import pathlib

import pytest

from ltm.ui import agent, cli


def _raw(p: pathlib.Path) -> str:
    """The file's text with its line endings intact.

    `Path.read_text(newline=...)` is 3.13; this product targets 3.12, and the
    whole point of these tests is that the bytes do not move.
    """
    with p.open("r", encoding="utf-8", newline="") as fh:
        return fh.read()


def _put(p: pathlib.Path, text: str) -> pathlib.Path:
    with p.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return p


def _fake_install(tmp_path: pathlib.Path) -> pathlib.Path:
    """A directory holding something `shutil.which` will accept as `ltm`."""
    d = tmp_path / "bin"
    d.mkdir()
    if os.name == "nt":
        exe = d / "ltm.bat"
        exe.write_text("@echo off\n", encoding="utf-8")
    else:
        exe = d / "ltm"
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        exe.chmod(0o755)
    return exe


# --------------------------------------------------------------------------
# CLI-4: which
# --------------------------------------------------------------------------
def test_which_finds_an_ltm_on_the_path(tmp_path, monkeypatch):
    exe = _fake_install(tmp_path)
    monkeypatch.setenv("PATH", str(exe.parent))
    monkeypatch.setenv("PATHEXT", ".BAT;.EXE;.CMD")

    facts = agent.which()
    assert facts["onPath"] is True
    # `samefile` and not a string comparison: on Windows `shutil.which` returns
    # the extension in PATHEXT's case (`ltm.BAT`), so comparing names would fail
    # on a correct answer -- and the question being asked is "is this the same
    # file", which the filesystem answers better than a string does.
    assert os.path.samefile(facts["path"], exe)


def test_which_reports_discovery_and_installation_as_separate_facts(monkeypatch):
    """The two must be able to disagree, or the pair says nothing.

    This is the state of this repository right now: `ltm` is not on PATH and the
    package imports fine. A `which` that collapsed the two would tell a caller to
    give up where `python -m ltm` would have worked.
    """
    monkeypatch.setenv("PATH", "")
    facts = agent.which()
    assert facts["onPath"] is False
    assert facts["path"] is None
    assert facts["importable"] is True, (
        "the package is importable -- this test is running inside it -- so a "
        "false here means `importable` is reading PATH, not the interpreter")
    assert facts["interpreter"], "the interpreter is the other half of the answer"


def test_which_says_the_way_in_when_it_is_not_on_the_path(monkeypatch):
    """The human face earns its sentence: it names what DOES work."""
    monkeypatch.setenv("PATH", "")
    lines = agent.which_sentences(agent.which())
    assert any("python -m ltm" in line for line in lines), lines


def test_which_json_is_data_on_stdout(capsys, monkeypatch):
    monkeypatch.setenv("PATH", "")
    assert cli.main(["agent", "which", "--json"]) == 0
    out = capsys.readouterr().out
    facts = json.loads(out)
    assert set(facts) == {"onPath", "path", "interpreter", "importable"}


# --------------------------------------------------------------------------
# CLI-3: guide
# --------------------------------------------------------------------------
def test_guide_prints_the_packaged_file_byte_for_byte(capsys):
    """Not "contains the headings": the same bytes.

    The whole of CLI-1 is that there is one copy; a face that reformats it on the
    way out has made a second one that differs by whatever it reformatted.
    """
    assert cli.main(["agent", "guide"]) == 0
    out = capsys.readouterr().out
    assert out == agent.guide_path().read_text(encoding="utf-8")


def test_guide_path_names_a_file_that_exists(capsys):
    assert cli.main(["agent", "guide", "--path"]) == 0
    printed = capsys.readouterr().out.strip()
    assert pathlib.Path(printed).is_file()


def test_guide_json_carries_the_guide_its_path_and_which(capsys):
    """`which` rides along because the two questions arrive together.

    A caller printing the contract is deciding how to drive the product; the next
    thing it needs is whether to spell the command `ltm` or `python -m ltm`.
    """
    assert cli.main(["agent", "guide", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert set(doc) == {"guide", "guidePath", "which"}
    assert doc["guide"] == agent.guide_path().read_text(encoding="utf-8")
    assert pathlib.Path(doc["guidePath"]).is_file()
    assert set(doc["which"]) == {"onPath", "path", "interpreter", "importable"}


# --------------------------------------------------------------------------
# the argparse trap, asserted rather than remembered
# --------------------------------------------------------------------------
@pytest.mark.parametrize("argv", [
    ["--json", "agent", "which"],
    ["agent", "which", "--json"],
])
def test_json_means_the_same_on_both_sides_of_the_sub_command(argv, capsys,
                                                              monkeypatch):
    """The failure this catches is silent: exit 0, prose where JSON was asked for.

    A sub-parser flag with a default overwrites the top-level namespace value, so
    `--json` before the sub-command would be lost. `default=argparse.SUPPRESS` is
    what prevents it, and nothing else in the file says so.
    """
    monkeypatch.setenv("PATH", "")
    assert cli.main(argv) == 0
    json.loads(capsys.readouterr().out)          # raises if a sentence came back


def test_the_flag_is_not_on_by_accident(capsys, monkeypatch):
    """The known-false side: without `--json` the answer is sentences.

    Two spellings agreeing proves nothing if the command always prints JSON.
    """
    monkeypatch.setenv("PATH", "")
    assert cli.main(["agent", "which"]) == 0
    out = capsys.readouterr().out
    assert "on PATH" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


# --------------------------------------------------------------------------
# CLI-5: register writes between two markers and nowhere else
# --------------------------------------------------------------------------
BEFORE = "# My notes\n\nSomething I wrote.\n"
AFTER = "\n## Afterwards\n\nSomething else I wrote.\n"


#: A block as an OLDER version of this command would have left it. The stale
#: body is what makes the tests below exercise the update path at all: with the
#: current block already in place, `register` finds nothing to change and writes
#: nothing, so every assertion about "the rest of the file survived" would hold
#: on a file that was never opened for writing.
STALE = f"{agent.BEGIN}\n## local-transcript-maker\n\nan older pointer.\n{agent.END}\n"


def _with_block(tmp_path: pathlib.Path, *, eol: str = "\n",
                block: str = STALE) -> pathlib.Path:
    """A file with content on BOTH sides of an existing block.

    The interesting case is not the empty file. It is the file somebody has been
    keeping for a year, with the block somewhere in the middle of it.
    """
    p = tmp_path / "CLAUDE.md"
    text = BEFORE + block + AFTER
    return _put(p, text.replace("\n", eol) if eol != "\n" else text)


def test_register_leaves_both_halves_of_the_file_byte_for_byte(tmp_path):
    """The whole contract of this command, said as an assertion.

    An editing tool that reflows, re-indents or re-ends-of-lines the rest of the
    file has eaten work that was not its to touch, and the user will blame the
    tool that ran last.
    """
    p = _with_block(tmp_path)
    before_bytes = p.read_bytes()
    assert cli.main(["agent", "register", "--file", str(p)]) == 0

    after = _raw(p)
    assert p.read_bytes() != before_bytes, (
        "the stale block was not replaced, so this test proved nothing about "
        "the halves around it")
    assert after.startswith(BEFORE)
    assert after.endswith(AFTER)
    assert agent.block_body() in after


def test_registering_twice_changes_nothing(tmp_path, capsys):
    """Idempotent, and it SAYS so rather than writing the same bytes again.

    The block carries no date and no version for this reason: one timestamp in
    it and every run would dirty the user's own repository.
    """
    p = tmp_path / "AGENTS.md"
    _put(p, BEFORE)
    assert cli.main(["agent", "register", "--file", str(p)]) == 0
    once = _raw(p)
    mtime = p.stat().st_mtime_ns

    capsys.readouterr()
    assert cli.main(["agent", "register", "--file", str(p)]) == 0
    assert _raw(p) == once
    assert p.stat().st_mtime_ns == mtime, "an unchanged file was rewritten"
    assert "nothing to do" in capsys.readouterr().out


def test_remove_restores_the_file_byte_for_byte(tmp_path):
    """Register then remove is the identity, on a file that ends in a newline.

    The exception is named in the plan rather than hidden: a file NOT ending in
    a newline gets one, and `--remove` does not take that byte back because it
    was never inside the markers. The next test asserts that case too.
    """
    p = tmp_path / "AGENTS.md"
    _put(p, BEFORE)
    original = p.read_bytes()

    assert cli.main(["agent", "register", "--file", str(p)]) == 0
    assert p.read_bytes() != original
    assert cli.main(["agent", "register", "--file", str(p), "--remove"]) == 0
    assert p.read_bytes() == original


def test_the_one_byte_remove_cannot_take_back_is_declared(tmp_path, capsys):
    p = tmp_path / "AGENTS.md"
    _put(p, "no trailing newline")
    record = agent.plan(p)
    assert record["trailingNewlineAdded"] is True
    assert any("--remove" in line for line in agent.plan_sentences(record))

    assert cli.main(["agent", "register", "--file", str(p)]) == 0
    assert cli.main(["agent", "register", "--file", str(p), "--remove"]) == 0
    assert _raw(p) == "no trailing newline\n"


def test_a_crlf_file_stays_crlf(tmp_path):
    """Windows is the environment this product runs on.

    Reading with universal newlines and writing back would rewrite every line in
    the file -- a whole-file diff from a command that was asked to add six lines.
    """
    p = _with_block(tmp_path, eol="\r\n")
    original = p.read_bytes()
    assert b"\r\n" in original and original.count(b"\r\n") > 5
    assert cli.main(["agent", "register", "--file", str(p)]) == 0

    after = p.read_bytes()
    assert after != original, "the stale block was not replaced"
    assert b"\n" not in after.replace(b"\r\n", b""), "a bare LF was introduced"
    assert after.startswith(BEFORE.replace("\n", "\r\n").encode())
    assert after.endswith(AFTER.replace("\n", "\r\n").encode())


def test_dry_run_writes_nothing(tmp_path, capsys):
    p = tmp_path / "AGENTS.md"
    _put(p, BEFORE)
    original, mtime = p.read_bytes(), p.stat().st_mtime_ns

    assert cli.main(["agent", "register", "--file", str(p), "--dry-run"]) == 0
    assert p.read_bytes() == original
    assert p.stat().st_mtime_ns == mtime
    out = capsys.readouterr().out
    assert "dry run" in out and str(p) in out


def test_dry_run_json_is_the_plan(tmp_path, capsys):
    p = tmp_path / "AGENTS.md"
    assert cli.main(["agent", "register", "--file", str(p),
                     "--dry-run", "--json"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["action"] == "create"
    assert record["dryRun"] is True and record["changed"] is True
    assert not p.exists()
    assert "text" not in record, (
        "the response must not carry a copy of the file it is about")


@pytest.mark.parametrize("broken", [
    agent.BEGIN + "\nhalf a block, no end\n",
    "an end with no beginning\n" + agent.END + "\n",
    agent.END + "\nbackwards\n" + agent.BEGIN + "\n",
    agent._region() + agent._region(),
])
def test_a_file_with_broken_markers_is_refused_not_repaired(broken, tmp_path,
                                                            capsys):
    """The negative control, one per way markers go wrong.

    A file whose markers are already broken is one somebody edited by hand, and
    guessing which half they meant is how an editing tool eats work. Every one of
    these must come back as a refusal with the file untouched -- not a rewrite,
    and not a silent second block.
    """
    p = tmp_path / "AGENTS.md"
    _put(p, BEFORE + broken)
    original = p.read_bytes()

    assert cli.main(["agent", "register", "--file", str(p)]) == 2
    assert p.read_bytes() == original, "a refused register wrote anyway"
    assert "markers-unbalanced" in capsys.readouterr().err


def test_the_refusal_check_passes_a_well_formed_file(tmp_path):
    """The known-false side: one clean pair must NOT be refused.

    Without it the check above could be `raise` and every test would still pass.
    """
    p = _with_block(tmp_path)
    assert agent._find_markers(_raw(p)) is not None
    assert cli.main(["agent", "register", "--file", str(p)]) == 0


def test_targets_only_choose_a_default_file(tmp_path, capsys, monkeypatch):
    """`--target` decides a filename and nothing else, and the plan says which.

    Three of the four names resolve to the same file, which is a fact about other
    people's tools rather than about this one -- so the resolved path is printed
    instead of asked to be trusted.
    """
    monkeypatch.chdir(tmp_path)
    for target, expected in sorted(agent.TARGETS.items()):
        capsys.readouterr()
        assert cli.main(["agent", "register", "--target", target,
                         "--dry-run", "--json"]) == 0
        record = json.loads(capsys.readouterr().out)
        assert pathlib.Path(record["file"]).name == expected


def test_nothing_in_the_product_calls_register(tmp_path):
    """The product never edits somebody's configuration on its own initiative.

    The only two callers are a person setting up their own agent and an agent
    that was told to set itself up -- both of them somebody deciding to modify
    their own files. A product that helpfully registered itself on first run
    would be writing in a repository nobody asked it to touch, which is the shape
    R6 and R14 refuse everywhere else here.

    Checked by reading the tree rather than by remembering: `agent.apply` is the
    one write in this module, and only `agent.run` may reach it.
    """
    import ast

    src = pathlib.Path(agent.__file__).resolve().parents[2]
    offenders = []
    for path in sorted(src.rglob("*.py")):
        if path.resolve() == pathlib.Path(agent.__file__).resolve():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ("apply", "plan"):
                base = node.value
                if isinstance(base, ast.Name) and base.id == "agent":
                    offenders.append(f"{path.name}: agent.{node.attr}")
    assert offenders == [], offenders

    # positive control: the check can see such a call when there is one
    stub = ast.parse("def go():\n    agent.apply(p, r)\n")
    assert any(isinstance(n, ast.Attribute) and n.attr == "apply"
               and isinstance(n.value, ast.Name) and n.value.id == "agent"
               for n in ast.walk(stub))


def test_the_block_says_what_an_agent_must_not_do():
    """R1 travels with the pointer, or the pointer is an invitation.

    The block is what another agent's configuration will carry, so it is the last
    place the "never send the text anywhere" rule can be stated before somebody
    is driving this product from a context that has never read the guide.
    """
    body = agent.block_body()
    assert "ltm agent guide" in body
    assert "never send it" in body.lower()
    assert "do not keep a" in body.lower()      # do not cache the contract


# --------------------------------------------------------------------------
# the namespace touches nothing
# --------------------------------------------------------------------------
def test_the_namespace_needs_no_store_and_no_root(capsys, tmp_path, monkeypatch):
    """`agent which` must answer on a machine with no output root at all.

    It is what a caller runs to find out whether the product is there, so
    requiring the product's store to exist first would be a circle. `main`
    branches before it builds a `Context`; this is that branch, asserted.
    """
    monkeypatch.setenv("PATH", "")
    missing = tmp_path / "nowhere"
    assert cli.main(["--root", str(missing), "agent", "which"]) == 0
    assert not missing.exists(), "answering the question created a store"
    assert "interpreter" in capsys.readouterr().out
