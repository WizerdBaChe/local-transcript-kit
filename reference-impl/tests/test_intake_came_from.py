"""PSM card P-2: intake records where a file came from, and refuses before writing.

Generic on purpose (R10, the ordinary door): a URL and a short note, from any
caller -- a person, an agent that ran a downloader, a phone sync. Nothing here
knows any other product, and nothing reads anyone else's folder (R8).

Rung 0/1: example tests over the accept/refuse boundary. The refused inputs are a
hand list of the shapes the validator must close, each paired with the bytes of
the marker before and after (the asserted value a broken build would change).
"""
from __future__ import annotations

import dataclasses
import json

import pytest

from ltm.agentapi import schema, verbs
from ltm.agentapi.schema import AgentError
from ltm.store import layout

URL_A = "https://www.youtube.com/watch?v=demoVideo01"
URL_B = "https://www.bilibili.com/video/BV1DemoX7eB2"


@pytest.fixture()
def root(tmp_path):
    return tmp_path / "TranscriptDesk"


@pytest.fixture()
def audio(tmp_path):
    p = tmp_path / "旁白 2026.m4a"
    p.write_bytes(b"not really audio; intake never decodes it")
    return p


def marker(root, run):
    return layout.intake_dir(root) / f"{run}.json"


def entries(root, run):
    return json.loads(marker(root, run).read_text("utf-8")).get("came_from", [])


def test_first_intake_records_url_and_note(root, audio):
    ctx = verbs.Context(root=root)
    got = verbs.intake(ctx, path=str(audio), from_url=URL_A,
                       from_note="fetched by a downloader 2.2.0")
    rows = entries(root, got.run)
    assert [(r["url"], r["note"]) for r in rows] == [(URL_A, "fetched by a downloader 2.2.0")]
    assert rows[0]["at"]                       # a timestamp, set by the product
    assert got.came_from_count == 1.0


def test_a_second_source_appends_and_a_repeat_does_not(root, audio):
    ctx = verbs.Context(root=root)
    run = verbs.intake(ctx, path=str(audio), from_url=URL_A).run
    verbs.intake(ctx, path=str(audio), from_url=URL_B)
    again = verbs.intake(ctx, path=str(audio), from_url=URL_A)
    assert [r["url"] for r in entries(root, run)] == [URL_A, URL_B]
    assert again.came_from_count == 2.0


def test_a_plain_reintake_keeps_the_list(root, audio):
    ctx = verbs.Context(root=root)
    run = verbs.intake(ctx, path=str(audio), from_url=URL_A).run
    plain = verbs.intake(ctx, path=str(audio))
    assert [r["url"] for r in entries(root, run)] == [URL_A]
    assert plain.came_from_count == 1.0


def test_no_provenance_means_an_empty_list_and_zero(root, audio):
    got = verbs.intake(verbs.Context(root=root), path=str(audio))
    assert entries(root, got.run) == []
    assert got.came_from_count == 0.0


@pytest.mark.parametrize("url, note", [("", None), ("   ", None), (None, ""), (None, "  ")])
def test_blank_strings_behave_as_absent(root, audio, url, note):
    got = verbs.intake(verbs.Context(root=root), path=str(audio),
                       from_url=url, from_note=note)
    assert entries(root, got.run) == []


def test_a_note_alone_is_accepted(root, audio):
    got = verbs.intake(verbs.Context(root=root), path=str(audio),
                       from_note="phone recording, seminar room")
    assert [(r["url"], r["note"]) for r in entries(root, got.run)] == [
        (None, "phone recording, seminar room")]


REFUSED = [
    ("from-url", {"from_url": "file:///C:/talk.m4a"}),
    ("from-url", {"from_url": "javascript:alert(1)"}),
    ("from-url", {"from_url": "http://"}),
    ("from-url", {"from_url": "https://example.com/a b"}),
    ("from-url", {"from_url": "https://example.com/\x00"}),
    ("from-url", {"from_url": "https://example.com/" + "a" * 2048}),
    ("from-note", {"from_note": "line one\nline two"}),
    ("from-note", {"from_note": "x" * 201}),
]


@pytest.mark.parametrize("subject, kwargs", REFUSED)
def test_a_bad_value_is_refused_before_the_marker_moves(root, audio, subject, kwargs):
    ctx = verbs.Context(root=root)
    run = verbs.intake(ctx, path=str(audio), from_url=URL_A).run
    before = marker(root, run).read_bytes()
    with pytest.raises(AgentError) as caught:
        verbs.intake(ctx, path=str(audio), **kwargs)
    assert (caught.value.code, caught.value.subject) == ("bad-argument", subject)
    assert marker(root, run).read_bytes() == before


def test_positive_control_the_boundary_values_are_accepted(root, audio):
    # The refused list above is only meaningful if its neighbours pass.
    ctx = verbs.Context(root=root)
    got = verbs.intake(ctx, path=str(audio),
                       from_url="https://example.com/" + "a" * (2048 - 20),
                       from_note="x" * 200)
    assert got.came_from_count == 1.0


def test_the_response_carries_a_count_never_the_text(root, audio):
    # INV-12: the walk over shipping responses must still pass with no new
    # allowlist entry, and the URL must appear nowhere in the response.
    got = verbs.intake(verbs.Context(root=root), path=str(audio), from_url=URL_A)
    schema.check_response(schema.IntakeResponse)
    assert URL_A not in json.dumps(dataclasses.asdict(got), ensure_ascii=False)
