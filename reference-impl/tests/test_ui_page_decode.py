"""The panel a person actually uses. PSM section 19, card GUI-4.

Two kinds of claim live here and only one of them belongs in pytest.

**What the SERVER emitted** -- the panel's structure, its words, which controls
start disabled, and the properties that keep the page and the product from
drifting apart -- is read out of the HTML this file asks for. That is the
emitted artifact, which is what a gate is supposed to read.

**What the BROWSER does with it** -- that the start control is disabled while a
decode runs and that the progress text changes between two polls -- is not in
this file, because nothing here executes JavaScript and a test that asserted it
from the source would be asserting that the code says what it says. The card
names a DOM-level run through the browser pane for exactly that half, and
`docs/uat-2026-09-09.md` carries what it measured.

The most useful tests below are the ones generated FROM the script rather than
written beside it: every `getElementById` the script performs must find
something, and every state word it branches on must be a state the job table can
actually produce. Both scale with the page instead of certifying the handful of
cases their author thought of.
"""
from __future__ import annotations

import ast
import html
import pathlib
import re
import tempfile

import pytest

from fixtures import stub_decode
from ltm.agentapi import verbs
from ltm.ui import jobs, serve, sources

from test_ui_serve import _decode, _texts_of, fetch, q  # noqa: E402 (same suite)


@pytest.fixture()
def session(tmp_path):
    """A session that CAN decode, without launching a server."""
    launched = serve.start(verbs.Context(root=tmp_path / "store"))
    try:
        yield launched.session
    finally:
        launched.stop()


@pytest.fixture()
def reading_only(tmp_path):
    launched = serve.start(verbs.Context(root=tmp_path / "store"),
                           decodes=False)
    try:
        yield launched.session
    finally:
        launched.stop()


def ids_in(markup: str) -> set[str]:
    return set(re.findall(r'id="([A-Za-z0-9_-]+)"', markup))


def reached_by(script: str) -> set[str]:
    return set(re.findall(r"getElementById\('([A-Za-z0-9_-]+)'\)", script))


# --------------------------------------------------------------------------
# the panel, as the server emits it
# --------------------------------------------------------------------------
def test_the_panel_is_on_the_runs_page_and_starts_a_decode(session):
    panel = serve._decode_card(session)
    assert serve.DECODE_HEADING in panel
    assert serve.DECODE_BUTTON in panel and serve.CANCEL_BUTTON in panel
    assert 'action="/api/decode"' in panel
    assert 'data-state="idle"' in panel


def test_both_action_controls_start_disabled_and_the_live_region_is_hidden(
        session):
    """Nothing is picked and nothing is running, so neither button does anything.

    Written down because the enabling is the script's job and a panel that
    shipped its buttons enabled would look correct in every screenshot and be
    wrong for the one second before the first `/api/intake` answer arrives.
    """
    panel = serve._decode_card(session)
    assert re.search(r'<button id="go"[^>]*\bdisabled\b', panel)
    assert re.search(r'<button id="stop"[^>]*\bdisabled\b', panel)
    assert re.search(r'<div id="live"[^>]*\bhidden\b', panel)
    # and the one control that must work immediately does
    assert not re.search(r'<button id="refresh"[^>]*\bdisabled\b', panel)


def test_every_element_the_script_reaches_for_is_in_the_panel(session):
    """Generated FROM the script, so the control count grows with the page.

    A hand-written list of ids would certify the ones its author remembered and
    pass a rename of the one they did not. `getElementById` returning `null` is
    the classic silent-blank: no error, no panel, and a reader who thinks the
    feature was never built.
    """
    panel = serve._decode_card(session)
    wanted = reached_by(serve.DECODE_SCRIPT)
    assert len(wanted) >= 8, (
        "the extraction found almost nothing, so this test is measuring itself "
        "rather than the page")
    missing = sorted(wanted - ids_in(panel))
    assert not missing, f"the script reaches for ids the panel does not have: {missing}"


def test_an_id_the_panel_does_not_have_is_caught(session):
    """The positive control for the test above.

    Without it, a `reached_by` that returned the empty set would pass silently --
    which is the failure mode of every "no missing items" assertion ever written.
    """
    panel = serve._decode_card(session)
    assert "transcript-body" not in ids_in(panel)
    assert sorted({"go", "transcript-body"} - ids_in(panel)) == ["transcript-body"]


def test_the_state_words_the_script_branches_on_are_the_products_own(session):
    """The page and the job table must not drift into two vocabularies.

    `jobs.STATES` is what a status record can say; a branch on a word that is
    not in it is dead code that reads as coverage, and the day the job table
    renames one, this fails instead of the page quietly never matching again.
    """
    branched = set(re.findall(r"s\.state === '([a-z-]+)'", serve.DECODE_SCRIPT))
    assert branched, "no state branch found; the extraction is wrong"
    assert branched <= set(jobs.STATES), (
        f"the page branches on states the product never emits: "
        f"{sorted(branched - set(jobs.STATES))}")


