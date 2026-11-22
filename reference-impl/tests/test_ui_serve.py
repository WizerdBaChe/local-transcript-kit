"""The loopback face, and INV-23 with both of the controls that ruling names.

PSM §16, cards F-1..F-6.

**Why this suite never opens a socket to itself.** `tests/test_layering.py` bans
a network import in every shipping file, and a test module is a shipping file
(`shipping_python()` includes `tests/`); only the `ui` LAYER is exempt, and that
exemption is keyed on `src/ltm/ui`. So the requests below are driven through the
real `BaseHTTPRequestHandler` subclass over an in-memory connection: the request
line, the headers, the cookie, the status line and the body are all the product's
own code doing the parsing and the writing. What that cannot exercise is the
`accept()` loop, so the socket itself is asserted separately -- bound, loopback,
non-zero, and closed after `stop()`.

The exposure walk is the point of the file. A test that only asserted 「`/run`
shows the words」 would pass on a product with a second, unlisted route that also
showed them, which is the exact change INV-23 says nobody blocks at review.
"""
from __future__ import annotations

import ast
import html
import io
import json
import pathlib
import re
import shutil
import sys
import threading

import pytest

from fixtures import stub_decode
from ltm.agentapi import schema, verbs
from ltm.engine import contract
from ltm.store import layout
from ltm.ui import cli, read, serve

HERE = pathlib.Path(__file__).resolve().parent
STUB = str(HERE / "fixtures" / "stub_sidecar.py")

#: An all-Chinese decode whose AUDIO side says English in every slot, so the two
#: signals disagree everywhere: agreement 0.000 and the stretch is
#: `undetermined`. F-4 needs a round that is BOTH untrustworthy overall and has
#: findings in it -- the standard fixture's stretches are labelled, so it could
#: not show that the two halves of the screen come from two different verbs.
UNDETERMINED_DECODE = {"segments": 12, "gap": 0.01, "seg_seconds": 7.5,
                       "texts": [stub_decode.ZH] * 12,
                       "slot_langs": ["en", "en", "en"]}


# --- building a store ----------------------------------------------------

def _decode(tmp_path, monkeypatch, stub: dict, filename: str, root=None):
    weights = stub_decode.fake_weights(tmp_path / "weights")
    monkeypatch.setenv(contract.MODEL_DIR_VAR, str(weights))
    monkeypatch.setenv(contract.SIDECAR_PYTHON_VAR, sys.executable)
    monkeypatch.setenv("LTM_STUB", json.dumps(stub))
    audio = tmp_path / filename
    audio.write_bytes(b"not really audio; the stub never opens it -- "
                      + filename.encode("utf-8"))
    ctx = verbs.Context(root=root or (tmp_path / "TranscriptDesk"))
    run = verbs.intake(ctx, path=str(audio)).run
    rev = verbs.decode(ctx, run=run, duration_seconds=stub_decode.DURATION,
                       python=None, script=STUB).rev
    return ctx, run, rev


@pytest.fixture()
def face(tmp_path, monkeypatch):
    """A launched face over a store with one decoded run. ASCII run id.

    The id is ASCII so the request lines below can be written by hand; the CJK
    case has a test of its own, because a run id really can hold a space.
    """
    ctx, run, rev = _decode(tmp_path, monkeypatch, dict(stub_decode.DECODE),
                            "talk.m4a")
    launched = serve.start(ctx)
    try:
        yield launched, ctx, run, rev
    finally:
        launched.stop()


@pytest.fixture()
def undetermined(tmp_path, monkeypatch):
    ctx, run, rev = _decode(tmp_path, monkeypatch, UNDETERMINED_DECODE,
                            "mixed.m4a")
    launched = serve.start(ctx)
    try:
        yield launched, ctx, run, rev
    finally:
        launched.stop()


# --- the in-memory connection --------------------------------------------

class _Keeper(io.BytesIO):
    """A buffer `finish()` may close without losing what was written."""

    def close(self) -> None:
        pass


class _Connection:
    """Everything `StreamRequestHandler.setup()` asks a socket for."""

    def __init__(self, raw: bytes) -> None:
        self._incoming = _Keeper(raw)
        self.outgoing = _Keeper()

    def makefile(self, mode="rb", bufsize=-1, *args, **kwargs):
        return self._incoming if "r" in mode else self.outgoing

    def sendall(self, data) -> None:
        self.outgoing.write(data)


class Answer:
    """A parsed HTTP response, as the product actually wrote it."""

    def __init__(self, raw: bytes) -> None:
        head, _, body = raw.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").splitlines()
        self.status = int(lines[0].split()[1])
        self.headers: dict[str, str] = {}
        for line in lines[1:]:
            name, _, value = line.partition(":")
            self.headers[name.strip().lower()] = value.strip()
        self.body = body.decode("utf-8", "replace")


SAFE = ("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.~")


def q(value: str) -> str:
    """Percent-encode. Written out because `urllib` may not be imported here."""
    return "".join(
        c if c in SAFE else "".join(f"%{b:02X}" for b in c.encode("utf-8"))
        for c in value)


