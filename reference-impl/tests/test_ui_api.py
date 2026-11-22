"""The four JSON routes. PSM section 19, cards GUI-2 and GUI-3.

The face was read-only from the day it was built until 2026-09-16; these routes
are the first write path in it. Two properties are worth more than every status
code below:

* **none of them is in `TEXT_ROUTES`, and the two lists may not intersect.**
  INV-23 names 「多開一條 JSON 路由比較好接」 as the change nobody blocks at
  review, and this increment adds four JSON routes at once. `test_ui_serve.py`
  walks the table for leaks; this file asserts the membership question the walk
  cannot ask.
* **`/api/cancel` never reports `cancelled`.** It reports that the request was
  made. The answer belongs to the child and arrives through `/api/progress`.

The decode here is a FAKE child, for the reason card GUI-1 gives: what these
routes do is route, and a real engine would take four minutes to prove HTTP.
"""
from __future__ import annotations

import json
import sys
import time

import pytest

from ltm.agentapi import verbs
from ltm.ui import jobs as jobs_module
from ltm.ui import serve, sources
# `q` and not `urllib.parse.urlencode`: `tests/test_layering.py` bans a network
# import in a shipping file and a test file is one, so `test_ui_serve.py` wrote
# its own percent-encoder for exactly this. Importing `urllib` here turned the
# layering gate red -- which is the gate working, on its first real chance.
from test_ui_serve import fetch, q                   # noqa: E402  (same suite)

#: A child that writes a progress stream and watches the flag. One line per
#: event, because this file is about the routes and not about parsing.
STUB = r'''
import json, pathlib, sys, time
progress, cancel, steps = sys.argv[1], sys.argv[2], int(sys.argv[3])
p = pathlib.Path(progress)
p.parent.mkdir(parents=True, exist_ok=True)
c = pathlib.Path(cancel)
for i in range(steps):
    with p.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({
            "state": "decoding", "phase": "decode", "elapsed": round(i * 0.05, 3),
            "done": float(i), "total": float(steps), "unit": "audio-seconds",
            "name": None, "detail": {}}) + "\n")
    if c.exists():
        print(json.dumps({"verb": "decode", "code": "cancelled",
                          "subject": "decoding"}))
        sys.exit(2)
    time.sleep(0.05)
print(json.dumps({"verb": "decode", "run": "x", "cues": 3}))
'''


def stub_argv(steps: int = 60):
    def build(*, root, run, options, progress, cancel, python=None):  # noqa: ARG001
        return [sys.executable, "-c", STUB, str(progress), str(cancel),
                str(steps)]
    return build


@pytest.fixture()
def api(tmp_path):
    """A launched face that can start decodes, with a fake child and one file."""
    root = tmp_path / "store"
    media = tmp_path / "media"
    media.mkdir(parents=True)
    source = media / "talk.m4a"
    source.write_bytes(b"\0" * 4096)

    launched = serve.start(verbs.Context(root=root), audio_roots=(media,))
    launched.session.jobs = jobs_module.Jobs(
        root, argv_for=stub_argv(), workspace_root=tmp_path / "work")
    try:
        yield launched, source
    finally:
        launched.stop()


def _form(fields: dict) -> str:
    return "&".join(f"{q(k)}={q(str(v))}" for k, v in fields.items())


def get(launched, route: str, **params):
    query = _form(params)
    answer = fetch(launched, "GET", f"{route}?{query}" if query else route,
                   cookie=launched.token)
    return answer.status, json.loads(answer.body)


def poll(launched, job_id: str, ready, timeout: float = 30.0):
    """Poll `/api/progress` the way the page will, and return the record.

    `ready` is given the record. Note the shape the first version got wrong:
    `if body.get("done")` is False for a first event of `0.0`, which is a real
    number the page must draw -- the difference between "no progress yet" and
    "no progress so far" is exactly the one this product keeps insisting on. And
    without a sleep the loop spent its whole budget before the child wrote a
    line, then reported "no progress reached the page".
    """
    deadline = time.monotonic() + timeout
    body: dict = {}
    while time.monotonic() < deadline:
        status, body = get(launched, "/api/progress", job=job_id)
        assert status == 200, body
        if ready(body):
            return body
        time.sleep(0.05)
    return body


