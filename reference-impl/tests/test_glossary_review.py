"""Language case, the character-frequency prior, dormant candidates, and the
consent-gated pick's parser (2026-10-30 increment after `test_glossary.py`).

Evidence rungs: `case_of` and `picks` refusals are rung 0 examples over the
shapes the design names; the candidate path is rung 0 end to end through the
real verbs with a stub decode, with both sides of every switch asserted by value.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
import sys

import pytest

from fixtures import decodes, stub_decode
from ltm.agentapi import schema, verbs
from ltm.core import model
from ltm.detect import detectors, lexicon, review, soundalike as sa
from ltm.store import layout


# --- language case, read from the text ------------------------------------------

@pytest.mark.parametrize("text, case", [
    ("我們今天講光纖", "zh-only"),
    ("我們用COMSOL模擬", "mixed"),
    ("Kerr comb 的頻寬", "mixed"),
    ("the linewidth enhancement factor", "en-only"),
    ("16 32 64", "en-only"),               # nothing for a homophone to read
    ("", "en-only"),
])
def test_case_is_read_from_the_cue_text_without_a_label(text, case):
    assert sa.case_of(text) == case


def test_every_glossary_finding_carries_its_case():
    rec = _recording_with(["聽海枯的聲音", "我們用Council模擬"])
    L = lexicon.parse({"format": lexicon.FORMAT, "entries": [
        {"term": "海哭", "source": "human"}, {"term": "COMSOL", "source": "domain"}]})
    _plan, verdicts = detectors.run(rec, enabled=("glossary",), lex=L)
    got = {v.finding.span.cue_id: v.finding.evidence.as_dict()["script"]
           for v in verdicts if v.code == "glossary"
           and v.finding.span.cue_id in (rec.cues[0].id, rec.cues[1].id)}
    assert got == {rec.cues[0].id: detectors.GLOSSARY_SCRIPT["zh-only"],
                   rec.cues[1].id: detectors.GLOSSARY_SCRIPT["mixed"]}


# --- character frequency ------------------------------------------------------------

def _csv(path: pathlib.Path, rows: list[tuple[str, str]], head="字頻序位,字,字頻") -> pathlib.Path:
    path.write_text(head + "\n" + "".join(f"{i},{c},\"{n} \"\n" for i, (c, n) in enumerate(rows, 1)),
                    encoding="utf-8-sig")
    return path


def test_the_frequency_csv_is_summed_across_files_and_separators_are_read(tmp_path):
    a = _csv(tmp_path / "a.csv", [("線", "1,000"), ("纖", "10")])
    b = _csv(tmp_path / "b.csv", [("線", "500")])
    assert lexicon.load_freq([a, b]) == {"線": 1500, "纖": 10}


def test_a_csv_without_the_two_columns_is_refused_naming_it(tmp_path):
    bad = _csv(tmp_path / "x.csv", [("線", "1")], head="rank,char,count")
    with pytest.raises(lexicon.LexiconError) as e:
        lexicon.load_freq([bad])
    assert "x.csv" in str(e.value)


def test_prior_by_value_and_its_none_cases():
    f = {"光": 1000, "線": 999, "纖": 9}
    assert sa.prior("光線", "光纖", f) == pytest.approx(1 - 3)   # log10(10) - log10(1000)
    assert sa.prior("光線", "光纖", None) is None
    assert sa.prior("Council", "COMSOL", f) is None


def test_prior_appears_only_when_a_table_is_given():
    rec = _recording_with(["送進光線裡"])
    L = lexicon.parse({"format": lexicon.FORMAT, "entries": [{"term": "光纖"}]})
    ev = lambda freq: next(v for v in detectors.run(
        rec, enabled=("glossary",), lex=L, freq=freq)[1]
        if v.code == "glossary").finding.evidence.as_dict()
    assert "prior" not in ev(None)
    assert ev({"光": 1000, "線": 999, "纖": 9})["prior"] == pytest.approx(-2.0)


# --- dormant candidates -------------------------------------------------------------

def _recording_with(texts: list[str]) -> model.Recording:
    rec = decodes.in_repo()[decodes.COND_FALSE]
    cues = list(rec.cues)
    for k, t in enumerate(texts):
        cues[k] = dataclasses.replace(cues[k], text=t)
    return dataclasses.replace(rec, cues=tuple(cues))


def test_a_sound_alike_is_a_candidate_and_a_declared_form_is_a_script_op():
    rec = _recording_with(["聽海枯的聲音", "送進光線裡"])
    L = lexicon.parse({"format": lexicon.FORMAT, "entries": [
        {"term": "海哭", "wrong": ["海枯"]}, {"term": "光纖"}]})
    plan, _ = detectors.run(rec, enabled=("glossary",), lex=L)
    assert [op.text for op in plan.script] == ["海哭"]
    assert [op.text for op in plan.candidates] == ["光纖"]


def test_a_candidate_inside_a_deleted_cue_is_not_offered(monkeypatch):
    """An offer `accept` would refuse every time is not an offer."""
    rec = _recording_with(["送進光線裡"])
    L = lexicon.parse({"format": lexicon.FORMAT, "entries": [{"term": "光纖"}]})

    class DeleteFirst:
        name, may_delete = "engine-loop", True

        def scan(self, rec, thr):
            for i, cue in enumerate(rec.cues):
                out = ()
                if i == 0:
                    out = (detectors.Verdict(
                        "finding", self.name, (0, 0), 2,
                        finding=model.Finding(
                            id="engine-loop:0-0", span=model.Span(cue.id, 0, len(cue.text)),
                            code=self.name, severity="high", unit="cue",
                            evidence=model.Evidence.of("cue", signals=2.0),
                            actions=("delete", "keep")),
                        ops=detectors._whole_cue_deletes(rec, 0, 0, self.name)),)
                yield i, out
    alone, _ = detectors.run(rec, enabled=("glossary",), lex=L)
    assert len(alone.candidates) == 1                       # control: offered alone
    monkeypatch.setattr(detectors, "build", lambda enabled, terms=None, lex=None: [
        detectors.Glossary(L), DeleteFirst()])
    plan, _ = detectors.run(rec)
    assert plan.candidates == ()


@pytest.fixture
def decoded(tmp_path, monkeypatch):
    from ltm.engine import contract
    root = tmp_path / "TranscriptDesk"
    audio = tmp_path / "talk.m4a"
    audio.write_bytes(b"stub")
    weights = stub_decode.fake_weights(tmp_path / "weights")
    monkeypatch.setenv(contract.MODEL_DIR_VAR, str(weights))
    monkeypatch.setenv(contract.SIDECAR_PYTHON_VAR, sys.executable)
    monkeypatch.setenv("LTM_STUB", json.dumps(stub_decode.DECODE))
    stub = str(pathlib.Path(__file__).resolve().parent / "fixtures" / "stub_sidecar.py")
    ctx = verbs.Context(root=root)
    run = verbs.intake(ctx, path=str(audio)).run
    first = verbs.decode(ctx, run=run, duration_seconds=stub_decode.DURATION,
                         python=None, script=stub)
    folder = layout.lexicon_dir(root)
    folder.mkdir(parents=True)
    # 試驗 reads shi-yan like the stub's 實驗: a homophone, so a candidate.
    (folder / "lab.json").write_text(json.dumps({
        "format": lexicon.FORMAT, "entries": [{"term": "試驗", "source": "domain"}]},
        ensure_ascii=False), encoding="utf-8")
    got = verbs.call(ctx, "detect", run=run, rev=first.rev, enabled=("glossary",),
                     lexicons=())
    return ctx, run, got.rev


def _text(ctx, done) -> str:
    srt = next(pathlib.Path(x) for x in done.paths if x.endswith(".srt"))
    srt = srt if srt.is_absolute() else ctx.root / srt
    return srt.read_text(encoding="utf-8")


def test_plan_lists_candidates_as_positions_and_never_the_text(decoded):
    ctx, run, rev = decoded
    p = verbs.call(ctx, "plan", run=run, rev=rev)
    assert p.ops == () and len(p.candidates) == 12          # 6 ZH cues, 實驗 twice each
    wire = json.dumps(dataclasses.asdict(p), ensure_ascii=False, default=str)
    assert "試驗" not in wire and "實驗" not in wire


def test_accept_with_no_selection_never_applies_a_candidate(decoded):
    ctx, run, rev = decoded
    done = verbs.call(ctx, "accept", run=run, rev=rev, ops=())
    assert done.declined == 1.0                             # nothing in the script
    named = verbs.call(ctx, "accept", run=run, rev=rev, candidates=(0,))
    text = _text(ctx, named)
    assert named.ops == 1.0 and text.count("試驗") == 1 and text.count("實驗") == 11


def test_the_picker_is_recorded_and_checked(decoded):
    ctx, run, rev = decoded
    done = verbs.call(ctx, "accept", run=run, rev=rev, candidates=(0, 2),
                      picked_by="advisor:claude-sonnet-5-5@glossary-pick-1")
    edit = next(pathlib.Path(x) for x in done.paths if x.endswith(".json") and "edit" in x)
    edit = edit if edit.is_absolute() else ctx.root / edit
    rows = json.loads(edit.read_text(encoding="utf-8"))["ops"]
    assert [r["picked_by"] for r in rows] == ["advisor:claude-sonnet-5-5@glossary-pick-1"] * 2
    for bad, code in (({"candidates": (99,)}, "candidate"),
                      ({"candidates": (0,), "picked_by": "detector:glossary"}, "picked-by"),
                      ({"candidates": (0,), "picked_by": "說了算"}, "picked-by")):
        with pytest.raises(schema.AgentError) as e:
            verbs.call(ctx, "accept", run=run, rev=rev, **bad)
        assert code in str(e.value.args) or code in repr(e.value)


def test_overlapping_candidates_are_refused(decoded, tmp_path):
    ctx, run, rev = decoded
    plan = json.loads((layout.run_dir(ctx.root, run) / "_plans" / f"{rev}.json")
                      .read_text(encoding="utf-8"))
    dup = dict(plan["candidates"][0])
    plan["candidates"].append(dup)                          # same span twice
    (layout.run_dir(ctx.root, run) / "_plans" / f"{rev}.json").write_text(
        json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(schema.AgentError) as e:
        verbs.call(ctx, "accept", run=run, rev=rev,
                   candidates=(0, len(plan["candidates"]) - 1))
    assert "overlap" in repr(e.value)


# --- feedback: an accepted correction becomes a declared wrong form --------------------

def test_learning_turns_a_picked_candidate_into_a_replace_op_next_time(decoded):
    """The 0-token second pass: before learning, 試驗 is only a candidate (no op);
    after `learn` from the accepted round, the same detect proposes the replace."""
    ctx, run, rev = decoded
    done = verbs.call(ctx, "accept", run=run, rev=rev, candidates=(0,),
                      picked_by="advisor:claude-sonnet-5-5@glossary-pick-1")
    edit = json.loads((layout.run_dir(ctx.root, run) / layout.EDITS / f"{done.rev}.json")
                      .read_text(encoding="utf-8"))
    lex, gained = review.learn(edit, None, where=f"{run}/{done.rev}")
    assert gained == 1
    assert [(e["term"], e["wrong"], e["source"]) for e in lex["entries"]] == [
        ("試驗", ["實驗"], "advisor")]
    target = ctx.root / "learned.json"
    target.write_text(json.dumps(lex, ensure_ascii=False), encoding="utf-8")
    before = verbs.call(ctx, "plan", run=run, rev=rev)
    again = verbs.call(ctx, "detect", run=run, rev=rev, enabled=("glossary",),
                       lexicons=(str(target),))
    after = verbs.call(ctx, "plan", run=run, rev=again.rev)
    assert (len(before.ops), len(after.ops)) == (0, 12)    # by value, both sides
    assert after.candidates == ()


def test_learning_says_who_picked_and_ignores_other_detectors():
    edit = {"ops": [
        {"cue_id": "c1", "char_start": 0, "code": "glossary", "picked_by": "human"},
        {"cue_id": "c2", "char_start": 0, "code": "engine-loop"}],
        "journal": [
        {"cue_id": "c1", "char_start": 0, "removed": "光線", "inserted": "光纖"},
        {"cue_id": "c2", "char_start": 0, "removed": "重複重複", "inserted": ""}]}
    lex, gained = review.learn(edit, None)
    assert gained == 1 and lex["entries"] == [
        {"term": "光纖", "wrong": ["光線"], "source": "human"}]
    again, more = review.learn(edit, lex)                  # idempotent
    assert more == 0 and again["entries"] == lex["entries"]
    with pytest.raises(review.ReviewError):
        review.learn(edit, {"format": "something-else", "entries": []})


# --- the review pack and the parser ----------------------------------------------------

def test_pack_holds_context_and_states_its_size(decoded):
    ctx, run, rev = decoded
    data = json.loads((layout.run_dir(ctx.root, run) / "_plans" / f"{rev}.json")
                      .read_text(encoding="utf-8"))
    p = review.pack(data, run=run, rev=rev)
    assert p["prompt_version"] == review.PROMPT_VERSION and len(p["items"]) == 12
    first = p["items"][0]
    assert (first["heard"], first["proposed"], first["kind"], first["script"]) == (
        "實驗", "試驗", "homophone", "zh-only")
    assert p["chars"] == sum(len(x["before"]) + len(x["text"]) + len(x["after"])
                             for x in p["items"])
    assert review.PROMPT_VERSION in review.prompt(p)


PACK = {"format": review.FORMAT, "items": [{"candidate": 0}, {"candidate": 3}]}


@pytest.mark.parametrize("reply, want", [
    ('{"picks": [3]}', (3,)),
    ('Here you go:\n{"picks": [0, 3, 3]}', (0, 3)),
    ('{"picks": []}', ()),
])
def test_known_true_a_well_formed_reply_is_read(reply, want):
    assert review.picks(reply, PACK) == want


@pytest.mark.parametrize("reply", [
    "I think 0 and 3",                        # no JSON
    '{"picks": [7]}',                         # not a candidate
    '{"picks": [0], "rewrite": "光纖"}',       # free text smuggled in
    '{"picks": ["0"]}',                       # not integers
    '{"picks": [true]}',
    '[0, 3]',
])
def test_known_false_anything_but_numbers_from_the_pack_is_refused(reply):
    with pytest.raises(review.ReviewError):
        review.picks(reply, PACK)
