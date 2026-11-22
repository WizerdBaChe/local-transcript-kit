"""`glossary`: the correction lexicon, the sound-alike finder, and its control pair.

The control pair (INV-9) is REAL text from a real decode: seven cues of the
2026-10-30 large-v3 decode of a Mandarin photonics talk that carry a known error,
and seven that do not (`docs/evidence/glossary_controls_2026-10-30.json`). The
lexicons are frozen beside them. Every number asserted here is in
`docs/glossary-lexicon-2026-10-30.md` §5 with the run that produced it.

Rungs: everything here is rung 0 (examples) except where a test says otherwise;
the control pair is two-sided, and the mutation controls record the asserted
value moving, not only a verdict flipping.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib

import pytest

from fixtures import decodes, stub_decode
from ltm.agentapi import schema, verbs
from ltm.core import editscript, model
from ltm.detect import detectors, lexicon, soundalike
from ltm.store import layout

EVIDENCE = pathlib.Path(__file__).resolve().parents[1] / "docs" / "evidence"
CONTROLS = json.loads((EVIDENCE / "glossary_controls_2026-10-30.json")
                      .read_text(encoding="utf-8"))


def lex(*names: str) -> lexicon.Lexicon:
    return lexicon.load_all([EVIDENCE / f"glossary_lexicon_{n}_2026-10-30.json"
                             for n in names])


BLIND = ("photonics", "talk")          # written before reading the transcript
WITH_HUMAN = ("photonics", "talk", "human")


def _norm(s: str) -> str:
    return "".join(s.lower().split())


def _named(text: str, lx: lexicon.Lexicon) -> list[tuple[str, str, str]]:
    return [(text[h.start:h.end], lx.entries[h.entry].term, h.kind)
            for h in soundalike.find(text, lx)]


# --- the lexicon format ------------------------------------------------------

@pytest.mark.parametrize("bad, says", [
    ({"entries": []}, "format"),
    ({"format": lexicon.FORMAT, "entries": {}}, "entries"),
    ({"format": lexicon.FORMAT, "entries": [{"wrong": ["x"]}]}, "term"),
    ({"format": lexicon.FORMAT, "entries": [{"term": "光纖", "source": "web"}]},
     "source"),
    ({"format": lexicon.FORMAT, "entries": [{"term": "光纖", "sound": "yes"}]},
     "sound"),
    ({"format": lexicon.FORMAT, "entries": [{"term": "光纖", "colour": 1}]},
     "unknown"),
    ({"format": lexicon.FORMAT, "entries": [{"term": "光纖", "wrong": ["光纖"]}]},
     "own wrong form"),
])
def test_a_malformed_lexicon_is_refused_naming_what_broke(bad, says):
    with pytest.raises(lexicon.LexiconError) as e:
        lexicon.parse(bad)
    assert says in str(e.value)


def test_merge_accumulates_wrong_forms_and_keeps_case_apart():
    """A talk file teaches a domain term a new wrong form; `case` stays per entry.

    The second half is a regression: one entry per term let the first file's
    `case: false` decide for 「Language」 (case-sensitive), and seven lower-case
    「language」 in the Breeze decode were flagged (measured 7 -> 0 by this rule).
    """
    a = lexicon.parse({"format": lexicon.FORMAT, "entries": [
        {"term": "linewidth", "wrong": ["Line with"]}]})
    b = lexicon.parse({"format": lexicon.FORMAT, "entries": [
        {"term": "linewidth", "wrong": ["LightWidth"]},
        {"term": "linewidth", "wrong": ["Language"], "case": True}]})
    m = lexicon.merge([a, b])
    assert [(e.term, e.case, e.wrong) for e in m.entries] == [
        ("linewidth", False, ("Line with", "LightWidth")),
        ("linewidth", True, ("Language",))]
    assert _named("the language part", m) == []
    assert _named("the Language part", m) == [("Language", "linewidth", "wrong")]


def test_an_empty_lexicon_finds_nothing():
    """INV-7: no built-in list falls in behind an empty one."""
    for row in CONTROLS["positive"]:
        assert soundalike.find(row["text"], lexicon.Lexicon()) == []


# --- the control pair (INV-9) ------------------------------------------------

@pytest.mark.parametrize("row", CONTROLS["positive"], ids=lambda r: r["id"])
def test_known_true_the_blind_lexicon_names_the_right_term(row):
    """Known-true: each real error is flagged, naming the right correct form, by a
    lexicon that was written without reading the transcript."""
    got = _named(row["text"], lex(*BLIND))
    assert got, row["text"]
    assert any(_norm(row["term"]) in _norm(term) for _w, term, _k in got)
    assert all(kind != "wrong" for _w, _t, kind in got)   # no declared forms here


@pytest.mark.parametrize("row", CONTROLS["negative"], ids=lambda r: r["id"])
def test_known_false_right_text_is_not_flagged(row):
    """Known-false: the terms are written right, or the sound-alike is what the
    speaker said. Zero hits with the lexicon a person would actually run."""
    assert _named(row["text"], lex(*WITH_HUMAN)) == []


def test_the_keep_context_is_what_silences_the_one_real_sound_alike():
    """「送光線」 (send light) reads exactly like 光纖. The blind lexicon flags it --
    one of the seven negatives, measured -- and the human lexicon's keep context
    is the only thing that stops it. Both values recorded: 1 hit -> 0 hits."""
    text = next(r["text"] for r in CONTROLS["negative"] if r["id"] == "c803")
    assert _named(text, lex(*BLIND)) == [("光線", "光纖", "homophone")]
    assert _named(text, lex(*WITH_HUMAN)) == []


def test_mutation_without_the_mergers_the_near_homophones_go_dark(monkeypatch):
    """The zh/z and -ng/-n mergers are what find 矽光紙 and 光屏書. With `merged`
    an identity, the hit count on those two cues moves 2 -> 0."""
    rows = [r for r in CONTROLS["positive"] if r["id"] in ("c88", "c819")]
    L = lex(*BLIND)
    before = sum(len(soundalike.find(r["text"], L)) for r in rows)
    monkeypatch.setattr(soundalike, "merged", lambda s: s)
    soundalike.reading.cache_clear()
    soundalike._plan.cache_clear()
    try:
        after = sum(len(soundalike.find(r["text"], L)) for r in rows)
    finally:
        soundalike.reading.cache_clear()
        soundalike._plan.cache_clear()
    assert (before, after) == (2, 0)


def test_mutation_whole_skeleton_comparison_lets_nonsense_through(monkeypatch):
    """Word-by-word is what keeps 「health insurance」 from standing for
    `locking range` (measured on the in-repo decodes before the rule)."""
    L = lexicon.parse({"format": lexicon.FORMAT, "entries": [
        {"term": "locking range"}]})
    text = "health insurance"
    assert soundalike.find(text, L) == []
    monkeypatch.setattr(soundalike, "latin_close", lambda w, t: soundalike.skeletons_close(
        soundalike.skeleton(w), soundalike.skeleton(t)))
    assert len(soundalike.find(text, L)) == 1


@pytest.mark.parametrize("name, expected", [("cond=False", 8), ("cond=True", 7)])
def test_a_recording_with_no_photonics_in_it_stays_nearly_quiet(name, expected):
    """The in-repo decodes are real speech about something else. The blind
    photonics lexicon's hits there are its false-positive floor, PINNED at the
    measured value so a looser rule shows up as a number, not as a feeling
    (659 and 936 cues; the hits are listed in the design doc §5.3)."""
    rec = decodes.in_repo()[name]
    L = lex("photonics")
    assert sum(len(soundalike.find(c.text, L)) for c in rec.cues) == expected


# --- the detector ------------------------------------------------------------

def _recording_with(texts: list[str]) -> model.Recording:
    """A real in-repo recording whose first cues carry the given texts."""
    rec = decodes.in_repo()[decodes.COND_FALSE]
    cues = list(rec.cues)
    for k, t in enumerate(texts):
        cues[k] = dataclasses.replace(cues[k], text=t)
    return dataclasses.replace(rec, cues=tuple(cues))


def test_a_declared_wrong_form_proposes_a_replace_and_a_sound_alike_does_not():
    """User ruling 2026-10-30: 「已知錯寫帶替換，音近只標」."""
    rec = _recording_with(["聽海枯的聲音", "我們用Council"])
    human = lexicon.parse({"format": lexicon.FORMAT, "entries": [
        {"term": "海哭", "wrong": ["海枯"], "source": "human"}]})
    blind_cmsl = lexicon.parse({"format": lexicon.FORMAT, "entries": [
        {"term": "COMSOL", "source": "domain"}]})
    plan, verdicts = detectors.run(rec, enabled=("glossary",),
                                   lex=lexicon.merge([human, blind_cmsl]))
    g = {v.finding.span.cue_id: v for v in verdicts if v.code == "glossary"}
    a, b = g[rec.cues[0].id], g[rec.cues[1].id]
    assert a.finding.actions == ("replace", "keep")
    assert [(op.op, op.text) for op in a.ops] == [("replace", "海哭")]
    assert a.finding.evidence.as_dict()["kind"] == 1.0
    assert b.finding.actions == ("check", "keep") and b.ops == ()
    assert b.finding.evidence.as_dict()["kind"] == 4.0
    assert [op.text for op in plan.script] == ["海哭"]
    assert plan.summary.proposals == {}        # BR-5 counts deletions only
    source = {c.id: c.text for c in rec.cues}
    assert editscript.prove(source, plan.script)


def test_inv8_still_refuses_a_delete_from_glossary(monkeypatch):
    """`may_replace` opens the gate for replace ops and nothing else."""
    class Deleting(detectors.Glossary):
        def scan(self, rec, thr):
            for i, batch in super().scan(rec, thr):
                yield i, tuple(dataclasses.replace(v, ops=tuple(
                    dataclasses.replace(op, op="delete", text=None) for op in v.ops))
                    for v in batch)
    rec = _recording_with(["聽海枯的聲音"])
    L = lexicon.parse({"format": lexicon.FORMAT, "entries": [
        {"term": "海哭", "wrong": ["海枯"]}]})
    monkeypatch.setattr(detectors, "build",
                        lambda enabled, terms=None, lex=None: [Deleting(L)])
    with pytest.raises(detectors.DetectError) as e:
        detectors.run(rec)
    assert "INV-8" in str(e.value)


def test_a_replace_inside_a_deleted_cue_keeps_its_finding_and_says_so(monkeypatch):
    """A delete and a replace on one cue cannot both be in a script (`merge`
    refuses overlap). The delete wins, the glossary finding stays, its op goes,
    and `collides` = 1 says why: nothing is resolved silently."""
    rec = _recording_with(["聽海枯的聲音"])
    L = lexicon.parse({"format": lexicon.FORMAT, "entries": [
        {"term": "海哭", "wrong": ["海枯"]}]})

    class DeleteFirst:
        name, may_delete = "engine-loop", True

        def scan(self, rec, thr):
            for i, cue in enumerate(rec.cues):
                out = ()
                if i == 0:
                    span = model.Span(cue.id, 0, len(cue.text))
                    out = (detectors.Verdict(
                        "finding", self.name, (0, 0), 2,
                        finding=model.Finding(
                            id="engine-loop:0-0", span=span, code=self.name,
                            severity="high", unit="cue",
                            evidence=model.Evidence.of("cue", signals=2.0),
                            actions=("delete", "keep")),
                        ops=detectors._whole_cue_deletes(rec, 0, 0, self.name)),)
                yield i, out
    monkeypatch.setattr(detectors, "build", lambda enabled, terms=None, lex=None: [
        detectors.Glossary(L), DeleteFirst()])
    plan, verdicts = detectors.run(rec)
    assert [op.op for op in plan.script] == ["delete"]
    g = next(v for v in verdicts if v.code == "glossary")
    assert g.ops == () and g.finding.actions == ("check", "keep")
    assert g.finding.evidence.as_dict()["collides"] == 1.0


def test_glossary_has_controls_and_stays_out_of_the_default_set():
    """INV-9 is met (the pair above), so no uncontrolled warning; the default set
    is unchanged because a lexicon is a user's list, not a default."""
    assert "glossary" not in detectors.UNCONTROLLED
    assert "glossary" not in detectors.DEFAULT
    assert detectors.warnings_for(("glossary",)) == []