def post(launched, route: str, **fields):
    # `route` and not `path`: `path` is a FORM FIELD these routes take, and the
    # first version of this helper collided with it -- `post(launched,
    # "/api/decode", path=...)` raised `TypeError` before a request was made.
    answer = fetch(launched, "POST", route, cookie=launched.token,
                   body=_form(fields))
    return answer.status, json.loads(answer.body)


# --------------------------------------------------------------------------
# the lists
# --------------------------------------------------------------------------
def test_no_json_route_is_a_text_route():
    """The membership question the leak walk cannot ask.

    The walk proves no route RETURNED text for one fixture. This proves the
    lists themselves cannot overlap, which is the thing a future commit would
    change -- and the assertion is in the module too, so the import fails rather
    than the test.
    """
    assert serve.JSON_ROUTES, "an empty list would make this pass for nothing"
    assert not (serve.JSON_ROUTES & serve.TEXT_ROUTES)
    assert serve.JSON_ROUTES <= set(serve.ROUTES)
    assert serve.TEXT_ROUTES == frozenset({"/run"})


def test_the_two_write_routes_are_post_only(api):
    """A state change must not arrive by a link somebody followed."""
    launched, _ = api
    for path in ("/api/decode", "/api/cancel"):
        assert path in serve.POST_ROUTES
        answer = fetch(launched, "GET", path, cookie=launched.token)
        assert answer.status == 405, path
    for path in ("/api/intake", "/api/progress"):
        assert path not in serve.POST_ROUTES


@pytest.mark.parametrize("path", sorted(serve.JSON_ROUTES))
def test_every_json_route_is_403_without_the_cookie(api, path):
    """INV-23's token half covers the new routes too, not just the pages."""
    launched, _ = api
    method = "POST" if path in serve.POST_ROUTES else "GET"
    assert fetch(launched, method, path, body="").status == 403


@pytest.mark.parametrize("path", sorted(serve.JSON_ROUTES))
def test_every_json_route_answers_json(api, path):
    """Including the refusals. A caller that has to tell a sentence from a
    record by looking at it has no machine-readable failure channel (A-16)."""
    launched, _ = api
    method = "POST" if path in serve.POST_ROUTES else "GET"
    answer = fetch(launched, method, path, cookie=launched.token, body="")
    assert answer.headers["content-type"].startswith("application/json")
    json.loads(answer.body)


# --------------------------------------------------------------------------
# intake: paths, never an upload
# --------------------------------------------------------------------------
def test_intake_lists_what_may_be_decoded(api):
    launched, source = api
    status, body = get(launched, "/api/intake")
    assert status == 200
    assert [row["path"] for row in body["sources"]] == [str(source.resolve())]
    assert body["audioRoots"] == [str(source.parent)]


def test_a_read_only_launch_refuses_all_four(tmp_path):
    """The face as it was for its first week is still a legal configuration."""
    launched = serve.start(verbs.Context(root=tmp_path / "store"), decodes=False)
    try:
        for path in sorted(serve.JSON_ROUTES):
            method = "POST" if path in serve.POST_ROUTES else "GET"
            answer = fetch(launched, method, path, cookie=launched.token, body="")
            assert answer.status == 503, path
            assert json.loads(answer.body)["code"] == "not-configured"
    finally:
        launched.stop()


# --------------------------------------------------------------------------
# decode: the allowlist, and the one-at-a-time rule
# --------------------------------------------------------------------------
def test_a_path_that_was_never_offered_is_refused_and_starts_nothing(api):
    """The security surface, at the route rather than in the catalogue.

    The refusal says `not-offered` and nothing about the filesystem -- and the
    assertion that matters is the second one: no child was started.
    """
    launched, source = api
    outside = source.parent.parent / "outside.m4a"
    outside.write_bytes(b"\0" * 10)

    status, body = post(launched, "/api/decode", path=str(outside))
    assert status == 403
    assert body["code"] == sources.NotOffered.code
    assert launched.session.jobs.running() is None