def test_the_page_never_writes_the_cancelled_state_itself(session):
    """R13, at the last layer that could still break it.

    The job table refuses to write `cancelled` until the child says so, and the
    cancel route reports the REQUEST -- and all of that is undone by a page that
    sets the word itself when the button is clicked. So the states this script
    can name as literals are enumerated, and they are the two that describe what
    the PAGE is doing rather than what the decode did.
    """
    literals = set(re.findall(r"say\('([a-z-]+)'\)", serve.DECODE_SCRIPT))
    assert literals == {"starting", "running"}, (
        f"the page names a state of its own accord: {sorted(literals)}")
    for outcome in ("cancelled", "done", "failed"):
        assert f"say('{outcome}')" not in serve.DECODE_SCRIPT


def test_the_candidate_list_has_exactly_one_renderer(session):
    """The `_decode_card` docstring's claim, checked rather than asserted.

    Two renderers of one record is the defect this project keeps meeting under
    other names: they agree the day they are written and drift the first time a
    column changes. So the panel ships an EMPTY select and the script fills it
    from `/api/intake`.
    """
    panel = serve._decode_card(session)
    assert "<option" not in panel
    assert "/api/intake" not in panel
    assert "'/api/intake'" in serve.DECODE_SCRIPT


def test_a_read_only_launch_says_so_instead_of_offering_a_dead_button(
        reading_only):
    """A launch that cannot decode must not draw a panel that cannot work.

    The refusal names the flag, because "nothing happens when I click" is the
    one outcome a local tool has no excuse for.
    """
    panel = serve._decode_card(reading_only)
    assert 'data-state="read-only"' in panel
    assert html.escape(serve.DECODE_READ_ONLY) in panel
    assert "<button" not in panel and "<select" not in panel
    assert "--read-only" in panel


def test_the_script_stands_down_on_a_read_only_page(reading_only):
    """Same page, same script: the guard is in the script, not in the template.

    `page_runs` emits one script for both configurations, so the read-only case
    is a branch inside it rather than a second template to keep in step.
    """
    assert "'read-only'" in serve.DECODE_SCRIPT


def test_the_noscript_sentence_hands_the_reader_the_command_instead(session):
    """Every action on this panel is a request, so no script means no panel.

    Saying that, and naming what to type instead, is the difference between a
    degraded page and a broken one.
    """
    panel = serve._decode_card(session)
    assert "<noscript>" in panel
    assert html.escape(serve.DECODE_NOSCRIPT) in panel
    assert "ltm decode" in serve.DECODE_NOSCRIPT


def test_the_poll_interval_is_the_one_r13_names(session):
    """R13: progress at least once a second, and a cancel answered within one."""
    assert serve.POLL_MS <= 1000
    assert f"setInterval(tick, {serve.POLL_MS})" in serve.decode_script()


# --------------------------------------------------------------------------
# R1, with the control that makes it mean something
# --------------------------------------------------------------------------
def test_the_runs_page_carries_no_transcript_text_and_the_round_page_does(
        tmp_path, monkeypatch):
    """R1 and INV-23 at once, and the control is the other page.

    The panel is the first thing on `/` that makes work happen, so `/` is the
    route most likely to be handed a "just show the first line" feature. The
    assertion that it carries none of the recording's words is worth nothing
    without the second half: those same words ARE on `/run`, which is what shows
    this test can see transcript text when there is transcript text to see.
    """
    ctx, run, rev = _decode(tmp_path, monkeypatch, dict(stub_decode.DECODE),
                            "talk.m4a")
    texts = _texts_of(ctx, run, rev)
    assert texts, "the fixture produced no transcript, so this proves nothing"

    launched = serve.start(ctx)
    try:
        cookie = launched.token
        runs_page = fetch(launched, "GET", "/", cookie=cookie).body
        round_page = fetch(launched, "GET",
                           f"/run?run={q(run)}&rev={q(rev)}",
                           cookie=cookie).body
    finally:
        launched.stop()

    assert serve.DECODE_HEADING in runs_page, "the panel is not even there"
    for text in texts:
        assert text not in runs_page
    assert any(t in round_page for t in texts), (
        "the control did not fire: this test cannot see transcript text at all")
    assert "/" not in serve.TEXT_ROUTES


# --------------------------------------------------------------------------
# GUI-5: a failure has to announce itself
# --------------------------------------------------------------------------
#: The two shapes a failure can arrive in, named by the handler that receives
#: it rather than by a count: every handler given an `answered()` result is a
#: place the server may have said no, and every rejection handler is a place the
#: server may not have answered at all. Both must land in `fault`.
FAILURE_HANDLERS = (".then(function (a)", ".catch(function (")


