"""PSM card P-1: the output root is resolved per machine, never hard-coded.

Until 2026-11-22 the default was the literal `D:/output/TranscriptDesk`, which is
right on exactly one machine and fails on any machine without a D: drive. The
order is now `--root` > `LTM_ROOT` > `home()/TranscriptDesk`, read at CALL time:
a value captured at import is right only for whoever imported first.
"""
from __future__ import annotations

import pathlib

import pytest

from ltm.agentapi import verbs
from ltm.store import layout
from ltm.ui import cli


def test_the_variable_wins_when_set(monkeypatch, tmp_path):
    monkeypatch.setenv("LTM_ROOT", str(tmp_path / "desk"))
    assert layout.default_root() == tmp_path / "desk"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_unset_or_blank_falls_back_to_home(monkeypatch, tmp_path, value):
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    if value is None:
        monkeypatch.delenv("LTM_ROOT", raising=False)
    else:
        monkeypatch.setenv("LTM_ROOT", value)
    assert layout.default_root() == tmp_path / "TranscriptDesk"


def test_negative_control_no_drive_letter_without_the_variable(monkeypatch):
    # The defect this card removes: a default that names one machine's drive.
    monkeypatch.delenv("LTM_ROOT", raising=False)
    assert not str(layout.default_root()).upper().startswith("D:")


def test_tilde_is_expanded(monkeypatch, tmp_path):
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LTM_ROOT", "~/elsewhere")
    assert layout.default_root() == tmp_path / "elsewhere"


def test_context_reads_the_environment_when_built_not_when_imported(monkeypatch, tmp_path):
    monkeypatch.setenv("LTM_ROOT", str(tmp_path / "late"))
    assert verbs.Context().root == tmp_path / "late"


def test_an_explicit_root_still_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("LTM_ROOT", str(tmp_path / "env"))
    assert verbs.Context(root=tmp_path / "given").root == tmp_path / "given"


def test_cli_default_follows_the_variable(monkeypatch, tmp_path):
    monkeypatch.setenv("LTM_ROOT", str(tmp_path / "cli"))
    ns = cli.build_parser().parse_args(["runs"])
    assert pathlib.Path(ns.root) == tmp_path / "cli"
    ns = cli.build_parser().parse_args(["--root", str(tmp_path / "flag"), "runs"])
    assert pathlib.Path(ns.root) == tmp_path / "flag"
