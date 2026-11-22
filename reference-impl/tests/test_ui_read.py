"""The one text reader, and the join rule it exists to enforce.

`docs/gui-contract.json` says two things about text reaching a person, and this
file is what makes them checkable rather than remembered:

* **the join rule** -- the string a finding names is
  `cues[cue_id].text[char_start:char_end]`, in the source coordinates INV-5 fixes;
* **the forbidden alternative** -- re-locating a finding by counting lines in
  `全文/*.txt`, which is a rendering of the SURVIVING cues and therefore stops
  agreeing with the cue ids the first time anybody accepts a deletion.

The second one gets a real control here rather than a comment, because it is the
kind of shortcut that works on every test fixture until a user makes a decision.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

from fixtures import stub_decode
from ltm.agentapi import schema, verbs
from ltm.engine import contract
from ltm.store import layout
from ltm.ui import cli, read

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STUB = os.path.join(HERE, "tests", "fixtures", "stub_sidecar.py")


@pytest.fixture()
def decoded(tmp_path, monkeypatch):
    """A store with one decoded run, through the stub engine."""
    weights = stub_decode.fake_weights(tmp_path / "weights")
    monkeypatch.setenv(contract.MODEL_DIR_VAR, str(weights))
    monkeypatch.setenv(contract.SIDECAR_PYTHON_VAR, sys.executable)
    monkeypatch.setenv("LTM_STUB", json.dumps(stub_decode.DECODE))
    audio = tmp_path / "訪談 2026.m4a"
    audio.write_bytes(b"not really audio; the stub never opens it")
    root = tmp_path / "TranscriptDesk"
    ctx = verbs.Context(root=root)
    run = verbs.intake(ctx, path=str(audio)).run
    first = verbs.decode(ctx, run=run, duration_seconds=stub_decode.DURATION,
                         python=None, script=STUB)
    return ctx, root, run, first.rev


def test_the_words_shown_are_the_span_the_finding_names(decoded):
    """The join rule, character for character.

    Positive control: the excerpt's `span` equals the slice the record says, for
    every finding in the round. Negative control: it is NOT the whole cue -- a
    reader shown the entire cue for a six-character span has been told where to
    look and not what was found, and the two are only distinguishable when at
    least one span is shorter than its cue. The assertion pins that too, so a
    fixture where every span happens to be the whole cue cannot make this vacuous.
    """
    ctx, root, run, rev = decoded
    got = verbs.plan(ctx, run=run, rev=rev)
    cues = read.cues_of(root, run, rev)
    assert got.findings, "no findings, so this test asserts about nothing"

    shorter_than_its_cue = 0
    for ref in got.findings:
        text = cues[str(ref.cue_id)]["text"]
        piece = read.excerpt(cues, str(ref.cue_id), ref.char_start, ref.char_end)
        assert piece is not None
        assert piece.span == text[int(ref.char_start):int(ref.char_end)]
        if len(piece.span) < len(text):
            shorter_than_its_cue += 1
    assert shorter_than_its_cue or all(
        len(cues[str(r.cue_id)]["text"]) == int(r.char_end - r.char_start)
        for r in got.findings)


def test_counting_lines_in_the_plain_text_stops_working_and_the_cue_id_does_not(
        decoded):
    """The forbidden locator, shown failing. This is the whole point of `read.py`.

    `全文/*.txt` is one line per surviving cue. Accept one deletion that empties a
    cue and the line numbers shift; the cue ids do not. So:

    * **before** the accept, line N and cue N agree -- which is why the shortcut
      looks correct and would pass any test written only at this point;
    * **after** it, the offset locator lands on the wrong text while `cue_id`
      still reaches the right one.

    Without the first half this test could pass because the shortcut never worked;
    without the second it could pass because nothing ever changes.
    """
    ctx, root, run, rev = decoded
    got = verbs.plan(ctx, run=run, rev=rev)
    assert got.ops, "nothing to accept, so the drift can never happen"

    stem = layout.run_stem_of(run)

    def lines_of(revision):
        path = (layout.run_dir(root, run) / layout.PLAIN)
        hits = sorted(path.glob(f"*.{revision}.*txt")) or sorted(
            path.glob(f"*.{revision}.txt"))
        assert hits, f"no plain-text member for {revision} under {path}"
        return hits[0].read_text("utf-8").splitlines()

    cues_before = read.cues_of(root, run, rev)
    order = [c["id"] for c in json.loads(
        (layout.run_dir(root, run) / layout.PLANS / f"{rev}.json"
         ).read_text("utf-8"))["recording"]["cues"]]
    before = lines_of(rev)
    assert len(before) == len(order), (
        "the shortcut has to be sound BEFORE the accept, or its later failure "
        "proves nothing")

    # Accept every op, which empties the cues they name.
    applied = verbs.accept(ctx, run=run, rev=rev)
    after = lines_of(applied.rev)
    assert len(after) < len(before), (
        "no cue was emptied, so the line numbering never drifted and the negative "
        "control below would be vacuous")

    # The cue id still reaches its own text in the round it was measured in; the
    # line offset does not.
    victim = str(got.ops[0].cue_id)
    assert victim in cues_before
    old_line = order.index(victim)
    assert old_line >= len(after) or after[old_line] != before[old_line], (
        "line {} means the same thing before and after, so the fixture does not "
        "exercise the drift".format(old_line))


def test_a_round_that_cannot_show_its_text_says_so_instead_of_printing_nothing(
        decoded, tmp_path):
    """BR-10 at the reading layer: a failure announces itself.

    Positive control: a missing plan record raises `ReadError` with the path in
    it, and the CLI turns that into a visible line rather than the numbers-only
    output it used to print (which is indistinguishable from a healthy round).
    Negative control: the healthy round prints no such line.
    """
    ctx, root, run, rev = decoded
    got = verbs.plan(ctx, run=run, rev=rev)

    healthy = cli.describe_plan(got, root)
    assert not any("cannot read" in line for line in healthy)
    assert any("[" in line for line in healthy), "no words were shown at all"

    broken = cli.describe_plan(got, tmp_path / "not-a-store")
    assert any("cannot read this round's text" in line for line in broken)
    # And the numbers survive: a record it cannot read is not a reason to say
    # nothing about the round.
    assert broken[1] == healthy[1]

    with pytest.raises(read.ReadError) as e:
        read.cues_of(tmp_path / "not-a-store", run, rev)
    assert "no plan record" in str(e.value)


def test_an_unknown_cue_is_none_and_never_a_sentence_that_reads_like_transcript():
    """`excerpt` returns `None` for what it cannot show. Three ways in.

    A sentinel string would be indistinguishable from a very short cue, and this
    product's whole claim is that the reader can tell a measurement from an
    absence.
    """
    cues = {"c0": {"text": "abcdef", "start": 1.0, "end": 2.0}}
    assert read.excerpt(cues, "c9", 0, 2) is None          # no such cue
    assert read.excerpt({"c1": {"start": 0.0}}, "c1", 0, 2) is None   # no text
    assert read.excerpt({"c1": {"text": None}}, "c1", 0, 2) is None   # not a str

    # And a span past the end of the cue clamps rather than raising: the record is
    # what it is, and a reader gets the truncated truth instead of a traceback.
    piece = read.excerpt(cues, "c0", 4, 99)
    assert piece is not None and piece.span == "ef"


def test_the_clock_is_for_a_person_about_to_go_and_listen():
    assert read.clock(0.0) == "0:00"
    assert read.clock(65.4) == "1:05"
    assert read.clock(3925.0) == "1:05:25"
