"""The seam one step before the shared function: argv -> the verb's parameters.

`test_agentapi_schema.py::test_both_faces_call_one_function` proves the two faces
hold the SAME function objects -- identity, not equivalence. That is the strong
half. The half nothing covered until now is the step immediately before that call:
`cli._args_for` turns an argparse namespace into keyword arguments, and it is a
hand-written table. A verb that renames a parameter, or grows a required one,
leaves that table stale; the agent face keeps working because it passes the new
name, and the CLI raises `TypeError` at call time -- for whoever runs that verb
first, which may be nobody for a long while.

**How long is measurable, and it has been measured elsewhere**: `ops/lessons.md`
L-043 records the same shape in another product -- one capability, two surfaces, a
refactor that updated only one of them, and `ImportError` on the CLI path for two
days with 2,500 tests green, because no test invoked that handler and the other
surface reached the capability correctly.

The same minority paths exist here. Counting `cli.main([...])` across this suite
before this file was added: `decode` 7, `show` 3, `plan` 3, `intake` 3, `detect` 2,
`runs` 1, `accept` 1 -- and **`open`, `cite` and `cancel` zero**. Those three were
reachable by a person and unchecked by the suite.

This file closes that by construction rather than by diligence: the table below
must name every verb in `verbs.IMPLEMENTATIONS`, and each entry is bound against
the real signature. A new verb with no entry fails the coverage test; there is no
exemption list.
"""
from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

from ltm.agentapi import schema, verbs
from ltm.ui import agent, cli

#: One representative invocation per verb -- the shortest argv that parses. The
#: point is the mapping, not the behaviour, so nothing here has to exist on disk:
#: `bind` never calls the verb.
ARGV: dict[str, list[str]] = {
    "intake": ["intake", "talk.m4a"],
    "decode": ["decode", "talk-0badc0de"],
    "detect": ["detect", "talk-0badc0de"],
    "plan": ["plan", "talk-0badc0de"],
    "accept": ["accept", "talk-0badc0de"],
    "runs": ["runs"],
    "show": ["show", "talk-0badc0de"],
    "open": ["open", "talk.zh.txt"],
    "cite": ["cite", "talk-0badc0de", "--rev", "r000",
             "--granted-by", "the person at this machine"],
    "cancel": ["cancel", "--state", "decoding"],
}


def _kwargs(argv: list[str]) -> dict:
    ns = cli.build_parser().parse_args(argv)
    return cli._args_for(ns)


def test_the_table_names_every_verb_and_only_verbs():
    """No exemption list, in both directions.

    A verb added to `IMPLEMENTATIONS` without an entry here is not covered by the
    binding test below, and a stale entry for a deleted verb is a check that can
    never fail again. Both are failures of this test rather than of a reader's
    attention.
    """
    assert set(ARGV) == set(verbs.IMPLEMENTATIONS)


@pytest.mark.parametrize("verb", sorted(ARGV))
def test_the_cli_passes_arguments_the_verb_actually_accepts(verb):
    """`signature.bind` on what the CLI would really pass, for all ten verbs.

    `bind` is the whole assertion: it raises when a keyword the mapping produces
    is not a parameter (a rename), and when a parameter without a default is
    missing (a new requirement). It does not call the verb, so this stays a
    statement about the seam and not about behaviour -- which is why it can cover
    `cite` and `cancel`, whose behaviour needs a store and a running job.
    """
    fn = verbs.IMPLEMENTATIONS[verb]
    ctx = verbs.Context(root=pathlib.Path("."))
    inspect.signature(fn).bind(ctx, **_kwargs(ARGV[verb]))


def test_the_binding_check_catches_a_rename_and_a_new_requirement():
    """The positive control. A check that has never failed is not known to check.

    Two ways the seam breaks in practice, both applied to the real mapping the CLI
    produces for `plan` (`{"run": ..., "rev": ...}`):

    * a parameter renamed under it (`rev` -> `revision`);
    * a parameter added that the CLI has no flag for and no default to fall back on.

    If either bound cleanly, the test above would be decoration.
    """
    kwargs = _kwargs(ARGV["plan"])
    assert set(kwargs) == {"run", "rev"}, kwargs
    ctx = verbs.Context(root=pathlib.Path("."))

    def renamed(ctx, *, run, revision) -> schema.Response:  # noqa: ARG001
        raise AssertionError("never called")

    def grew_a_requirement(ctx, *, run, rev, audience) -> schema.Response:  # noqa: ARG001
        raise AssertionError("never called")

    for stand_in in (renamed, grew_a_requirement):
        with pytest.raises(TypeError):
            inspect.signature(stand_in).bind(ctx, **kwargs)


#: The one sub-command that is not a verb. A NAMESPACE, holding questions about
#: the tool itself, and it is a single name on purpose: PSM section 18.0 (1) had
#: three shapes to pick from and this one puts exactly one entry in the sentence
#: below, ever. What keeps it from being an exemption list is the property test
#: that follows -- the entry is not "trust me", it is "and nothing under it may
#: reach the product".
NAMESPACE = "agent"