def fetch(launched, method: str, target: str, *, cookie: str | None = None,
          body: str | None = None) -> Answer:
    lines = [f"{method} {target} HTTP/1.1", f"Host: {serve.HOST}"]
    payload = b""
    if body is not None:
        payload = body.encode("utf-8")
        lines.append("Content-Type: application/x-www-form-urlencoded")
        lines.append(f"Content-Length: {len(payload)}")
    if cookie is not None:
        lines.append(f"Cookie: {serve.COOKIE}={cookie}")
    raw = ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + payload
    conn = _Connection(raw)
    launched.server.RequestHandlerClass(conn, (serve.HOST, 51234),
                                        launched.server)
    return Answer(conn.outgoing.getvalue())


# ==========================================================================
# F-1 -- INV-23: the list, and both controls
# ==========================================================================

def _texts_of(ctx, run, rev) -> list[str]:
    """Every distinct cue text in the round, which is what may not leak."""
    cues = read.cues_of(ctx.root, run, rev)
    return sorted({row["text"] for row in cues.values() if row.get("text")})


def _leaky(session, request):
    """A route that returns `Cue.text` and is NOT on the list.

    It reaches the reader directly rather than through `serve._cues_for`,
    because that is how the mistake actually happens: somebody adds a handler,
    uses the reader that is already imported, and never meets the list at all. A
    control that went through the guard would only be testing the guard.
    """
    run = request.query["run"][0]
    rev = request.query["rev"][0]
    cues = read.cues_of(session.ctx.root, run, rev)
    return serve.Reply(200, "<pre>" + "\n".join(
        row["text"] for row in cues.values()) + "</pre>")


def walk(launched, ctx, run, rev) -> tuple[list[str], dict[str, bool]]:
    """Drive every registered route and report which ones returned transcript.

    Returns `(leaks, carried)` -- the routes that returned text while not on the
    list, and what every route did. The second half is what keeps the first
    honest: a walk where nothing carried text would report no leaks on a broken
    checker just as loudly as on a clean product.
    """
    texts = _texts_of(ctx, run, rev)
    assert texts, "the fixture has no transcript, so nothing could leak"
    leaks, carried = [], {}
    for path in list(serve.ROUTES):
        if path in serve.POST_ROUTES:
            answer = fetch(launched, "POST", path, cookie=launched.token,
                           body=f"run={q(run)}&rev={q(rev)}")
        else:
            answer = fetch(launched, "GET",
                           f"{path}?run={q(run)}&rev={q(rev)}",
                           cookie=launched.token)
        hit = any(text in answer.body for text in texts)
        carried[path] = hit
        if hit and path not in serve.TEXT_ROUTES:
            leaks.append(path)
    return leaks, carried


def test_negative_control_no_registered_route_leaks_and_the_listed_one_does_show(
        face):
    """INV-23's known-FALSE half: the product as it ships must be clean.

    And it must be clean for a reason: `carried["/run"]` asserts the listed route
    really did return the words, so the walk is known to be able to SEE text.
    Without that line this test would pass on a face that rendered nothing.
    """
    launched, ctx, run, rev = face
    leaks, carried = walk(launched, ctx, run, rev)
    assert leaks == []
    assert carried["/run"] is True, (
        "the one route that is allowed to show the words did not show them, so "
        "this walk has not been shown any text and proves nothing")
    assert all(not v for p, v in carried.items() if p not in serve.TEXT_ROUTES)


def test_positive_control_an_unregistered_route_that_returns_text_is_caught(
        face, monkeypatch):
    """INV-23's known-TRUE half, and the whole reason the list exists.

    `/leak` is 「多開一條 JSON 路由比較好接」 in ten lines: it is registered in the
    route table, it is not on `TEXT_ROUTES`, and it returns the transcript. The
    walk must catch it, and must catch ONLY it.
    """
    launched, ctx, run, rev = face
    monkeypatch.setitem(serve.ROUTES, "/leak", _leaky)
    leaks, carried = walk(launched, ctx, run, rev)
    assert leaks == ["/leak"], leaks
    assert carried["/leak"] is True
    assert carried["/run"] is True, "the listed route must still be allowed"


def test_the_guard_refuses_text_to_a_route_that_is_not_on_the_list(face):
    """`_cues_for`, the second line, with both of its own controls.

    It cannot catch a route that fabricates text some other way -- `_leaky`
    above is proof of that -- which is why it is not the mechanism INV-23 rests
    on. It catches the ordinary version: reaching for the reader without
    thinking about the list.
    """
    launched, ctx, run, rev = face
    session = launched.session
    listed = serve.Request(method="GET", path="/run", query={}, form={},
                           cookie=None)
    unlisted = serve.Request(method="GET", path="/", query={}, form={},
                             cookie=None)
    assert serve._cues_for(session, listed, run, rev), "the listed route must read"
    with pytest.raises(serve.ExposureError) as e:
        serve._cues_for(session, unlisted, run, rev)
    assert "TEXT_ROUTES" in str(e.value)