# --- through the agent face ----------------------------------------------------

def test_the_verb_reads_the_lexicon_folder_and_never_echoes_the_text(
        tmp_path, monkeypatch):
    """`_詞表/` under the root is read; `--lexicon` adds to it; the response holds
    counts and codes only (INV-12), and `accept` applies the replace."""
    from ltm.engine import contract
    import sys
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
    (folder / "lab.json").write_text(json.dumps({
        "format": lexicon.FORMAT, "entries": [
            {"term": "實驗成果", "wrong": ["實驗的結果"], "source": "human"}]},
        ensure_ascii=False), encoding="utf-8")
    got = verbs.call(ctx, "detect", run=run, rev=first.rev,
                     enabled=("engine-loop",), lexicons=())
    assert "glossary" not in got.findings          # not enabled, folder not read

    talk = tmp_path / "talk.lexicon.json"
    talk.write_text(json.dumps({"format": lexicon.FORMAT, "entries": []}),
                    encoding="utf-8")
    got = verbs.call(ctx, "detect", run=run, rev=first.rev,
                     # the stub's cues are one sentence twice, so engine-loop
                     # would delete them and every replace would yield to it
                     enabled=(), lexicons=(str(talk),))
    assert got.findings.get("glossary", 0) >= 6     # six ZH cues, each twice
    wire = json.dumps(schema.response(got) if hasattr(schema, "response")
                      else dataclasses.asdict(got), ensure_ascii=False, default=str)
    assert "實驗成果" not in wire

    done = verbs.call(ctx, "accept", run=run, rev=got.rev, ops=())
    srt = next(p for p in (pathlib.Path(x) for x in done.paths)
               if p.suffix == ".srt")
    srt = srt if srt.is_absolute() else root / srt
    assert "實驗成果" in srt.read_text(encoding="utf-8")
    assert "實驗的結果" not in srt.read_text(encoding="utf-8")


def test_a_broken_lexicon_file_is_a_bad_argument(tmp_path):
    ctx = verbs.Context(root=tmp_path / "TranscriptDesk")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(schema.AgentError):
        verbs.detect(ctx, run="nope", lexicons=(str(bad),))
