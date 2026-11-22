"""`script-switch`: where a transcript left the Chinese script the reader expects.

User rulings 2026-11-23: summary counts plus one info finding per run of the
other script, not in `DEFAULT`; the expected script defaults to `hant` and can
be `hans`. The text is never converted.

Rung 0 examples on the two in-repo real decodes, two-sided (the same text with
the expected script flipped), plus a mutation control whose asserted count
moves. The stored-decode controls over a whole output root are
`docs/evidence/script_switch_controls_2026-11-23.txt` (23 all-traditional
plans: 0 runs with `hant`, 1 with `hans`).
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
import sys

import pytest

from fixtures import decodes, stub_decode
from ltm.agentapi import schema, verbs
from ltm.detect import detectors, scriptform

IN_REPO = decodes.in_repo()


def _sizes(rec, expect):
    plan, _ = detectors.run(rec, enabled=("script-switch",), expect_script=expect)
    return sorted((int(f.evidence.as_dict()["cues"]) for f in plan.findings),
                  reverse=True), plan


@pytest.mark.parametrize("name, sizes", [
    (decodes.COND_FALSE, [36, 14, 11, 11, 7, 4, 4, 3, 3]),
    (decodes.COND_TRUE, [113, 51]),
])
def test_known_true_the_simplified_runs_of_a_real_decode_are_found(name, sizes):
    """PINNED: the runs measured on 2026-11-23; a looser or tighter rule shows
    up as a number."""
    got, plan = _sizes(IN_REPO[name], "hant")
    assert got == sizes
    assert set(plan.summary.script_cues) == {"hant", "hans", "none"}
    assert plan.script == () and plan.candidates == ()      # never an edit


def test_known_false_a_traditional_stretch_gives_no_run_and_flips_when_expected_differs():
    """The same real text both ways: its traditional cues are no run with `hant`
    expected, and ARE the run with `hans` expected."""
    rec = IN_REPO[decodes.COND_FALSE]
    labels = [scriptform.label(c.text) for c in rec.cues]
    hant_only = [c for c, lab in zip(rec.cues, labels) if lab != "hans"]
    trad = dataclasses.replace(rec, cues=tuple(hant_only))
    assert _sizes(trad, "hant")[0] == []
    assert _sizes(trad, "hans")[0] == [sum(1 for x in labels if x == "hant")]


def test_the_run_length_floor_is_what_drops_scattered_cues(monkeypatch):
    """Mutation control: with the floor at 1 a scattered single cue becomes a run
    (count moves), with the shipped floor it does not."""
    rec = IN_REPO[decodes.COND_FALSE]
    cues = list(rec.cues)
    k = next(i for i, c in enumerate(cues) if scriptform.label(c.text) == "hant"
             and scriptform.label(cues[i - 1].text) == "hant"
             and scriptform.label(cues[i + 1].text) == "hant")
    cues[k] = dataclasses.replace(cues[k], text="验收")
    one = dataclasses.replace(rec, cues=tuple(cues))
    shipped = len(_sizes(one, "hant")[0])
    monkeypatch.setattr(scriptform, "MIN_CUES", 1)
    monkeypatch.setattr(scriptform.other_runs, "__defaults__", (1,))
    assert len(_sizes(one, "hant")[0]) == shipped + 1


def test_shared_characters_count_for_neither_script():
    assert scriptform.counts("中文的人") == (0, 0)
    assert scriptform.label("系统") == "hans" and scriptform.label("系統") == "hant"
    assert scriptform.label("頭髮") == "hant" and scriptform.label("头发") == "hans"


def test_absent_when_not_run_and_refused_outside_the_closed_set(tmp_path):
    plan, _ = detectors.run(IN_REPO[decodes.COND_FALSE], enabled=("engine-loop",))
    assert plan.summary.script_cues == {}
    with pytest.raises(detectors.DetectError):
        detectors.ScriptSwitch("latin")
    with pytest.raises(schema.AgentError) as e:
        verbs.detect(verbs.Context(root=tmp_path), run="nope", script="latin")
    assert e.value.subject == "script"


def test_naming_a_script_turns_it_on_and_the_response_carries_counts_only(
        tmp_path, monkeypatch):
    from ltm.engine import contract
    root = tmp_path / "TranscriptDesk"
    audio = tmp_path / "talk.m4a"
    audio.write_bytes(b"stub")
    monkeypatch.setenv(contract.MODEL_DIR_VAR,
                       str(stub_decode.fake_weights(tmp_path / "weights")))
    monkeypatch.setenv(contract.SIDECAR_PYTHON_VAR, sys.executable)
    monkeypatch.setenv("LTM_STUB", json.dumps(stub_decode.DECODE))
    stub = str(pathlib.Path(__file__).resolve().parent / "fixtures" / "stub_sidecar.py")
    ctx = verbs.Context(root=root)
    run = verbs.intake(ctx, path=str(audio)).run
    first = verbs.decode(ctx, run=run, duration_seconds=stub_decode.DURATION,
                         python=None, script=stub)
    got = verbs.call(ctx, "detect", run=run, rev=first.rev, enabled=(),
                     script="hans")
    assert set(got.script_cues) == {"hant", "hans", "none"}
    assert sum(got.script_cues.values()) > 0
    plan = json.loads(next(pathlib.Path(p) for p in got.paths
                           if "_plans" in p).read_text(encoding="utf-8"))
    assert plan["script_cues"] == {k: int(v) for k, v in got.script_cues.items()}