def test_the_list_is_a_literal_frozenset_inside_the_table(face):
    """The card's shape, asserted rather than assumed.

    A list assembled at runtime, or one naming a route that does not exist,
    cannot be read by the walk above -- and a list that has drifted from the
    table is a list nobody is enforcing.
    """
    assert isinstance(serve.TEXT_ROUTES, frozenset)
    assert serve.TEXT_ROUTES <= set(serve.ROUTES)
    assert serve.POST_ROUTES <= set(serve.ROUTES)
    assert serve.TEXT_ROUTES == frozenset({"/run"}), (
        "a second text-bearing route is a decision, not an implementation "
        "detail: it widens what an agent on this machine can read")


@pytest.mark.parametrize("target", ["/", "/run?run=x&rev=r000", "/nope"])
def test_no_cookie_is_403_on_every_route(face, target):
    launched, _ctx, _run, _rev = face
    assert fetch(launched, "GET", target).status == 403


def test_a_wrong_token_is_403_and_the_right_one_becomes_a_cookie(face):
    """The token half of INV-23, both directions.

    The exchange is the point: the token arrives in the query, leaves in a
    host-only `SameSite=Strict` cookie, and the browser is sent to a bare `/` so
    the token is not left in the address bar, the history, or any log that
    records paths.
    """
    launched, _ctx, _run, _rev = face
    assert fetch(launched, "GET", "/?t=not-the-token").status == 403

    answer = fetch(launched, "GET", f"/?t={q(launched.token)}")
    assert answer.status == 303
    assert answer.headers["location"] == "/"
    cookie = answer.headers["set-cookie"]
    assert cookie.startswith(f"{serve.COOKIE}={launched.token};")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert "Domain=" not in cookie, "a host-only cookie, so it is this origin only"

    assert fetch(launched, "GET", "/", cookie=launched.token).status == 200


def test_it_binds_loopback_on_an_ephemeral_port_and_dies_with_the_process(face):
    launched, _ctx, _run, _rev = face
    host, port = launched.server.server_address[:2]
    assert host == "127.0.0.1"
    assert port != 0 and port > 1024
    assert launched.url.startswith(f"http://127.0.0.1:{port}/?t=")
    assert serve.Server.daemon_threads is True, (
        "a non-daemon handler thread can outlive the launcher, and INV-23 says "
        "this face exists only while the process that started it does")


def test_the_token_is_new_every_launch(tmp_path):
    """「每次啟動重新產生」 is a property of `start`, not of a stored file."""
    a = serve.start(verbs.Context(root=tmp_path / "a"))
    b = serve.start(verbs.Context(root=tmp_path / "b"))
    try:
        assert a.token != b.token
        assert len(a.token) >= 32
    finally:
        a.stop()
        b.stop()


def test_the_face_is_not_a_verb(face):
    """§16.0 (1): the launcher is a module, deliberately not `ltm serve`.

    `test_two_faces.py` asserts the parser's subcommands ARE the verb table, so
    a subcommand with no verb behind it would mean widening that assertion with
    a CLI-only allowlist -- the allowlist-growth shape A-8 refuses. Asserted here
    as well, from the other side: an agent cannot reach this face through the
    verb table because there is no entry to reach it by.
    """
    assert "serve" not in verbs.IMPLEMENTATIONS
    assert "serve" not in verbs.AGENT_VERBS
    parser = cli.build_parser()
    (subparsers,) = [a for a in parser._subparsers._group_actions  # noqa: SLF001
                     if hasattr(a, "choices")]
    assert "serve" not in subparsers.choices


# ==========================================================================
# F-2 -- the read model
# ==========================================================================

def _imports_the_writer(source: str) -> bool:
    """Does this source reach `store.write`, by any of the three spellings?"""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[-1] == "write" for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[-1] == "write":
                return True
            if (node.module or "").endswith("store"):
                if any(a.name == "write" for a in node.names):
                    return True
    return False


def test_the_surface_never_writes_to_the_store():
    """§16.0 (4). Asserted on the source, with both controls on the predicate.

    A checker that returned `False` for everything would make the first line
    green forever, so the three spellings of the mistake are fed to it and one
    legal import is fed to it too.
    """
    source = pathlib.Path(serve.__file__).read_text("utf-8")
    assert not _imports_the_writer(source)
    assert not hasattr(serve, "write")

    assert _imports_the_writer("from ..store import write\n")
    assert _imports_the_writer("from ltm.store.write import place_round\n")
    assert _imports_the_writer("import ltm.store.write\n")
    # ...and the module it legitimately does import must not be caught
    assert not _imports_the_writer("from ..store import layout\n")


