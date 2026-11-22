"""`ltm` and `python -m ltm` are one product. PSM section 18, card CLI-6.

There are three ways into this product and all three must land on one function:
`pyproject.toml`'s console script, `python -m ltm`, and an in-process call to
`cli.main`. Two of those are strings in files nothing executes during a test run
-- a console-script entry point that names a function which was renamed, or a
`package-data` line that stopped matching, fails at somebody's INSTALL, which is
the one moment a test suite is not watching.

So the strings are read and resolved here:

* the entry point is resolved by `importlib` from the name `pyproject.toml`
  gives, and asserted to BE `cli.main` -- identity, not equality, the same shape
  `test_agentapi_schema.py::test_both_faces_call_one_function` uses for the two
  faces;
* `python -m ltm` is run as a real subprocess, because `__main__.py` is the one
  module in this package that an in-process import never executes.
"""
from __future__ import annotations

import importlib
import os
import pathlib
import subprocess
import sys
import tomllib

import pytest

from ltm.ui import cli

REPO = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO / "src"


@pytest.fixture(scope="module")
def project() -> dict:
    with (REPO / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def test_the_console_script_resolves_to_this_main(project):
    """Read the string, import what it names, and check the object.

    `ltm = "ltm.ui.cli:main"` is a promise kept by a packaging tool at install
    time and by nothing at all during development. A rename inside this package
    would leave the string pointing at a function that no longer exists, and the
    first person to find out would be the first person to install it.
    """
    scripts = project["project"]["scripts"]
    assert set(scripts) == {"ltm"}, (
        f"a second console script is a design change, not a packaging detail: "
        f"{sorted(scripts)}")

    module_name, _, attribute = scripts["ltm"].partition(":")
    resolved = getattr(importlib.import_module(module_name), attribute)
    assert resolved is cli.main, (
        f"{scripts['ltm']!r} does not resolve to `ltm.ui.cli.main`; the "
        f"installed command and the tested function are two different things")


def test_the_module_entry_point_is_the_same_function():
    """`python -m ltm` imports `main` from the same place, checked in process."""
    main_module = importlib.import_module("ltm.__main__")
    assert main_module.main is cli.main


def test_the_packaged_guide_is_declared_as_package_data(project):
    """CLI-1's packaging claim, read from the file that makes it.

    The guide is only "shipped" because of this table. A wheel built without it
    would leave `ltm agent guide` raising `FileNotFoundError` on an installed
    product while every test in this repository stayed green, because a checkout
    finds the file next to the source either way.
    """
    data = project["tool"]["setuptools"]["package-data"]
    assert "AGENTS.md" in data["ltm"], data


# --------------------------------------------------------------------------
# the subprocess: the only way `__main__.py` is ever executed
# --------------------------------------------------------------------------
def _run(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run([sys.executable, "-m", "ltm", *args],
                          capture_output=True, text=True, env=env,
                          cwd=str(REPO), timeout=120)


def test_python_dash_m_ltm_runs_and_offers_the_agent_namespace():
    done = _run("--help")
    assert done.returncode == 0, done.stderr
    assert "agent" in done.stdout, done.stdout
    # and the namespace's own help is reachable through the same route
    inner = _run("agent", "--help")
    assert inner.returncode == 0, inner.stderr
    for thing in ("guide", "which", "register"):
        assert thing in inner.stdout, inner.stdout


def test_python_dash_m_ltm_agent_which_answers_out_of_process():
    """The one command that must work before anything else is set up.

    In process it shares this interpreter's state; out of process it has to find
    its own package, its own interpreter and its own guide. That is the situation
    a caller is actually in.
    """
    done = _run("agent", "which", "--json")
    assert done.returncode == 0, done.stderr
    assert '"interpreter"' in done.stdout
    assert done.stdout.strip().startswith("{")


def test_a_bad_subcommand_fails_loudly_out_of_process():
    """The known-false side: the subprocess harness can report a failure.

    Without it `_run` returning a green for anything would make the two tests
    above decoration -- a harness that cannot fail is not a harness.
    """
    done = _run("agent", "not-a-thing")
    assert done.returncode != 0
    assert "not-a-thing" in (done.stderr + done.stdout)