def blocks(script: str, opener: str) -> list[str]:
    """Every `opener ... { ... }` body, matched by counting braces.

    A regex cannot do this: the bodies nest, and a non-greedy match to the first
    `}` would silently stop inside an inner block and then report that the
    handler contains no report -- a false alarm that teaches a reader to widen
    the pattern until it stops firing at all.
    """
    out: list[str] = []
    for m in re.finditer(re.escape(opener), script):
        start = script.index("{", m.end() - 1)
        depth, i = 0, start
        while i < len(script):
            if script[i] == "{":
                depth += 1
            elif script[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        out.append(script[start:i + 1])
    return out


def codes_the_routes_can_emit() -> set[str]:
    """Every `"code": "<literal>"` in `serve.py`, read out of its syntax tree.

    Generated FROM the module rather than listed here, which is the difference
    between a control that scales and one that certifies the four refusals its
    author happened to remember. A fifth refusal added next year has no sentence
    on the page, and that is what the assertion below is for.
    """
    tree = ast.parse(pathlib.Path(serve.__file__).read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (isinstance(key, ast.Constant) and key.value == "code"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)):
                found.add(value.value)
    return found


def test_every_code_a_reader_can_arrive_at_has_a_sentence():
    """L-044's shape: the page's vocabulary must cover every class the product
    can name.

    Three populations, and none of them is a list kept by hand: the refusal
    codes in `serve.py`'s own syntax tree, the allowlist's code, and the job
    states that are not `done` or `cancelled`. A code with no sentence reaches
    the reader as a bare identifier or, worse, as nothing at all.
    """
    reachable = (codes_the_routes_can_emit()
                 | {sources.NotOffered.code}
                 | (set(jobs.STATES) - {"done", "cancelled", "running",
                                        "cancelling"}))
    missing = sorted(reachable - set(serve.DECODE_FAULTS))
    assert not missing, (
        f"these can reach the page with no sentence to show: {missing}")


def test_a_new_refusal_code_with_no_sentence_is_caught():
    """The positive control for the test above.

    Without it, a `codes_the_routes_can_emit` that returned the empty set would
    pass on a page that had no sentences at all.
    """
    assert len(codes_the_routes_can_emit()) >= 4, (
        "the syntax-tree scan found almost nothing, so it is not measuring "
        "serve.py")
    invented = {"disk-full"} | set(serve.DECODE_FAULTS)
    assert sorted(invented - set(serve.DECODE_FAULTS)) == ["disk-full"]


def test_there_is_exactly_one_place_a_failure_is_reported():
    """One reporter, so a failure cannot reach the reader as a page that stopped.

    `fault` does three things in one call -- writes `data-state`, writes a
    sentence a person can read, and puts a structured record on the console --
    and the way to be sure all three happen every time is for there to be
    nowhere else that reports anything. The console call is counted with its
    parenthesis because the comment above it names the function.
    """
    script = serve.decode_script()
    assert script.count("console.error(") == 1
    assert script.count("function fault(") == 1


@pytest.mark.parametrize("opener", FAILURE_HANDLERS)
def test_every_handler_that_can_learn_of_a_failure_reports_it(opener):
    """Generated from the script, so it scales with the number of ways to fail.

    A handler that took a refusal and returned quietly would leave a bar that
    never moves and a console with nothing in it -- the defect this card is
    named after, and the one a reader reports as "it just did nothing".

    This is the test that found one: `refresh` did not look at `a.ok` at all, so
    a refused listing would have arrived as an EMPTY listing and the page would
    have told the reader that nothing is offered. A wrong sentence is worse than
    no sentence, because nobody investigates it.
    """
    script = serve.decode_script()
    bodies = blocks(script, opener)
    assert bodies, f"no handler matched {opener!r}; this test is not running"
    silent = [b for b in bodies if "fault(" not in b]
    assert not silent, f"a handler reports nothing: {silent}"


@pytest.mark.parametrize("opener", FAILURE_HANDLERS)
def test_a_handler_with_its_report_removed_is_caught(opener):
    """**The card's own control**: a stubbed failure with no visible change must
    fail the test above.

    Mutating every handler the scan found rather than one written by hand, so
    the control count is the handler count -- and a scan that quietly stopped
    matching is caught by the emptiness assertion rather than passing.
    """
    script = serve.decode_script()
    bodies = blocks(script, opener)
    assert bodies
    for body in bodies:
        muted = script.replace(body, body.replace("fault(", "quiet("))
        assert muted != script, "the mutation changed nothing"
        still = [b for b in blocks(muted, opener) if "fault(" not in b]
        assert still, (
            f"a handler with its report removed was NOT caught, so this scan "
            f"does not measure what it claims: {body!r}")


def test_the_reporter_reaches_a_reader_who_has_not_started_anything():
    """The outcome line lives OUTSIDE the region that only appears mid-decode.

    A refused path and a server that has gone away both happen before any
    decode exists. If the only place the page can speak were inside `#live`,
    those two would be silent -- which is precisely the failure this card is
    about, arriving by layout instead of by logic.
    """
    panel = serve._decode_card(session_of())
    live = re.search(r'<div id="live".*?</div>', panel, re.S)
    assert live, "the live region moved; this test no longer means anything"
    assert 'id="outcome"' not in live.group(0)
    assert 'id="outcome"' in panel


def session_of():
    launched = serve.start(verbs.Context(root=pathlib.Path(tempfile.mkdtemp())))
    launched.stop()
    return launched.session