def test_the_words_come_from_the_one_reader_and_nowhere_else(face, monkeypatch):
    """F-2's control: unplug `read.cues_of` and the words must be gone.

    If the page had its own way to reach the text -- parsing `全文/`, or
    re-reading `_plans/*.json` itself -- the words would survive this and the
    product would have two text readers with one rule between them.
    """
    launched, ctx, run, rev = face
    words = _texts_of(ctx, run, rev)[0]
    target = f"/run?run={q(run)}&rev={q(rev)}"

    before = fetch(launched, "GET", target, cookie=launched.token)
    assert words in before.body, "the fixture never showed the words at all"

    monkeypatch.setattr(read, "cues_of", lambda *a, **k: {})
    after = fetch(launched, "GET", target, cookie=launched.token)
    assert words not in after.body
    # ...and the absence is SAID, in the CLI's own wording rather than a second
    # one. Escaped, because everything this page writes goes through
    # `html.escape` -- including the sentence that says there is nothing to show.
    assert html.escape(cli.NO_TEXT) in after.body


def test_a_round_whose_record_cannot_be_read_says_so_on_the_page(face,
                                                                 monkeypatch):
    """BR-10 at the surface: a blank page is a defect, a stated failure is not."""
    launched, _ctx, run, rev = face

    def boom(*_a, **_k):
        raise read.ReadError("no plan record for this round: nowhere.json")

    monkeypatch.setattr(read, "cues_of", boom)
    answer = fetch(launched, "GET", f"/run?run={q(run)}&rev={q(rev)}",
                   cookie=launched.token)
    assert answer.status == 200
    assert "record could not be read" in answer.body
    assert "nowhere.json" in answer.body


# ==========================================================================
# F-3 -- screen 1, the runs
# ==========================================================================

def test_the_runs_page_lists_both_runs_of_a_two_run_store(tmp_path, monkeypatch):
    ctx, first, _rev = _decode(tmp_path, monkeypatch, dict(stub_decode.DECODE),
                               "one.m4a")
    _ctx2, second, _rev2 = _decode(
        tmp_path, monkeypatch, dict(stub_decode.DECODE), "two.m4a",
        root=ctx.root)
    assert first != second
    launched = serve.start(ctx)
    try:
        body = fetch(launched, "GET", "/", cookie=launched.token).body
        assert first in body and second in body
        assert "r000" in body
        # two different recordings: nothing claims they are the same audio
        assert "same audio" not in body
    finally:
        launched.stop()


def test_two_runs_holding_one_recording_say_so(tmp_path, monkeypatch):
    """I-19's shape on the runs screen, with its own negative control above.

    `intake` no longer MAKES this pair -- the same content resolves to the run
    that already holds it -- but stores written before that fix have them, and
    the real one measured on 2026-09-10 had two directories ending in
    `45a0cad2`. The page answers it from the ids themselves: the tail of a run
    id IS the content hash, so there is no second index to keep in step.
    """
    ctx, run, _rev = _decode(tmp_path, monkeypatch, dict(stub_decode.DECODE),
                             "one.m4a")
    digest = serve.content_hash(run)
    assert digest and len(digest) == layout.HASH_CHARS
    twin = f"renamed-{digest}"
    shutil.copytree(layout.run_dir(ctx.root, run),
                    layout.run_dir(ctx.root, twin))

    launched = serve.start(ctx)
    try:
        body = fetch(launched, "GET", "/", cookie=launched.token).body
        assert run in body and twin in body
        assert body.count("same audio as 1 other run") == 2
    finally:
        launched.stop()


def test_a_run_id_that_is_not_one_does_not_take_the_page_down(tmp_path):
    """A stray directory is not a reason for the runs list to fail."""
    assert serve.content_hash("talk-0badc0de") == "0badc0de"
    assert serve.content_hash("not-a-run-id") is None


# ==========================================================================
# F-4 -- screen 2: trust and findings in ONE view
# ==========================================================================

def test_an_undetermined_round_shows_its_agreement_and_its_findings_at_once(
        undetermined):
    """§16.1 F-4, and §16.2's 「絕不放棄」 second half.

    「這份錄音整體可不可信」 is `show`'s stretch table and 「哪一句是錯的」 is
    `plan`'s findings; measured on three real recordings, a reader had to run two
    commands and join them in their head (`first-run-2026-09-10.md` §4 item 6).
    **One response, both answers.**
    """
    launched, ctx, run, rev = undetermined
    answer = fetch(launched, "GET", f"/run?run={q(run)}&rev={q(rev)}",
                   cookie=launched.token)
    assert answer.status == 200
    body = answer.body

    # ...the trust half: the label and the number behind it
    assert layout.UNDETERMINED in body
    assert "0.000" in body
    assert "could not be labelled" in body

    # ...the findings half: the words, and a timecode to go and listen at
    plan = verbs.plan(ctx, run=run, rev=rev)
    assert plan.findings, "the fixture has no findings, so this asserts nothing"
    cues = read.cues_of(ctx.root, run, rev)
    for ref in plan.findings:
        piece = read.excerpt(cues, str(ref.cue_id), ref.char_start, ref.char_end)
        assert piece is not None
        assert piece.span[:20] in body
        assert read.clock(piece.start) in body