def test_every_verb_the_parser_offers_is_a_verb_the_product_has():
    """The parser is the other end of the same seam.

    `test_both_faces_call_one_function` compares `cli.VERBS` with
    `IMPLEMENTATIONS`; this compares the SUB-PARSERS, which is a different object.
    A sub-parser with no verb behind it is a command that exits with `no such
    verb` -- a promise the help text makes and the product cannot keep.

    **Re-cut, not widened** (2026-09-16, card CLI-2): `agent` is subtracted by
    name and the set equality is unchanged for everything else. Subtracting a
    name the parser does not offer would make this pass for the wrong reason, so
    the namespace's presence is asserted first.
    """
    parser = cli.build_parser()
    (subparsers,) = [a for a in parser._subparsers._group_actions  # noqa: SLF001
                     if hasattr(a, "choices")]
    assert NAMESPACE in subparsers.choices, (
        f"{NAMESPACE!r} is subtracted below; if it is gone, subtract nothing")
    assert set(subparsers.choices) - {NAMESPACE} == set(verbs.IMPLEMENTATIONS)


# --------------------------------------------------------------------------
# what the one exception costs: the namespace is CLOSED (card CLI-2)
# --------------------------------------------------------------------------
#: Read by AST from `ltm.ui.agent`, the module everything under the namespace is
#: dispatched to. Three reaches, each of which would make a meta command into a
#: verb with no response type, no progress and no cancellation.
FORBIDDEN_IMPORTS = ("store", "agentapi", "detect", "engine")
FORBIDDEN_CALLS = ("Context", "call")


def _layer(target: str) -> str:
    """The `ltm` layer a dotted import name lands in, absolute or relative.

    `ltm.store.write`, `..store` and `store` all have to come back as `store`, or
    the check reads three spellings of one reach as one caught and two clean --
    which is exactly what the first version of this function did.
    """
    parts = [p for p in target.lstrip(".").split(".") if p]
    if parts[:1] == ["ltm"]:
        parts = parts[1:]
    return parts[0] if parts else ""


def _reaches(source: str) -> list[str]:
    """Every way this source reaches the product rather than describing it."""
    tree = ast.parse(source)
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            target = "." * node.level + (node.module or "")
            if _layer(target) in FORBIDDEN_IMPORTS:
                bad.append(f"imports {target}")
            for alias in node.names:
                # `from . import store` / `from ltm import store`
                if not (node.module or "").strip(".") or node.module == "ltm":
                    if alias.name in FORBIDDEN_IMPORTS:
                        bad.append(f"imports {alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _layer(alias.name) in FORBIDDEN_IMPORTS and (
                        alias.name.split(".")[0] == "ltm"):
                    bad.append(f"imports {alias.name}")
        elif isinstance(node, ast.Call):
            fn = node.func
            name = (fn.attr if isinstance(fn, ast.Attribute)
                    else fn.id if isinstance(fn, ast.Name) else "")
            if name in FORBIDDEN_CALLS:
                bad.append(f"calls {name}()")
    return bad


def test_nothing_under_the_namespace_reaches_the_product():
    """The price of the exception in the test above.

    `agent` is in the parser's choices and not in `IMPLEMENTATIONS`, which is
    precisely the shape A-8 calls a ban list wearing the other costume -- unless
    the exception is CLOSED. It is closed by this: the module the namespace
    dispatches to may not import a store, an engine, a detector or the agent API,
    may not construct a `Context`, and may not call `verbs.call`. A meta command
    that needs any of those is not a meta command.
    """
    source = pathlib.Path(agent.__file__).read_text(encoding="utf-8")
    assert _reaches(source) == [], (
        "a command under `agent` has started reaching into the product; it "
        "belongs in `verbs.IMPLEMENTATIONS` with a response type, or it does "
        "not belong under this namespace")


@pytest.mark.parametrize("stub", [
    "from ltm.store import write\n",
    "from ..store import write\n",
    "from . import store\n",
    "from ltm import store\n",
    "import ltm.store.write\n",
    "from ..agentapi import verbs\n",
    "from ..engine import runner\n",
    "from ..detect import detectors\n",
    "def go():\n    ctx = Context(root='.')\n",
    "def go():\n    return verbs.call('runs', {})\n",
])
def test_the_closed_namespace_check_fires(stub: str):
    """The positive control, one per way in.

    A check that has only ever seen a clean module is not known to be reading
    it. Each stub is a real line somebody would write when a meta command grows
    an appetite, and every one must be caught.
    """
    assert _reaches(stub) != [], f"not caught: {stub!r}"


def test_the_closed_namespace_check_does_not_fire_on_what_the_module_does():
    """The known-false side: the imports this module legitimately has.

    Without it the check above could be a `return ['bad']` and both tests would
    still pass -- and a gate that flags correct code is one readers learn to skip.
    """
    innocent = ("import importlib.resources\nimport shutil\nimport sys\n"
                "from ltm.ui import read\n"
                "def go():\n    return shutil.which('ltm')\n")
    assert _reaches(innocent) == []