def test_a_missing_path_is_a_bad_argument_not_a_crash(api):
    launched, _ = api
    status, body = post(launched, "/api/decode")
    assert status == 400 and body["subject"] == "path"
    assert launched.session.jobs.running() is None


def test_a_listed_path_starts_a_job_and_registers_the_run(api):
    """The positive control for everything above.

    A route that refused every path would pass both refusal tests and leave the
    page unable to decode anything.
    """
    launched, source = api
    status, body = post(launched, "/api/decode", path=str(source))
    assert status == 200, body
    assert body["jobId"] and body["run"].startswith("talk-")

    # `intake` really ran: the store now holds a pointer at that recording
    listing = get(launched, "/api/intake")[1]
    assert listing["sources"][0]["run"] == body["run"]


def test_a_second_decode_is_refused_with_the_running_id(api):
    """Refused, not queued, and the id is in the answer (§19.0 (1))."""
    launched, source = api
    first = post(launched, "/api/decode", path=str(source))[1]["jobId"]

    status, body = post(launched, "/api/decode", path=str(source))
    assert status == 409
    assert body["code"] == "busy" and body["jobId"] == first


# --------------------------------------------------------------------------
# progress and cancel
# --------------------------------------------------------------------------
def test_progress_moves_and_carries_its_denominator(api):
    launched, source = api
    job_id = post(launched, "/api/decode", path=str(source))[1]["jobId"]

    seen = poll(launched, job_id, lambda b: b.get("done") is not None)
    assert seen["done"] is not None, "no progress reached the page"
    assert seen["total"] == 60.0 and seen["unit"] == "audio-seconds"
    assert seen["state"] in ("running", "done")

    moved = poll(launched, job_id, lambda b: (b.get("done") or 0) > seen["done"])
    assert (moved.get("done") or 0) > seen["done"], (
        "the numerator never moved, so the page would be drawing a spinner "
        "with a number painted on it (R13)")


def test_an_unknown_job_is_a_code_and_not_a_stuck_bar(api):
    """A job id from a previous launch is the ordinary way to get here."""
    launched, _ = api
    status, body = get(launched, "/api/progress", job="deadbeefcafe")
    assert status == 404 and body["code"] == "no-such-job"


def test_cancel_reports_the_request_and_the_child_reports_the_outcome(api):
    """The distinction R13 exists for, kept at the HTTP layer too.

    `/api/cancel` answers `cancelling`; `cancelled` arrives later, from
    `/api/progress`, because the child said so. A cancel route that answered
    `cancelled` would be the page telling the reader something no one has
    verified yet.
    """
    launched, source = api
    job_id = post(launched, "/api/decode", path=str(source))[1]["jobId"]
    poll(launched, job_id, lambda b: (b.get("done") or 0) > 0)

    status, asked = post(launched, "/api/cancel", job=job_id)
    assert status == 200
    assert asked["cancelRequested"] is True
    assert asked["state"] == "cancelling", (
        "the cancel route decided an outcome that is the child's to report")

    final = poll(launched, job_id,
                 lambda b: b["state"] in ("cancelled", "done", "failed"))
    assert final["state"] == "cancelled", final
    assert final["code"] == "cancelled"


def test_cancelling_an_unknown_job_is_a_code(api):
    launched, _ = api
    status, body = post(launched, "/api/cancel", job="deadbeefcafe")
    assert status == 404 and body["code"] == "no-such-job"