def test_a_silent_round_says_nobody_spoke_not_that_the_signals_disagreed(
        tmp_path, monkeypatch):
    """I-22 on the page. A round with no words has one `undetermined` stretch.

    The trust card used to explain every `undetermined` stretch the same way --
    「the words and the audio disagreed」 -- which over a silent recording is a
    confident wrong answer: there were no words to disagree. **Control**: the test
    above, whose round DOES have words that disagree with the audio and must still
    get that sentence.
    """
    silent = {"segments": 0, "gap": 0.01, "vad_kept": 0.0,
              "slot_langs": ["en", "nn", "en"]}
    ctx, run, rev = _decode(tmp_path, monkeypatch, silent, "quiet.m4a")
    launched = serve.start(ctx)
    try:
        answer = fetch(launched, "GET", f"/run?run={q(run)}&rev={q(rev)}",
                       cookie=launched.token)
    finally:
        launched.stop()
    assert answer.status == 200
    assert "found no speech" in answer.body
    assert "could not be labelled" not in answer.body
    assert "0 cues, 1 stretches" in answer.body


def test_a_sparse_cue_finding_says_what_it_means_on_the_page(tmp_path,
                                                             monkeypatch):
    """I-23 on the page: the finding carries the CLI's sentence, not only numbers.

    **Control**: the standard `face` round has no `sparse-cue`, and the sentence
    must be absent there -- a line printed under every finding is wallpaper.
    """
    stub = {"segments": 2, "gap": 0.01, "seg_seconds": 20.0,
            "texts": ["嗯", "對對"], "slot_langs": ["zh", "zh", "zh"]}
    ctx, run, rev = _decode(tmp_path, monkeypatch, stub, "sparse.m4a")
    launched = serve.start(ctx)
    try:
        answer = fetch(launched, "GET", f"/run?run={q(run)}&rev={q(rev)}",
                       cookie=launched.token)
    finally:
        launched.stop()
    sentence = html.escape(cli.FINDING_SENTENCE["sparse-cue"])
    assert answer.body.count(sentence) == 2


def test_the_sparse_sentence_is_absent_where_nothing_is_sparse(face):
    launched, ctx, run, rev = face
    answer = fetch(launched, "GET", f"/run?run={q(run)}&rev={q(rev)}",
                   cookie=launched.token)
    assert answer.status == 200
    assert html.escape(cli.FINDING_SENTENCE["sparse-cue"]) not in answer.body


def test_the_two_halves_really_do_come_from_two_places(undetermined):
    """The control for the test above: it is a JOIN, not one response rendered.

    If `plan` already carried the agreement, or the stretch reader already
    carried the findings, F-4 would be describing something that was never
    split. It is split: the verdicts travel in `PlanResponse`, which has no
    language field at all (INV-12 keeps it to spans and codes), and the language
    measurement is read off the round's own record.
    """
    _launched, ctx, run, rev = undetermined
    plan = verbs.plan(ctx, run=run, rev=rev)
    assert not hasattr(plan, "stretches") and not hasattr(plan, "languages")
    stretches = read.stretches_of(ctx.root, run, rev)
    assert stretches and plan.findings
    assert {"language", "agreement", "start", "end"} <= set(stretches[0])


def test_the_round_record_and_the_show_verb_do_not_disagree(face):
    """Two writers describe one decode; they must say the same thing.

    `show` reads `_origin.json`'s per-revision decode provenance and the page
    reads `recording.stretches` out of the plan record. For a DECODE round both
    exist, so this is the guard on the only place they could drift -- and it is
    a real failure mode, because they are written by two different functions in
    the same call.
    """
    _launched, ctx, run, rev = face
    show = verbs.show(ctx, run=run, rev=rev)
    stretches = read.stretches_of(ctx.root, run, rev)
    assert len(stretches) == len(show.stretches) > 0
    for mine, theirs, lang in zip(stretches, show.stretches, show.languages):
        assert float(mine["start"]) == theirs["start"]
        assert float(mine["end"]) == theirs["end"]
        assert float(mine["agreement"]) == theirs["agreement"]
        assert str(mine["language"]) == str(lang)


def test_a_detect_round_still_answers_the_trust_question(face):
    """The defect the real store showed, kept as a case that must not come back.

    Measured 2026-09-11 against a sandbox copy of the real store: the run a
    reader is most likely to open sits at `r012`, a **detect** round, and
    `verbs.show` returns an EMPTY stretch table for it -- there is no decode
    provenance for a revision that was not a decode. A page that asked `show`
    for the trust half therefore went blank on exactly the revision whose
    findings the reader was reading, which is F-4 failing on the only data that
    matters.

    Positive control: `show` really is empty here, or this test is asserting
    against a case that does not exist. Negative control: the page shows the
    agreement number anyway, because it reads the round's own record.
    """
    launched, ctx, run, rev = face
    detected = verbs.detect(ctx, run=run, rev=rev)
    assert detected.rev != rev
    assert verbs.show(ctx, run=run, rev=detected.rev).stretches == (), (
        "the premise is gone: `show` now answers for a detect round, so this "
        "test no longer reproduces what was measured")

    body = fetch(launched, "GET", f"/run?run={q(run)}&rev={q(detected.rev)}",
                 cookie=launched.token).body
    assert "is this trustworthy?" in body
    stretches = read.stretches_of(ctx.root, run, detected.rev)
    assert stretches
    for s in stretches:
        assert f'{float(s["agreement"]):.3f}' in body


def test_a_cjk_run_id_with_a_space_survives_the_round_trip(tmp_path,
                                                           monkeypatch):
    """A run id is `<the user's filename>-<hash8>`, so it can be anything."""
    ctx, run, rev = _decode(tmp_path, monkeypatch, dict(stub_decode.DECODE),
                            "訪談 2026.m4a")
    launched = serve.start(ctx)
    try:
        listing = fetch(launched, "GET", "/", cookie=launched.token).body
        assert "訪談 2026" in listing
        answer = fetch(launched, "GET", f"/run?run={q(run)}",
                       cookie=launched.token)
        assert answer.status == 200
        assert run in answer.body and rev in answer.body
    finally:
        launched.stop()


def test_asking_for_a_run_that_is_not_there_is_a_stated_refusal(face):
    launched, _ctx, _run, _rev = face
    answer = fetch(launched, "GET", "/run?run=nope-0badc0de",
                   cookie=launched.token)
    assert answer.status == 404
    assert "no-such-run" in answer.body


# ==========================================================================
# F-5 -- screen 3: decide
# ==========================================================================

def _journal(ctx, run, rev) -> list[dict]:
    path = layout.run_dir(ctx.root, run) / layout.EDITS / f"{rev}.json"
    return json.loads(path.read_text("utf-8"))["journal"]


def test_a_ticked_subset_becomes_exactly_those_ops_and_no_others(face):
    """F-5's positive half: the index on the page IS `accept --op N`.

    Two of twelve, chosen for being non-adjacent, so an off-by-one or an
    accept-all would both be visible in the journal rather than plausible.
    """
    launched, ctx, run, rev = face
    plan = verbs.plan(ctx, run=run, rev=rev)
    assert len(plan.ops) >= 5, len(plan.ops)
    chosen = (1, 4)

    answer = fetch(launched, "POST", "/adopt", cookie=launched.token,
                   body=f"run={q(run)}&rev={q(rev)}&op=1&op=4")
    assert answer.status == 303
    assert answer.headers["location"] == f"/run?run={q(run)}&rev={rev}", (
        "the redirect must carry BOTH parameters: an html-escaped `&` here "
        "would send the reader to the run's newest round instead of the one "
        "they just decided on")

    new_rev = layout.serial(layout.serial_number(rev) + 1)
    journal = _journal(ctx, run, new_rev)
    assert [row["cue_id"] for row in journal] == [
        str(plan.ops[i].cue_id) for i in chosen]
    assert len(journal) == 2, "an empty tick list would have applied all twelve"


def test_ticking_nothing_declines_and_is_not_a_no_op(face):
    """F-5's other half, and it is the one that is easy to get wrong.

    `accept` selects by index and an EMPTY `ops` means 「all of them」, so a face
    that passed the empty tick list straight through would delete everything the
    detector proposed. `cli.DECLINE` is the spelling both faces use for 「none」,
    and §7 case (3) says the declined round still consumes a serial: 「I looked
    and decided not to」 must not look like 「I never ran」.
    """
    launched, ctx, run, rev = face
    before = verbs.plan(ctx, run=run, rev=rev)
    assert before.ops, "nothing was proposed, so declining proves nothing"

    answer = fetch(launched, "POST", "/adopt", cookie=launched.token,
                   body=f"run={q(run)}&rev={q(rev)}")
    assert answer.status == 303

    new_rev = layout.serial(layout.serial_number(rev) + 1)
    run_dir = layout.run_dir(ctx.root, run)
    assert not (run_dir / layout.EDITS / f"{new_rev}.json").exists(), (
        "an empty selection wrote an edit script, so it was read as "
        "'accept everything' -- the failure this test exists for")
    assert (run_dir / layout.PLANS / f"{new_rev}.json").exists(), (
        "the declined round left nothing on disk, so it looks like a run that "
        "never happened")
    assert cli.DECLINE == (-1,)
    high = next(r.high_water for r in verbs.runs(ctx).runs if r.run == run)
    assert int(high) == layout.serial_number(new_rev)


def test_the_page_says_which_of_the_two_it_is_about_to_do(face):
    launched, _ctx, run, rev = face
    body = fetch(launched, "GET", f"/run?run={q(run)}&rev={q(rev)}",
                 cookie=launched.token).body
    assert html.escape(serve.ADOPT_HINT) in body
    assert serve.ADOPT_BUTTON in body
    assert "DECLINE" in body
    assert 'name="op" value="0"' in body


def test_what_was_decided_is_reported_on_the_next_page(face):
    """The redirect lands somewhere that says what happened, not silently."""
    launched, _ctx, run, rev = face
    fetch(launched, "POST", "/adopt", cookie=launched.token,
          body=f"run={q(run)}&rev={q(rev)}&op=0")
    body = fetch(launched, "GET", f"/run?run={q(run)}&rev={q(rev)}",
                 cookie=launched.token).body
    assert "1 edit(s) applied" in body
    # ...and it is a flash, not a permanent claim: the next view is clean
    again = fetch(launched, "GET", f"/run?run={q(run)}&rev={q(rev)}",
                  cookie=launched.token).body
    assert "1 edit(s) applied" not in again