def test_stopping_the_server_asks_the_job_to_stop(tmp_path):
    """§19.0 (6): a job dies with the server, by request and not by kill.

    A parent that killed the child would take away the thing section 8.2
    requires -- the child discarding its own partial output -- so the flag is
    written and the child stops itself.
    """
    root = tmp_path / "store"
    media = tmp_path / "media"
    media.mkdir(parents=True)
    source = media / "talk.m4a"
    source.write_bytes(b"\0" * 4096)

    launched = serve.start(verbs.Context(root=root), audio_roots=(media,))
    launched.session.jobs = jobs_module.Jobs(
        root, argv_for=stub_argv(), workspace_root=tmp_path / "work")
    job_id = post(launched, "/api/decode", path=str(source))[1]["jobId"]
    job = launched.session.jobs._job(job_id)            # noqa: SLF001

    launched.stop()
    assert job.cancel_path.is_file(), "the server left a child nobody can reach"
    assert job.cancel_requested is True


# ==========================================================================
# GUI-5 -- a failure the page can name, all the way from the child
# ==========================================================================
def dying_argv(code: int = 3, quiet: bool = True):
    """A child that ends badly. `quiet` is the harsher case: it says nothing."""
    say = "" if quiet else r'print("{\"verb\": \"decode\", \"code\": \"boom\"}")'
    body = f"{say}\nimport sys; sys.exit({code})"
    def build(*, root, run, options, progress, cancel, python=None):  # noqa: ARG001
        return [sys.executable, "-c", body]
    return build


@pytest.mark.parametrize("quiet,expected_code", [(True, jobs_module.NO_ANSWER),
                                                 (False, "boom")])
def test_a_dead_child_reaches_the_page_as_a_named_failure(tmp_path, quiet,
                                                          expected_code):
    """Card GUI-5 through the route the page actually polls.

    The job table has its own tests for a child that dies; this one is about
    what comes out of `/api/progress`, because that is the record the panel
    renders and the panel can only say what reaches it. Two children: one that
    exits with a code and no words at all -- the worst case, because there is
    nothing to explain -- and one that names a refusal on its way out.

    `state` is what the page turns into a sentence (`serve.DECODE_FAULTS`), and
    `code` plus `exitCode` are what it appends. The assertion is that all three
    are there, because a `failed` with two nulls beside it is a bar that stopped
    with nothing to say.
    """
    root = tmp_path / "store"
    media = tmp_path / "media"
    media.mkdir(parents=True)
    source = media / "talk.m4a"
    source.write_bytes(b"\0" * 4096)

    launched = serve.start(verbs.Context(root=root), audio_roots=(media,))
    launched.session.jobs = jobs_module.Jobs(
        root, argv_for=dying_argv(3, quiet), workspace_root=tmp_path / "work")
    try:
        job_id = post(launched, "/api/decode", path=str(source))[1]["jobId"]
        body = poll(launched, job_id, lambda b: b["state"] != "running")
    finally:
        launched.stop()

    assert body["state"] == "failed"
    assert body["code"] == expected_code
    assert body["exitCode"] == 3
    assert body["state"] in serve.DECODE_FAULTS, (
        "the page has no sentence for the state this job ended in")


def test_a_failure_is_never_reported_as_a_finished_decode(tmp_path):
    """The control for the pair above: a child that succeeds must NOT be failed.

    Without it, a `status` that returned `failed` for everything would satisfy
    every assertion in this section -- and a page that showed the failure
    sentence on every decode would be exactly as useless as one that never
    showed it.
    """
    root = tmp_path / "store"
    media = tmp_path / "media"
    media.mkdir(parents=True)
    source = media / "talk.m4a"
    source.write_bytes(b"\0" * 4096)

    launched = serve.start(verbs.Context(root=root), audio_roots=(media,))
    launched.session.jobs = jobs_module.Jobs(
        root, argv_for=stub_argv(steps=2), workspace_root=tmp_path / "work")
    try:
        job_id = post(launched, "/api/decode", path=str(source))[1]["jobId"]
        body = poll(launched, job_id, lambda b: b["state"] != "running")
    finally:
        launched.stop()

    assert body["state"] == "done"
    assert body["exitCode"] == 0