def test_a_get_cannot_change_anything(face):
    """A state change may not arrive by a link somebody followed."""
    launched, ctx, run, rev = face
    assert fetch(launched, "GET", f"/adopt?run={q(run)}&rev={q(rev)}",
                 cookie=launched.token).status == 405
    high = next(r.high_water for r in verbs.runs(ctx).runs if r.run == run)
    assert int(high) == layout.serial_number(rev)


def test_the_newest_round_with_a_plan_is_what_a_bare_run_link_opens(face):
    """After an edit round the high-water revision has no verdicts of its own.

    So `/run?run=...` with no revision must land on the newest round that HAS a
    plan, not on the edit round -- which would be a dead end reached by the most
    ordinary click on the page.
    """
    launched, ctx, run, rev = face
    fetch(launched, "POST", "/adopt", cookie=launched.token,
          body=f"run={q(run)}&rev={q(rev)}&op=0")
    new_rev = layout.serial(layout.serial_number(rev) + 1)
    with pytest.raises(schema.AgentError):
        verbs.plan(ctx, run=run, rev=new_rev)      # the premise of this test

    answer = fetch(launched, "GET", f"/run?run={q(run)}", cookie=launched.token)
    assert answer.status == 200
    assert f"revision {rev}" in answer.body
    assert f"has gone on to {new_rev}" in answer.body


# ==========================================================================
# F-6 -- the launcher
# ==========================================================================

def test_the_server_serves_and_the_port_is_gone_when_it_stops(face):
    """Start, serve for real in a thread, answer, stop, and check the socket.

    The request goes through the handler class over an in-memory connection (see
    the module docstring); the SOCKET is real, and its closure is asserted on the
    real object rather than inferred.
    """
    launched, _ctx, _run, _rev = face
    thread = threading.Thread(target=launched.serve_forever, daemon=True)
    thread.start()
    assert launched.serving.wait(5.0)
    assert launched.server.socket.fileno() != -1

    assert fetch(launched, "GET", "/", cookie=launched.token).status == 200

    launched.stop()
    thread.join(5.0)
    assert not thread.is_alive()
    assert launched.server.socket.fileno() == -1, "the port is still held"


def test_a_closed_tab_is_quiet_and_a_real_fault_is_not(face, capsys):
    """Both controls on the one thing `handle_error` is allowed to swallow.

    Known-FALSE: a client hanging up is `ConnectionError`, and must print
    nothing -- measured against the real server, where the default handler put a
    forty-line traceback on the terminal for a closed tab. Known-TRUE: anything
    else keeps the full report, or this is not a filter, it is a mute button.
    """
    launched, _ctx, _run, _rev = face
    capsys.readouterr()

    try:
        raise ConnectionResetError(10054, "the reader closed the tab")
    except ConnectionResetError:
        launched.server.handle_error(None, (serve.HOST, 1))
    assert capsys.readouterr().err == ""

    try:
        raise RuntimeError("the maintainer needs to see this one")
    except RuntimeError:
        launched.server.handle_error(None, (serve.HOST, 1))
    err = capsys.readouterr().err
    assert "RuntimeError" in err
    assert "the maintainer needs to see this one" in err


def test_stopping_a_server_that_never_served_does_not_hang(tmp_path):
    """The deadlock this was found by hitting: `shutdown()` waits on an event
    that only `serve_forever` sets, so calling it on a bound-but-never-served
    server blocks the caller forever. Every test above ends in this path."""
    launched = serve.start(verbs.Context(root=tmp_path))
    assert not launched.serving.is_set()
    launched.stop()
    assert launched.server.socket.fileno() == -1


def test_the_launcher_prints_a_loopback_url_with_a_token(tmp_path, monkeypatch,
                                                         capsys):
    """`python -m ltm.ui.serve`, both ways round on the browser.

    `--no-browser` must not open one and the default must, or the flag is
    decoration and the acceptance item behind it cannot fail.
    """
    opened: list[str] = []
    monkeypatch.setattr(serve.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(serve.Launched, "serve_forever", lambda self: None)

    assert serve.main(["--root", str(tmp_path), "--no-browser"]) == 0
    printed = capsys.readouterr().out
    match = re.search(r"http://127\.0\.0\.1:(\d+)/\?t=([A-Za-z0-9_-]{22,})",
                      printed)
    assert match, printed
    assert int(match.group(1)) not in (0, 80, 443)
    assert str(tmp_path) in printed
    assert opened == []

    assert serve.main(["--root", str(tmp_path)]) == 0
    assert len(opened) == 1 and opened[0].startswith("http://127.0.0.1:")
    assert opened[0] != match.group(0), "the token must be new every launch"


def test_ctrl_c_stops_it_and_says_so_and_lets_go_of_the_port(tmp_path,
                                                             monkeypatch,
                                                             capsys):
    """The exit path a user actually takes, and the sentence they will read.

    Ctrl-C in a console is `KeyboardInterrupt`, so the launcher's handler is
    what runs -- and the port has to be released on the way out, or the next
    launch is answered by a corpse. Both are asserted rather than described,
    because the UAT item quotes the sentence.
    """
    closed: list[bool] = []
    real_stop = serve.Launched.stop

    def boom(self):
        raise KeyboardInterrupt

    def watch(self):
        real_stop(self)
        closed.append(self.server.socket.fileno() == -1)

    monkeypatch.setattr(serve.webbrowser, "open", lambda url: None)
    monkeypatch.setattr(serve.Launched, "serve_forever", boom)
    monkeypatch.setattr(serve.Launched, "stop", watch)

    assert serve.main(["--root", str(tmp_path), "--no-browser"]) == 0
    assert "stopped; the port is gone" in capsys.readouterr().out
    assert closed == [True], "the socket was still open after Ctrl-C"


class _Sink(io.RawIOBase):
    """A byte sink that records only what a buffer actually handed to the OS."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def writable(self) -> bool:
        return True

    def write(self, data) -> int:               # type: ignore[override]
        self.chunks.append(bytes(data))
        return len(data)


def _buffered() -> tuple[_Sink, io.TextIOWrapper]:
    sink = _Sink()
    return sink, io.TextIOWrapper(io.BufferedWriter(sink, buffer_size=8192),
                                  encoding="utf-8")


def test_the_address_reaches_the_reader_before_the_server_blocks(tmp_path,
                                                                 monkeypatch):
    """Found by running it: redirect the launcher's output and the file is empty.

    Python block-buffers stdout the moment it is not a terminal, so `print(url)`
    followed by `serve_forever()` puts the one thing this process exists to say
    into an 8 KB buffer that is flushed when the process ends -- which is exactly
    when the address stops being any use.

    The known-FALSE control is the same buffered stream written to and not
    flushed: if the sink saw those bytes anyway, this harness could not tell a
    flushed launcher from an unflushed one and the assertion above would be
    decoration.
    """
    control_sink, control = _buffered()
    control.write("http://127.0.0.1:1/?t=x\n")
    assert control_sink.chunks == [], (
        "a small write reached the sink without a flush, so this test cannot "
        "distinguish the defect from the repair")

    sink, stream = _buffered()
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(serve.webbrowser, "open", lambda url: None)
    seen: dict[str, bytes] = {}
    monkeypatch.setattr(
        serve.Launched, "serve_forever",
        lambda self: seen.setdefault("at-block", b"".join(sink.chunks)))

    serve.main(["--root", str(tmp_path), "--no-browser"])
    assert b"http://127.0.0.1:" in seen["at-block"], (
        "the address was still in a buffer when the server started blocking")


# The engine's own first-window guess. faster-whisper fills
# `TranscriptionInfo.language` BEFORE the decode loop, from a single 30-second
# window (`language_detection_segments=1`), and the per-window detections never
# write back to it. It is therefore a confident wrong answer on exactly the
# recordings this product exists for -- measured 2026-09-11 in
# docs/multilingual-verdict-2026-09-11.md §4.1: one recording's transcript went
# from 88 to 7,290 Chinese characters while this field stayed `en (0.821)`.
ENGINE_FIRST_WINDOW_FIELD = "language_detected"


def _names_the_engines_guess(source: str) -> bool:
    return ENGINE_FIRST_WINDOW_FIELD in source


def test_the_face_never_shows_the_engines_first_window_language():
    """A screen may say a language only from the audio-side stretch table.

    This is a property of the FILES, not a reminder to whoever writes the next
    screen: `_origin.json` carries the engine's guess, it reads like the answer,
    and reaching for it is one keystroke cheaper than reading
    `recording.stretches`. The honest source stays honest under the multilingual
    flag; this one does not.

    Two-sided, because a scanner that fires on nothing proves nothing: the
    positive control is a line that DOES name the field (it must be caught), the
    negative one is a line about the audio-side table (it must not be), and the
    face is then required to still show a language at all -- otherwise a page
    that had simply dropped the subject would pass for a truthful one.
    """
    assert _names_the_engines_guess(
        'lang = origin["decode"]["r000"]["language_detected"]'), (
        "the scanner does not catch the field it exists to catch")
    assert not _names_the_engines_guess(
        'stretch["language"], and the agreement detected for it'), (
        "the scanner fires on any mention of a language, so a clean scan below "
        "would mean nothing")

    face = sorted(pathlib.Path(serve.__file__).resolve().parent.glob("*.py"))
    assert len(face) >= 4, f"the face's package went missing: {face}"
    named = [p.name for p in face
             if _names_the_engines_guess(p.read_text(encoding="utf-8"))]
    assert named == [], (
        f"{named} shows the engine's first-window guess; the screen-facing "
        f"answer is recording.stretches[].language")

    served = pathlib.Path(serve.__file__).read_text(encoding="utf-8")
    assert "stretches_of" in served, (
        "the face stopped saying a language at all, which is not what this "
        "test is asking for")
