"""The human face: one loopback page served by the standard library (R15).

PSM §16, cards F-1..F-6. `docs/adr-gui-stack-2026-09-10.md` chose this shape and
`docs/gui-contract.json` is the seam it reads through; neither is restated here.

**What this page exists for**, in one sentence taken from the measurement rather
than from a wish: three real recordings showed that 「這份錄音整體可不可信」 lives
in `show` and 「哪一句是錯的」 lives in `plan`, so a reader had to hold two
commands in their head and join them by hand (`docs/first-run-2026-09-10.md` §4
item 6). **The round screen puts both on one screen.** Without that this face is
a slower CLI, which is why §16.2 marks it as the one thing that may not be
dropped.

**INV-23, and why it is a list rather than a habit.** A route that returns
transcript text is readable by anything on this machine that can speak HTTP,
including an agent -- and INV-12 spends the whole agent schema keeping text out
of agent-reachable responses. So the rule is not 「文字不能出現」, it is 「承載
文字的出口必須被逐條列舉」: `TEXT_ROUTES` below is that list, `tests/
test_ui_serve.py` asserts a route outside it cannot return `Cue.text`, and the
assertion ships with both controls in this same commit. The danger INV-23 names
in so many words is 「多開一條 JSON 路由比較好接」 -- the change nobody blocks at
review -- so the list is a literal in the source and the test reads THAT literal.

`_cues_for` is a second, weaker line: it refuses to read text at all unless the
route currently being served is in the list. It cannot catch a route that
fabricates text some other way, which is exactly why the test walks the routes
and does not merely trust this function.

**Nothing here writes to the store.** Every state change goes through a verb;
adoption is `POST /adopt` -> `verbs.accept`, and this module imports no
`store.write`. `tests/test_ui_serve.py` asserts the absence rather than trusting
the reading of it.

**The verbs are called directly and not through `verbs.call`.** `call()` stamps
`face="agent"` on the context (A-21), and a person at this machine looking at
their own recording is not an agent. The revision this page writes says `human`
because a human is what wrote it.

**The page can start decodes (PSM §19, 2026-09-16), and that is the first write
path this face has ever had.** Four JSON routes -- `/api/intake`, `/api/decode`,
`/api/progress`, `/api/cancel` -- and every one of them is narrow on purpose:

* the decode runs in a CHILD (`ui/jobs.py`), never in this process, because
  `http.server` in the shape used here answers one request at a time and an
  engine inside it would make every other route wait out four minutes --
  including the cancel the reader is about to need;
* the path a decode may be started on is an ALLOWLIST (`ui/sources.py`), not a
  string check, and a server launched without `--audio-root` can only re-decode
  recordings that are already in the store;
* **none of the four is in `TEXT_ROUTES`**, and none of them can be: they carry
  ids, counts and codes. `JSON_ROUTES` below is asserted disjoint from
  `TEXT_ROUTES`, because 「多開一條 JSON 路由比較好接」 is the exact sentence
  INV-23 was written against, and these are four new JSON routes.
"""
from __future__ import annotations

import argparse
import html
import http.server
import json
import pathlib
import secrets
import sys
import threading
import traceback
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

from ..agentapi import schema, verbs
from ..store import layout
from . import cli, jobs as jobs_module, read, sources

#: Loopback and nothing else. INV-23's first clause, and the reason the whole
#: face is legal under INV-11's scope (PIM §3 N-5): a request that never leaves
#: this machine is not the network request that ruling is about.
HOST = "127.0.0.1"

#: The cookie the token is exchanged for. Host-only and `SameSite=Strict`: the
#: token itself must not stay in the address bar, where it would reach history
#: and every log that records paths (§16.0 decision 3).
COOKIE = "ltm_face"

#: `?t=<token>` on the launch URL, and only there.
TOKEN_PARAM = "t"

#: A POST body larger than this is refused unread. Nobody ticks a megabyte of
#: checkboxes; a body that big is a mistake or a probe, and reading it first to
#: find out would be the wrong order.
MAX_BODY = 1 << 20


class ExposureError(RuntimeError):
    """A route that is not in `TEXT_ROUTES` tried to read transcript text."""


@dataclass
class Session:
    """One launch: which store, which token, and the last thing `accept` said.

    `flash` carries an `AcceptResponse` across the redirect that follows a POST,
    so the reader is told what their decision produced without the answer being
    re-derived from a query string. It is per-process state on a single-user
    loopback face -- there is exactly one reader, and they are the person who
    started the process.
    """

    ctx: verbs.Context
    token: str
    flash: Optional[schema.AcceptResponse] = None
    #: The decode the page started, if any. One table per launch, and it dies
    #: with the process (§19.0 (6)) -- a job that outlived its server would be a
    #: child nobody can cancel and nobody can read the progress of.
    jobs: Optional["jobs_module.Jobs"] = None
    #: What may be decoded. `None` means this launch cannot start decodes at all,
    #: which is what the read-only face was before 2026-09-16 and what a test
    #: fixture is unless it asks for more.
    catalogue: Optional["sources.Catalogue"] = None


@dataclass(frozen=True)
class Request:
    """What a handler is allowed to know. The socket does not reach past here."""

    method: str
    path: str
    query: Mapping[str, Sequence[str]]
    form: Mapping[str, Sequence[str]]
    cookie: Optional[str]


@dataclass(frozen=True)
class Reply:
    """A whole answer, decided before a byte is written.

    Handlers return this rather than writing to a socket, which is what lets the
    INV-23 walk run every route and READ what it produced. A check that could
    only observe a live socket would need the suite to speak HTTP, and
    `tests/test_layering.py` bans a network import in a test file.
    """

    status: int
    body: str = ""
    location: Optional[str] = None
    set_cookie: Optional[str] = None
    content_type: str = "text/html; charset=utf-8"


Handler = Callable[[Session, Request], Reply]


# --- the text guard ------------------------------------------------------

def _cues_for(session: Session, request: Request, run: str,
              rev: str) -> Mapping[str, dict]:
    """Transcript text, and only for a route that is on the list.

    The list is checked HERE as well as in the test because the two catch
    different mistakes: the test catches a route that returns text by any means,
    this catches the ordinary version of that mistake -- somebody adds a handler,
    reaches for the reader that is already imported, and never thinks about the
    list at all. It is the weaker of the two and is not a substitute for it.
    """
    if request.path not in TEXT_ROUTES:
        raise ExposureError(
            f"{request.path!r} is not in TEXT_ROUTES, so it may not read "
            f"transcript text (INV-23). A route that needs the words is a "
            f"decision to add one to that list, taken in the open")
    return read.cues_of(session.ctx.root, run, rev)


# --- rendering -----------------------------------------------------------

STYLE = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin: 0; font: 15px/1.55 "Segoe UI", system-ui, sans-serif;
       background: #fbfbfa; color: #1c1c1a; }
main { max-width: 1180px; margin: 0 auto; padding: 24px 28px 64px; }
h1 { font-size: 20px; margin: 0 0 2px; }
h2 { font-size: 15px; margin: 30px 0 8px; letter-spacing: .04em;
     text-transform: uppercase; color: #6a6a63; }
.sub { color: #6a6a63; margin: 0 0 18px; }
a { color: #1c4f8a; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid #e4e4de;
         vertical-align: top; }
th { font-weight: 600; color: #6a6a63; font-size: 13px; }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums;
             white-space: nowrap; }
.grid { display: grid; grid-template-columns: minmax(320px, 2fr) minmax(0, 3fr);
        gap: 28px; align-items: start; }
@media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
.card { border: 1px solid #e4e4de; border-radius: 6px; padding: 14px 16px;
        background: #fff; }
.words { font-size: 15px; margin: 4px 0 0; word-break: break-word; }
mark { background: #ffe9a8; padding: 0 1px; }
.meta { font-family: Consolas, ui-monospace, monospace; font-size: 12px;
        color: #6a6a63; }
.item { padding: 10px 0; border-bottom: 1px solid #eeeee8; }
.item:last-child { border-bottom: 0; }
.undetermined { color: #9a4a10; font-weight: 600; }
.flash { border: 1px solid #b8d4b0; background: #f2f8f0; border-radius: 6px;
         padding: 12px 16px; margin: 0 0 18px; }
.warn { border: 1px solid #d9b3a8; background: #fbf3f0; border-radius: 6px;
        padding: 12px 16px; margin: 0 0 18px; }
button { font: inherit; padding: 7px 16px; border-radius: 5px;
         border: 1px solid #1c4f8a; background: #1c4f8a; color: #fff;
         cursor: pointer; }
button[disabled] { opacity: .45; cursor: default; }
#stop, #refresh { background: transparent; color: #1c4f8a; }
select { font: inherit; padding: 6px 8px; max-width: 46ch;
         border-radius: 5px; border: 1px solid #c9c9c1; }
progress { width: 100%; height: 10px; }
#decode p { margin: 8px 0; }
label.op { display: block; padding: 8px 0; border-bottom: 1px solid #eeeee8; }
footer { margin-top: 40px; color: #8a8a82; font-size: 12px; }
@media (prefers-color-scheme: dark) {
  body { background: #17181a; color: #e6e6e2; }
  a { color: #7fb0e6; }
  th, td { border-bottom-color: #2c2e31; }
  th, .sub, .meta, footer { color: #9a9a94; }
  .card { background: #1e2023; border-color: #2c2e31; }
  .item { border-bottom-color: #26282b; }
  mark { background: #5c4a12; color: #fff3cf; }
  #stop, #refresh { color: #7fb0e6; border-color: #7fb0e6; }
  select { background: #1e2023; color: #e6e6e2; border-color: #3a3d41; }
  .flash { background: #1b2a1c; border-color: #33512f; }
  .warn { background: #2c2020; border-color: #5a3a34; }
  label.op { border-bottom-color: #26282b; }
}
"""


def _page(title: str, body: str) -> str:
    """The whole document. One template, no build step (R15)."""
    return ("<!doctype html>\n"
            "<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, "
            "initial-scale=1\">\n"
            f"<title>{html.escape(title)}</title>\n<style>{STYLE}</style>\n"
            "</head>\n<body>\n<main>\n" + body +
            "\n<footer>local-transcript-maker &middot; this page is served on "
            f"{HOST} for as long as the process that started it is alive, and "
            "nothing on it leaves this machine.</footer>\n"
            "</main>\n</body>\n</html>\n")


def url_for(path: str, **params: str) -> str:
    """A real URL. Percent-encoded, and NOT html-escaped.

    Split from `_link` after the escaped form reached a `Location:` header,
    where `&amp;` is not an ampersand: the browser would have asked for a
    parameter called `amp;rev` and the redirect after an adopt would have landed
    on the run's newest round instead of the one the reader decided on. Escaping
    belongs at the HTML boundary and nowhere else.
    """
    return path + "?" + urllib.parse.urlencode(params)


def _link(path: str, **params: str) -> str:
    """`url_for` for an `href`, where `&` must be written `&amp;`."""
    return html.escape(url_for(path, **params))


def _excerpt_html(piece: Optional[read.Excerpt]) -> str:
    """The words, with the span marked. `None` is SAID, never left blank.

    `read.excerpt` returns `None` when the round's record cannot show the words,
    and the CLI's sentence for that case is imported rather than written again:
    two faces saying the same thing in two wordings is two truths.
    """
    if piece is None:
        return f'<p class="words meta">{html.escape(cli.NO_TEXT)}</p>'
    left = ("…" if piece.truncated_left else "") + piece.before
    right = piece.after + ("…" if piece.truncated_right else "")
    return ('<p class="words">' + html.escape(left)
            + "<mark>" + html.escape(piece.span) + "</mark>"
            + html.escape(right) + "</p>")


def _evidence(mapping: Mapping[str, float]) -> str:
    return ", ".join(f"{k}={v:g}" for k, v in sorted(mapping.items()))


def content_hash(run: str) -> Optional[str]:
    """The `<hash8>` half of `<stem>-<hash8>`, or `None` if this is not a run id.

    Two runs whose ids end in the same hash hold the same audio -- that is what
    the hash is (`core.ids.content_hash`), and it is how this page answers I-19's
    question without a second index to keep in step. `layout.run_stem_of` owns
    the convention; this only takes the other half of what it split.
    """
    try:
        stem = layout.run_stem_of(run)
    except ValueError:
        return None
    return run[len(stem) + 1:]


# --- the decode panel (PSM §19, card GUI-4) -------------------------------

#: The words on the panel, as constants, so a test asserts the same strings the
#: reader sees rather than a paraphrase of them (the `ADOPT_HINT` precedent).
DECODE_HEADING = "Decode a recording"
DECODE_BUTTON = "Start the decode"
CANCEL_BUTTON = "Ask it to stop"
REFRESH_BUTTON = "Look again"
DECODE_EMPTY = ("Nothing is offered. Launch with --audio-root DIR to reach a "
                "folder of recordings, or register one with `ltm intake`.")
DECODE_NOSCRIPT = ("This panel needs JavaScript: starting a decode, watching it "
                   "and asking it to stop are three requests it makes for you. "
                   "Without it, `ltm decode <run>` does the same work at a "
                   "prompt and prints the same progress.")
DECODE_READ_ONLY = ("This launch reads only. It was started with --read-only, "
                    "so the page cannot start a decode; `ltm decode <run>` "
                    "still can.")

#: **One sentence per code a reader can actually arrive at** (PSM §19, GUI-5).
#: A panel that printed the bare code would be honest and useless; a panel that
#: printed a friendly sentence with no code would be useless to whoever has to
#: fix it. Both, every time, and the code is also what `data-state` carries.
#:
#: The keys are not invented here: `tests/test_ui_page_decode.py` collects every
#: `"code"` literal the four routes can emit out of this module's own syntax
#: tree, adds the job states that are not `done` or `cancelled`, and requires a
#: sentence for each. A refusal added without a sentence turns that test red --
#: which is the whole point, because the failure that has no words is the one
#: that reaches the reader as a page that simply stopped.
DECODE_FAULTS = {
    "not-offered": ("That recording is not on the list this page offered. "
                    "Pick one from the menu, or launch with --audio-root "
                    "pointing at the folder it lives in."),
    "bad-argument": "The page sent a request the server could not read.",
    "busy": "A decode is already running; this server runs one at a time.",
    "no-such-job": ("That job is gone. A job dies with the server that started "
                    "it, so a page left open across a restart will say this."),
    "not-configured": ("This server was launched without the ability to start "
                       "decodes."),
    "failed": ("The decode ended badly. The terminal that started this server "
               "has the engine's own words."),
    "unreachable": ("This page could not reach the server. It has probably "
                    "stopped -- and a job does not outlive it."),
    "unreadable": ("The server answered with something this page could not "
                   "read. The terminal that started it has the traceback."),
}

#: The sentence for a code that reaches the page without one. It must never be
#: the only thing shown, which is why `fault` appends the code itself.
DECODE_FAULT_UNKNOWN = ("Something went wrong that this page has no words for. "
                        "The code is below and the terminal that started this "
                        "server has more.")

#: How often the page asks. §19.0 (2): polling, not SSE -- `http.server` has no
#: event-stream helper and a long-lived streaming response in this server would
#: block every other route, including the cancel the reader is about to need.
#: One second is R13's own interval ("progress at least once a second").
POLL_MS = 1000


def _decode_card(session: Session) -> str:
    """Pick a recording, start it, watch it, stop it.

    **This panel renders no transcript text and cannot** (R1). What it shows is
    a filename, a phase name, two counts and a state word; when the decode ends
    it links to the RUN's record and stops there. That is why `/` is still not
    in `TEXT_ROUTES` after this card -- the thing that changed is that the page
    can now make work happen, not that it can show words.

    **The candidate list is drawn by the script, from `/api/intake`, and by
    nothing else.** Rendering it here as well would be a second copy of one
    record: the two would agree on the day they were written and drift the first
    time a column changed. A reader with no script cannot use this panel at all
    -- every one of its three actions is a request -- so showing them a list
    they could not act on would be worse than the sentence that replaces it.

    **No options yet, and that is a decision rather than an omission.** A pinned
    `--language` is refused unless `--no-multilingual` comes with it (E-3, PSM
    §17), so an options row means the page has to explain a refusal before the
    reader has done anything wrong. The card asks for pick-start-watch-stop; the
    defaults are the CLI's defaults.
    """
    if session.catalogue is None or session.jobs is None:
        return (f'<div class="card" id="decode" data-state="read-only">'
                f"<h2>{html.escape(DECODE_HEADING)}</h2>"
                f'<p class="sub">{html.escape(DECODE_READ_ONLY)}</p></div>')
    return (
        '<div class="card" id="decode" data-state="idle" data-sources="?">'
        f"<h2>{html.escape(DECODE_HEADING)}</h2>"
        '<form id="start" method="post" action="/api/decode">'
        '<p><select id="source" name="path" aria-label="recording"></select> '
        f'<button id="go" type="submit" disabled>{html.escape(DECODE_BUTTON)}'
        "</button> "
        f'<button id="stop" type="button" disabled>'
        f"{html.escape(CANCEL_BUTTON)}</button> "
        f'<button id="refresh" type="button">{html.escape(REFRESH_BUTTON)}'
        "</button></p>"
        "</form>"
        '<p class="sub" id="sources-note"></p>'
        '<div id="live" hidden>'
        '<p><progress id="bar" max="1"></progress></p>'
        '<p class="meta"><span id="state">idle</span> &middot; '
        '<span id="phase"></span> &middot; <span id="counts"></span></p>'
        "</div>"
        # OUTSIDE `#live`, and that is the point of card GUI-5: a failure before
        # a decode ever starts -- a refused path, a server that has gone away --
        # has to be able to say so, and it cannot say it from inside a region
        # that only becomes visible once a decode is running.
        '<p id="outcome"></p>'
        f"<noscript><p class=\"warn\">{html.escape(DECODE_NOSCRIPT)}</p>"
        "</noscript>"
        "</div>")


#: Inline and small, for the same reason `SCRIPT` is: R15 says no build step.
#:
#: **`data-state` on the panel is the page's state, and it is written in exactly
#: one place** (`say`). A test -- and a person with the inspector open -- reads
#: one attribute instead of inferring the state from which controls happen to be
#: disabled. The states are the job table's own (`jobs.STATES`) plus `idle` and
#: whatever code a refusal came back with, so the page never invents a word for
#: something the product already names.
#:
#: **`cancelled` is never written here.** `stop` posts the request and shows the
#: answer, and that answer says `cancelling` while the child is still alive. The
#: child is what turns it into `cancelled`, at every layer this increment
#: touched, because a page that reported "cancelled" over a still-writing engine
#: is the failure R13 exists for.
DECODE_SCRIPT = """
(function () {
  var panel = document.getElementById('decode');
  if (!panel || panel.getAttribute('data-state') === 'read-only') return;
  var form = document.getElementById('start'),
      picker = document.getElementById('source'),
      go = document.getElementById('go'),
      stop = document.getElementById('stop'),
      again = document.getElementById('refresh'),
      note = document.getElementById('sources-note'),
      live = document.getElementById('live'),
      bar = document.getElementById('bar'),
      stateText = document.getElementById('state'),
      phaseText = document.getElementById('phase'),
      countsText = document.getElementById('counts'),
      outcome = document.getElementById('outcome');
  var job = null, timer = null;
  var FAULTS = %(faults)s;

  function say(state) {
    panel.setAttribute('data-state', state);
    stateText.textContent = state;
  }

  // **Every failure path in this script ends here** (card GUI-5). Three
  // audiences, one call site: `data-state` for a test and for whoever has the
  // inspector open, a sentence for the reader, and a structured record on the
  // console for whoever has to fix it. A branch that returned quietly would
  // leave a bar that never moves and a console with nothing in it, which is the
  // defect this card is named after -- so there is exactly one `console.error`
  // in this file and it is in here.
  function fault(code, detail) {
    if (timer) { clearInterval(timer); timer = null; }
    job = null;
    stop.disabled = true;
    go.disabled = false;
    bar.removeAttribute('value');
    say(code);
    outcome.textContent = (FAULTS[code] || %(unknown)s)
      + '  [' + code + (detail ? ': ' + detail : '') + ']';
    console.error('ltm decode panel', {code: code, detail: detail || null,
                                       state: code, at: new Date().toISOString()});
  }

  function size(b) {
    return b >= 1048576 ? (b / 1048576).toFixed(1) + ' MB'
                        : Math.max(1, Math.round(b / 1024)) + ' KB';
  }

  // A body that is not JSON is a fault with a name, not an exception nobody
  // catches. `/api/*` answers JSON on every path it has, including its
  // refusals -- so arriving here means something else answered, and the reader
  // is owed that sentence rather than a page that stopped.
  function answered(r) {
    return r.json().then(
      function (body) { return {ok: r.ok, status: r.status, body: body}; },
      function () { return {ok: false, status: r.status,
                            body: {code: 'unreadable'}}; });
  }

  function send(path, pairs) {
    var payload = new URLSearchParams();
    for (var key in pairs) payload.append(key, pairs[key]);
    return fetch(path, {method: 'POST', body: payload}).then(answered);
  }

  function refresh() {
    return fetch('/api/intake').then(answered).then(function (a) {
      // A refused listing is not an empty listing. Without this branch a 403 or
      // a read-only 503 would arrive as `sources: undefined`, the picker would
      // empty itself, and the note would tell the reader that nothing is
      // offered -- a wrong sentence, which is worse than no sentence. Found by
      // writing the test that says every handler of an answer reports.
      if (!a.ok) {
        fault((a.body && a.body.code) || 'unreadable', 'the source list');
        panel.setAttribute('data-sources', '?');
        return;
      }
      picker.textContent = '';
      var rows = (a.body && a.body.sources) || [];
      rows.forEach(function (s) {
        var option = document.createElement('option');
        option.value = s.path;
        option.textContent = s.name + '  \\u00b7  ' + size(s.bytes)
          + (s.run ? '  \\u00b7  ' + s.run : '');
        option.title = s.path;
        picker.appendChild(option);
      });
      panel.setAttribute('data-sources', String(rows.length));
      note.textContent = rows.length ? (a.body.note || '')
                                     : (a.body.note || %(empty)s);
      go.disabled = !rows.length || job !== null;
    }).catch(function (e) {
      // The server is the only thing this page has. Saying so beats a picker
      // that silently stays empty and a button nobody can explain.
      panel.setAttribute('data-sources', '?');
      fault('unreachable', 'the source list');
      go.disabled = true;
    });
  }

  function show(s) {
    say(s.state);
    phaseText.textContent = s.phase || '';
    if (typeof s.total === 'number' && s.total > 0) {
      bar.max = s.total;
      bar.value = typeof s.done === 'number' ? s.done : 0;
      countsText.textContent = (s.done || 0) + ' / ' + s.total
        + (s.unit ? ' ' + s.unit : '');
    } else {
      bar.removeAttribute('value');
      countsText.textContent = typeof s.elapsed === 'number'
        ? Math.round(s.elapsed) + ' s' : '';
    }
    if (s.state === 'running' || s.state === 'cancelling') return;
    ended(s);
  }

  function ended(s) {
    if (s.state !== 'done' && s.state !== 'cancelled') {
      // Every unhappy ending the job table can produce goes through the one
      // reporter, so `failed` reaches the reader with the same three signals a
      // refused path does.
      fault(s.state, [s.code, typeof s.exitCode === 'number'
                              ? 'exit ' + s.exitCode : null]
                     .filter(Boolean).join(', '));
      refresh();
      return;
    }
    if (timer) { clearInterval(timer); timer = null; }
    job = null;
    stop.disabled = true;
    bar.removeAttribute('value');
    outcome.textContent = '';
    if (s.state === 'done') {
      var link = document.createElement('a');
      link.href = '/run?run=' + encodeURIComponent(s.run);
      link.textContent = s.run;
      outcome.appendChild(document.createTextNode('decoded. The record is at '));
      outcome.appendChild(link);
      outcome.appendChild(document.createTextNode(
        ' \\u2014 this list does not update itself, so reload for the rest.'));
    } else {
      outcome.textContent = 'stopped, and the partial output was discarded by'
        + ' the decode itself rather than by this page.';
    }
    refresh();
  }

  function tick() {
    if (!job) return;
    fetch('/api/progress?job=' + encodeURIComponent(job)).then(answered)
      .then(function (a) {
        if (a.ok) { show(a.body); return; }
        fault((a.body && a.body.code) || 'unreadable', 'while polling');
      })
      .catch(function (e) { fault('unreachable', 'while polling'); });
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    if (job || !picker.value) return;
    go.disabled = true;
    outcome.textContent = '';
    live.hidden = false;
    say('starting');
    send('/api/decode', {path: picker.value}).then(function (a) {
      if (!a.ok) {
        fault((a.body && a.body.code) || 'unreadable',
              a.body && a.body.jobId ? a.body.jobId : null);
        return;
      }
      job = a.body.jobId;
      panel.setAttribute('data-job', job);
      stop.disabled = false;
      say('running');
      timer = setInterval(tick, %(interval)d);
      tick();
    }).catch(function (e) { fault('unreachable', 'while starting'); });
  });

  stop.addEventListener('click', function () {
    if (!job) return;
    stop.disabled = true;
    send('/api/cancel', {job: job}).then(function (a) {
      if (a.ok) { show(a.body); return; }
      fault((a.body && a.body.code) || 'unreadable', 'while cancelling');
    }).catch(function (e) { fault('unreachable', 'while cancelling'); });
  });

  again.addEventListener('click', function () { refresh(); });
  refresh();
})();
"""


def decode_script() -> str:
    """`DECODE_SCRIPT` with its four holes filled. The only caller is the page.

    A function rather than a `%` at the call site, because the tests need the
    SAME string the reader is served -- a test that filled the template itself
    would be testing its own substitution, and the fault sentences live in the
    filled version only.
    """
    return DECODE_SCRIPT % {
        "empty": _js(DECODE_EMPTY),
        "unknown": _js(DECODE_FAULT_UNKNOWN),
        "faults": _js_object(DECODE_FAULTS),
        "interval": POLL_MS,
    }


# --- screen 1: the runs ---------------------------------------------------

def page_runs(session: Session, request: Request) -> Reply:
    """Every run, how far each got, and which of them are the same recording.

    **No transcript text**, which is why `/` is not in `TEXT_ROUTES`: a run id
    carries the user's own filename and a content hash, and a filename is not
    something INV-23 is about.
    """
    response = verbs.runs(session.ctx)
    counts: dict[str, int] = {}
    for ref in response.runs:
        digest = content_hash(ref.run)
        if digest:
            counts[digest] = counts.get(digest, 0) + 1

    rows = []
    for ref in response.runs:
        high = int(ref.high_water)
        digest = content_hash(ref.run)
        where = (layout.serial(high) if high >= 0 else "no round yet")
        same = counts.get(digest or "", 0) - 1
        twin = (f"same audio as {same} other run"
                + ("s" if same > 1 else "")) if same > 0 else ""
        cell = (f'<a href="{_link("/run", run=ref.run)}">'
                f"{html.escape(ref.run)}</a>" if high >= 0
                else html.escape(ref.run))
        rows.append(f"<tr><td>{cell}</td><td class=\"n meta\">"
                    f"{html.escape(where)}</td>"
                    f"<td class=\"meta\">{html.escape(twin)}</td></tr>")

    if not rows:
        table = ('<p class="sub">No runs under this root yet. '
                 "<code>ltm intake &lt;file&gt;</code> then "
                 "<code>ltm decode &lt;run&gt;</code> makes one.</p>")
    else:
        table = ("<table><thead><tr><th>run</th><th class=\"n\">latest "
                 "revision</th><th>same recording?</th></tr></thead>"
                 "<tbody>" + "".join(rows) + "</tbody></table>")

    body = (f"<h1>Runs</h1>\n<p class=\"sub\">"
            f"{html.escape(str(session.ctx.root))}</p>\n"
            + _decode_card(session) + table
            + "<script>" + decode_script() + "</script>")
    return Reply(200, _page("ltm — runs", body))


# --- screen 2 and 3: one round -------------------------------------------

def _newest_round_with_a_plan(session: Session, run: str
                              ) -> tuple[str, schema.PlanResponse]:
    """The most recent revision that has a detection round of its own.

    A run's high-water revision is often an EDIT round, which places transcript
    and an edit script and no plan -- so 「the latest revision」 and 「the latest
    thing there is a verdict about」 are different revisions, and landing a reader
    on the first when they asked for the second is a dead end.

    It walks down through `verbs.plan`, one revision at a time, rather than
    listing `_plans/`: the surface has no second reader of the store, and the
    walk costs one `stat` per revision that is not there.
    """
    record = verbs.runs(session.ctx)
    high = next((int(r.high_water) for r in record.runs if r.run == run), None)
    if high is None:
        raise schema.AgentError("no-such-run")
    for n in range(high, -1, -1):
        try:
            got = verbs.plan(session.ctx, run=run, rev=layout.serial(n))
        except schema.AgentError as e:
            if e.code != "no-such-revision":
                raise
            continue
        return layout.serial(n), got
    raise schema.AgentError("no-such-revision", "r000")


def _trust_card(stretches: Sequence[Mapping[str, object]],
                cue_count: Optional[int],
                report: Optional[Mapping[str, object]] = None) -> str:
    """「這份錄音整體可不可信」 -- the stretch table with its agreement numbers.

    This is half of what §16 says may not be dropped. The other half is
    `_findings_card`, and the point is that they are in the same response and
    come out of the same round's record -- see `read.stretches_of` for why they
    do not come from the `show` verb.
    """
    if not stretches:
        return ('<div class="card"><h2>Overall</h2><p class="sub">This '
                "round's record carries no language measurement. A round's "
                "verdicts belong to the version they were measured on.</p>"
                "</div>")

    undetermined = sum(1 for s in stretches
                       if str(s.get("language")) == layout.UNDETERMINED)
    rows = "".join(
        f'<tr><td class="n meta">{read.clock(float(s["start"]))}'
        f'&ndash;{read.clock(float(s["end"]))}</td>'
        f'<td class="{"undetermined" if str(s.get("language")) == layout.UNDETERMINED else ""}">'
        f'{html.escape(str(s.get("language")))}</td>'
        f'<td class="n">{float(s["agreement"]):.3f}</td></tr>'
        for s in stretches)

    if cue_count == 0:
        # I-22. With no words there is no text side, so every stretch is
        # `undetermined` for a reason that is NOT "the words and the audio
        # disagreed" -- saying that here would be a confident wrong answer about
        # a recording in which nobody spoke. The sentence is the CLI's.
        report = report or {}
        kept = report.get("vad_kept")
        verdict = ("<strong>This round has " + html.escape(cli.no_words_sentence(
            report.get("vad_enabled") is True,
            float(kept) if isinstance(kept, (int, float)) else None))
            + ".</strong>")
    elif undetermined:
        verdict = (f"<strong>{undetermined} of {len(stretches)} stretch"
                   f"{'es' if len(stretches) > 1 else ''} could not be "
                   "labelled.</strong> Agreement below 0.5 means no language had "
                   "majority support from two independent signals &mdash; the "
                   "words and the audio disagreed. Treat those stretches as the "
                   "ones to read yourself.")
    else:
        verdict = ("Every stretch has a label with majority support from two "
                   "independent signals. That is a statement about the language, "
                   "not about whether the words are right.")

    counts = (f"{cue_count} cues, {len(stretches)} stretches"
              if cue_count is not None else f"{len(stretches)} stretches")
    return ('<div class="card"><h2>Overall &mdash; is this trustworthy?</h2>'
            f'<p class="sub">{verdict}</p>'
            "<table><thead><tr><th class=\"n\">when</th><th>language</th>"
            "<th class=\"n\">agreement</th></tr></thead><tbody>"
            + rows + "</tbody></table>"
            f'<p class="meta">{html.escape(counts)}</p></div>')


def _findings_card(plan: schema.PlanResponse,
                   cues: Mapping[str, dict]) -> str:
    """「哪一句是錯的」 -- each finding with its words and a timecode.

    The words come from `ui/read.py` and from nowhere else (`docs/
    gui-contract.json`'s `implementationRules`), in the source coordinates INV-5
    fixes. The note sentences come from `ui/cli.py`, for the same reason: one
    ruling, one wording.
    """
    parts = []
    for ref in plan.findings:
        piece = read.excerpt(cues, str(ref.cue_id), ref.char_start, ref.char_end)
        when = read.clock(piece.start) if piece else "?"
        parts.append(
            '<div class="item"><p class="meta">'
            f"<strong>{html.escape(str(ref.code))}</strong> &middot; {when} "
            f"&middot; {html.escape(str(ref.cue_id))} chars "
            f"{ref.char_start:.0f}&ndash;{ref.char_end:.0f} &middot; "
            f"{html.escape(_evidence(ref.evidence))}</p>"
            + (f'<p class="sub">{html.escape(cli.FINDING_SENTENCE[str(ref.code)])}'
               "</p>" if str(ref.code) in cli.FINDING_SENTENCE else "")
            + _excerpt_html(piece) + "</div>")
    for ref in plan.notes:
        piece = read.excerpt(cues, str(ref.cue_id), ref.char_start, ref.char_end)
        when = read.clock(piece.start) if piece else "?"
        sentence = cli.NOTE_SENTENCE.get(str(ref.reason), cli.NOTE_UNSAID)
        parts.append(
            '<div class="item"><p class="meta">note &middot; '
            f"{when} &middot; {html.escape(str(ref.cue_id))} &middot; "
            f"{html.escape(sentence)}</p>" + _excerpt_html(piece) + "</div>")

    if not parts:
        parts.append('<p class="sub">Nothing was flagged in this round. That '
                     "is what the detectors did not find, which is not the same "
                     "as the transcript being right.</p>")
    return ('<div class="card"><h2>Which sentence &mdash; '
            f"{len(plan.findings)} finding(s), {len(plan.notes)} note(s)</h2>"
            + "".join(parts) + "</div>")


#: The two sentences the decide form has to be able to say, kept as constants so
#: the test asserts the same strings the reader sees.
ADOPT_HINT = ("Tick what you want removed, then submit. Ticking nothing and "
              "submitting is a DECLINE: it writes a revision that changed "
              "nothing, so that \"I looked and decided not to\" is on record "
              "rather than looking like \"I never ran\".")
ADOPT_BUTTON = "Adopt the ticked edits"
DECLINE_BUTTON = "Decline this round (nothing ticked)"

#: Kept small and inline on purpose: R15 says no build step, and the page must
#: still say which of the two it is about to do when this never runs.
SCRIPT = """
(function () {
  var form = document.getElementById('decide');
  if (!form) return;
  var button = document.getElementById('go');
  function refresh() {
    var n = form.querySelectorAll('input[name=op]:checked').length;
    button.textContent = n
      ? %(adopt)s.replace('the ticked edits', n + ' edit' + (n > 1 ? 's' : ''))
      : %(decline)s;
  }
  form.addEventListener('change', refresh);
  refresh();
})();
"""


def _decide_card(run: str, rev: str, plan: schema.PlanResponse,
                 cues: Mapping[str, dict]) -> str:
    """「要刪掉什麼」 -- the proposal list, each with the words it removes.

    `accept --op N` eats an index and, until this page existed, no face could say
    what the Nth proposal was (`docs/first-run-2026-09-10.md` §4 item 5). The
    order here IS that index, because `PlanResponse.ops` is the same list the
    verb enumerates.
    """
    if not plan.ops:
        return ('<div class="card"><h2>Decide</h2><p class="sub">This round '
                "proposed no edits, so there is nothing to adopt or decline."
                "</p></div>")
    rows = []
    for i, op in enumerate(plan.ops):
        piece = read.excerpt(cues, str(op.cue_id), op.char_start, op.char_end)
        when = read.clock(piece.start) if piece else "?"
        rows.append(
            f'<label class="op"><input type="checkbox" name="op" value="{i}"> '
            f'<span class="meta">--op {i} &middot; {html.escape(str(op.op))} '
            f"&middot; {when} &middot; {html.escape(str(op.cue_id))} chars "
            f"{op.char_start:.0f}&ndash;{op.char_end:.0f} &middot; "
            f"{html.escape(str(op.code))}, {html.escape(str(op.origin))}</span>"
            + _excerpt_html(piece) + "</label>")
    return ('<div class="card"><h2>Decide &mdash; '
            f"{len(plan.ops)} proposed edit(s)</h2>"
            f'<p class="sub">{html.escape(ADOPT_HINT)}</p>'
            '<form id="decide" method="post" action="/adopt">'
            f'<input type="hidden" name="run" value="{html.escape(run)}">'
            f'<input type="hidden" name="rev" value="{html.escape(rev)}">'
            + "".join(rows)
            + f'<p><button id="go" type="submit">{html.escape(ADOPT_BUTTON)}'
            "</button></p></form></div>")


def _flash_html(flash: schema.AcceptResponse) -> str:
    paths = "<br>".join(html.escape(p) for p in flash.paths)
    if flash.declined >= 1.0:
        what = (f"<strong>{html.escape(str(flash.source_rev))} &rarr; "
                f"{html.escape(flash.rev)}: declined.</strong> Nothing was "
                "changed, and that decision is now on record as a revision of "
                "its own.")
    else:
        what = (f"<strong>{html.escape(str(flash.source_rev))} &rarr; "
                f"{html.escape(flash.rev)}: {flash.ops:.0f} edit(s) applied."
                "</strong> The page below still shows the round you decided on "
                "&mdash; a round's verdicts belong to the version they were "
                "measured against.")
    return f'<div class="flash"><p>{what}</p><p class="meta">{paths}</p></div>'


def page_round(session: Session, request: Request) -> Reply:
    """One round: trust, findings and the decision, on ONE screen.

    This is the card the rest of the face is arranged around (§16.1 F-4, §16.2
    item 4). Splitting the stretch table off from the findings would put the
    reader back where the CLI left them.
    """
    run = _one(request.query, "run")
    if not run:
        return Reply(400, _page("ltm", '<h1>Which run?</h1><p class="sub">This '
                                'address needs a <code>run</code>. '
                                '<a href="/">Back to the runs</a>.</p>'))
    asked = _one(request.query, "rev")
    if asked:
        rev = asked
        plan = verbs.plan(session.ctx, run=run, rev=rev)
    else:
        rev, plan = _newest_round_with_a_plan(session, run)

    cues: Mapping[str, dict] = {}
    stretches: Sequence[Mapping[str, object]] = ()
    report: Mapping[str, object] = {}
    cue_count: Optional[int] = None
    trouble = ""
    try:
        cues = _cues_for(session, request, run, rev)
        stretches = read.stretches_of(session.ctx.root, run, rev)
        report = read.report_of(session.ctx.root, run, rev)
        # A count only once the record was READ: zero cues read is a fact about
        # the recording (I-22), zero cues because the read failed is not.
        cue_count = len(cues)
    except (read.ReadError, OSError, ValueError) as e:
        # BR-10's shape: a failure announces itself. A page that silently drops
        # the words looks exactly like a round that had none.
        trouble = ('<div class="warn"><p>This round\'s record could not be read, '
                   "so the findings below are positions and numbers only.</p>"
                   f'<p class="meta">{html.escape(str(e))}</p></div>')

    flash, session.flash = session.flash, None
    banner = _flash_html(flash) if flash is not None else ""

    high = next((int(r.high_water) for r in verbs.runs(session.ctx).runs
                 if r.run == run), -1)
    later = ""
    if high >= 0 and layout.serial_number(rev) < high:
        later = (f'<p class="sub">This run has gone on to '
                 f"{html.escape(layout.serial(high))}; you are looking at "
                 f"{html.escape(rev)}, the round these verdicts were measured "
                 "in.</p>")

    body = (f"<h1>{html.escape(run)}</h1>"
            f'<p class="sub">revision {html.escape(rev)} &middot; '
            f'<a href="/">all runs</a></p>' + banner + trouble + later
            + '<div class="grid"><div>'
            + _trust_card(stretches, cue_count, report) + "</div><div>"
            + _findings_card(plan, cues)
            + _decide_card(run, rev, plan, cues) + "</div></div>"
            + "<script>" + SCRIPT % {"adopt": _js(ADOPT_BUTTON),
                                     "decline": _js(DECLINE_BUTTON)}
            + "</script>")
    return Reply(200, _page(f"ltm — {run} {rev}", body))


def _js(text: str) -> str:
    """A Python string as a JavaScript literal, escaped for a `<script>` block."""
    return ('"' + text.replace("\\", "\\\\").replace('"', '\\"')
            .replace("<", "\\u003c").replace("&", "\\u0026") + '"')


def _js_object(mapping: Mapping[str, str]) -> str:
    """A `dict[str, str]` as a JavaScript object literal, safely inline.

    `json.dumps` produces valid JavaScript, but `</script>` inside a string
    would end the block early, so `<` and `&` go out as escapes exactly as they
    do in `_js`. None of the sentences contains either today; the escaping is
    here so that the day one does is not the day the page stops loading.
    """
    return (json.dumps(mapping, ensure_ascii=False, sort_keys=True)
            .replace("<", "\\u003c").replace("&", "\\u0026"))


# --- the decision ---------------------------------------------------------

#: Every state change goes through a verb (§16.0 decision 4). `accept` is the
#: only one this page calls that writes, and `cli.DECLINE` is the spelling both
#: faces use for 「I ticked nothing」 -- one convention, one place.
def adopt(session: Session, request: Request) -> Reply:
    run = _one(request.form, "run")
    rev = _one(request.form, "rev")
    if not run or not rev:
        return Reply(400, _page("ltm", "<h1>Nothing to adopt</h1>"))
    picked = tuple(sorted(int(v) for v in request.form.get("op", ())
                          if v.isdigit()))
    session.flash = verbs.accept(session.ctx, run=run, rev=rev,
                                 ops=picked or cli.DECLINE)
    return Reply(303, location=url_for("/run", run=run, rev=rev))


# --- the four JSON routes (PSM §19, cards GUI-2 and GUI-3) ----------------

def _json(payload: Mapping[str, Any], status: int = 200) -> Reply:
    """A record, on a route that carries records.

    `ensure_ascii=False` so a run id in Chinese survives as itself; the body is
    UTF-8 and says so. Nothing that reaches here has been anywhere near a cue:
    these routes carry ids, counts and codes, which is why none of them is in
    `TEXT_ROUTES` and why the assertion below says they may not be.
    """
    return Reply(status, json.dumps(payload, ensure_ascii=False),
                 content_type="application/json; charset=utf-8")


def _unavailable(what: str) -> Reply:
    """This launch cannot do that, and the answer says which launch it was.

    A read-only launch is a legitimate configuration -- it is what this face was
    for its first week -- so the refusal is a fact about the server, not an
    error the reader can fix by trying again.
    """
    return _json({"code": "not-configured", "subject": what,
                  "detail": "this server was launched without the ability to "
                            "start decodes"}, 503)


def api_intake(session: Session, request: Request) -> Reply:
    """What may be decoded. Filesystem PATHS, never an upload (§19.0 (5))."""
    if session.catalogue is None:
        return _unavailable("intake")
    return _json(session.catalogue.listing())


def api_decode(session: Session, request: Request) -> Reply:
    """Start one. The answer is an id, and the work happens in a child."""
    if session.catalogue is None or session.jobs is None:
        return _unavailable("decode")

    offered = _one(request.form, "path")
    if not offered:
        return _json({"code": "bad-argument", "subject": "path"}, 400)
    try:
        source = session.catalogue.resolve(offered)
    except sources.NotOffered:
        # Deliberately not "no such file": the answer must not tell the caller
        # anything about the filesystem, which is the thing being refused.
        return _json({"code": sources.NotOffered.code, "subject": "path"}, 403)

    # `intake` is instant and in process -- it hashes the file and writes a
    # pointer. It is NOT in the child, because the child's progress stream
    # starts at the decode: an intake inside it would be a phase with no events,
    # and a bad path and a bad decode would come back as one exit code.
    try:
        registered = verbs.intake(session.ctx, path=str(source))
    except schema.AgentError as e:
        return _json({"code": e.code, "subject": e.subject}, 400)

    options = {
        "language": _one(request.form, "language") or None,
        "multilingual": _one(request.form, "multilingual") != "0",
        "vad": _one(request.form, "vad") != "0",
    }
    try:
        job_id = session.jobs.start(registered.run, options)
    except jobs_module.Busy as e:
        # 409, and it NAMES the running job: "busy" with no id is a dead end for
        # whoever is holding the other tab (§19.0 (1)).
        return _json({"code": "busy", "jobId": e.job_id}, 409)
    return _json({"jobId": job_id, "run": registered.run,
                  "recognised": registered.recognised == 1.0})


def api_progress(session: Session, request: Request) -> Reply:
    """Poll. ~1 s is the shape the stream already supports (§19.0 (2))."""
    if session.jobs is None:
        return _unavailable("progress")
    job_id = _one(request.query, "job")
    if not job_id:
        return _json({"code": "bad-argument", "subject": "job"}, 400)
    try:
        return _json(session.jobs.status(job_id))
    except jobs_module.NoSuchJob:
        # 404 and a code, not a blank: a job id from a previous launch is the
        # ordinary way to get here, and the page has to be able to say so
        # rather than showing a progress bar that never moves.
        return _json({"code": "no-such-job", "subject": "job"}, 404)


def api_cancel(session: Session, request: Request) -> Reply:
    """Ask. The ANSWER comes back through `/api/progress`, from the child.

    This route never reports `cancelled`; it reports that the request was made.
    A page told "cancelled" while the engine is still decoding is the failure
    R13 was written against, so the distinction is kept at every layer that
    touches it.
    """
    if session.jobs is None:
        return _unavailable("cancel")
    job_id = _one(request.form, "job")
    if not job_id:
        return _json({"code": "bad-argument", "subject": "job"}, 400)
    try:
        return _json(session.jobs.cancel(job_id))
    except jobs_module.NoSuchJob:
        return _json({"code": "no-such-job", "subject": "job"}, 404)


# --- the route table ------------------------------------------------------

#: Every path this face answers. A literal, because INV-23's danger is the route
#: nobody registered, and a table assembled at runtime cannot be read by a test.
ROUTES: Mapping[str, Handler] = {
    "/": page_runs,
    "/run": page_round,
    "/adopt": adopt,
    "/api/intake": api_intake,
    "/api/decode": api_decode,
    "/api/progress": api_progress,
    "/api/cancel": api_cancel,
}

#: **INV-23's list.** A route outside it may not return `Cue.text`, and
#: `tests/test_ui_serve.py` walks every registered handler to say so -- with the
#: two controls that ruling names: an unregistered route that deliberately
#: returns text must be caught, and a registered one must not.
TEXT_ROUTES = frozenset({"/run"})

#: Routes that change something. Everything else refuses a POST, so a state
#: change cannot arrive by a link somebody followed.
POST_ROUTES = frozenset({"/adopt", "/api/decode", "/api/cancel"})

#: **Routes that answer with a record rather than a page.** A literal, for the
#: same reason `TEXT_ROUTES` is one: INV-23 names 「多開一條 JSON 路由比較好接」 as
#: the change nobody blocks at review, and this increment added four of them at
#: once. Listing them here is what lets the assertion below be written at all.
JSON_ROUTES = frozenset({"/api/intake", "/api/decode", "/api/progress",
                         "/api/cancel"})

assert TEXT_ROUTES <= set(ROUTES), (
    "TEXT_ROUTES names a route that does not exist, so the list and the table "
    "have already drifted")
assert POST_ROUTES <= set(ROUTES)
assert JSON_ROUTES <= set(ROUTES)
assert not (JSON_ROUTES & TEXT_ROUTES), (
    "a JSON route is in TEXT_ROUTES. These routes carry ids, counts and codes; "
    "one of them carrying transcript text would be the exact widening INV-23 "
    "exists to make somebody argue for in the open")


def _one(mapping: Mapping[str, Sequence[str]], key: str) -> Optional[str]:
    values = mapping.get(key) or ()
    return values[0] if values else None


def _forbidden() -> Reply:
    return Reply(403, _page("ltm", "<h1>Not this way</h1><p class=\"sub\">"
                            "This page is only reachable from the address the "
                            "launcher printed, in the browser on this machine. "
                            "The token is regenerated every launch.</p>"))


def _refusal(e: schema.AgentError) -> Reply:
    status = 404 if e.code in ("no-such-run", "no-such-revision") else 400
    subject = f" ({e.subject})" if e.subject else ""
    return Reply(status, _page("ltm", f"<h1>{html.escape(e.code)}"
                               f"{html.escape(subject)}</h1>"
                               '<p class="sub"><a href="/">Back to the runs'
                               "</a></p>"))


def handle(session: Session, request: Request) -> Reply:
    """Token, cookie, route, method -- in that order, and every failure is said.

    The token is exchanged for a cookie on `/` and the browser is redirected to a
    bare `/`, so the token does not stay in the address bar (§16.0 decision 3).
    Every route, including `/`, is `403` without the cookie.
    """
    if (request.path == "/" and request.method == "GET"
            and _valid(_one(request.query, TOKEN_PARAM), session.token)):
        return Reply(303, location="/",
                     set_cookie=(f"{COOKIE}={session.token}; Path=/; HttpOnly; "
                                 f"SameSite=Strict"))
    if not _valid(request.cookie, session.token):
        return _forbidden()

    route = ROUTES.get(request.path)
    if route is None:
        return Reply(404, _page("ltm", "<h1>No such page</h1>"
                                '<p class="sub"><a href="/">Back to the runs'
                                "</a></p>"))
    if (request.path in POST_ROUTES) != (request.method == "POST"):
        return Reply(405, _page("ltm", "<h1>Wrong method</h1>"))

    try:
        return route(session, request)
    except schema.AgentError as e:
        return _refusal(e)
    except Exception as e:                       # noqa: BLE001
        # A silent blank screen is a defect: the reader gets a page that says
        # what broke, and the process gets the traceback it needs to be fixed.
        traceback.print_exc()
        return Reply(500, _page(
            "ltm", "<h1>This page could not be built</h1>"
            f'<p class="sub">{html.escape(type(e).__name__)}: '
            f'{html.escape(str(e))}</p><p class="meta">The traceback is on the '
            "terminal that started this server.</p>"))


def _valid(offered: Optional[str], token: str) -> bool:
    return bool(offered) and secrets.compare_digest(offered or "", token)


# --- the socket -----------------------------------------------------------

class RequestHandler(http.server.BaseHTTPRequestHandler):
    """The thin adapter. Everything it decides, it decides in `handle`."""

    protocol_version = "HTTP/1.1"
    server_version = "ltm"
    sys_version = ""

    #: Set by `start`, so a handler can reach the session without a global.
    session: Session

    def do_GET(self) -> None:                    # noqa: N802
        self._respond("GET", {})

    def do_POST(self) -> None:                   # noqa: N802
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            self._write(Reply(413, _page("ltm", "<h1>Too much</h1>")))
            return
        raw = self.rfile.read(length).decode("utf-8", "replace")
        self._respond("POST", urllib.parse.parse_qs(raw, keep_blank_values=True))

    def _respond(self, method: str, form: Mapping[str, Sequence[str]]) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        request = Request(
            method=method, path=parsed.path,
            query=urllib.parse.parse_qs(parsed.query, keep_blank_values=True),
            form=form, cookie=_cookie_value(self.headers.get("Cookie")))
        self._write(handle(self.session, request))

    def _write(self, reply: Reply) -> None:
        payload = reply.body.encode("utf-8")
        self.send_response(reply.status)
        self.send_header("Content-Type", reply.content_type)
        self.send_header("Content-Length", str(len(payload)))
        # Nothing here is cacheable: the store changes underneath it, and a
        # cached page of verdicts is a page that can disagree with the store.
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        if reply.location:
            self.send_header("Location", reply.location)
        if reply.set_cookie:
            self.send_header("Set-Cookie", reply.set_cookie)
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args) -> None:
        """Quiet by default; the launcher's terminal is where the URL lives.

        Failures are not quiet -- `handle` prints the traceback and the page
        says so. A request log would bury both.
        """
        return


def _cookie_value(header: Optional[str]) -> Optional[str]:
    """Our cookie out of a `Cookie:` header, without importing a cookie jar."""
    for part in (header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE:
            return value
    return None


class Server(http.server.ThreadingHTTPServer):
    """Threaded so one slow page cannot wedge the tab, daemon so it dies with us.

    `allow_reuse_address` is off: this face binds an ephemeral port that nobody
    else has any business holding, and reusing a bound address would turn 「that
    port is taken」 into a silence.
    """

    daemon_threads = True
    allow_reuse_address = False

    def handle_error(self, request, client_address) -> None:
        """A reader closing the tab is not an incident.

        Measured by fetching a page and letting the client hang up: the default
        handler printed a forty-line `ConnectionResetError` traceback onto the
        terminal the user launched from. Closing a tab is the most ordinary
        thing a reader does, and a tool that answers it with a stack trace
        teaches its user that its output means nothing.

        **Only that class is swallowed.** Anything else keeps the default's full
        report, because a face that failed silently would be the other defect --
        `handle` already prints the traceback for a route that raised, and this
        is the same rule one layer down.
        """
        if issubclass(sys.exc_info()[0] or Exception, ConnectionError):
            return
        super().handle_error(request, client_address)


@dataclass
class Launched:
    """A bound server, its token, and the address to open."""

    server: Server
    session: Session
    token: str
    #: Set for exactly as long as `serve_forever` is inside its loop. It exists
    #: because `BaseServer.shutdown()` waits on an event that only
    #: `serve_forever` ever sets: calling it on a server that was bound but
    #: never served **blocks the caller forever**, which is a deadlock a test
    #: reaches before a user does. Found by running it, not by reading it.
    serving: threading.Event = field(default_factory=threading.Event)

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}/?{TOKEN_PARAM}={self.token}"

    def serve_forever(self) -> None:
        self.serving.set()
        try:
            self.server.serve_forever(poll_interval=0.2)
        finally:
            self.serving.clear()

    def stop(self) -> None:
        """End the loop if there is one, then close the socket.

        Both, and in this order: `shutdown` only ends `serve_forever`, and a
        server that stopped serving while still holding its socket is a port
        that is gone from the product's point of view and present from the
        operating system's.
        """
        if self.serving.is_set():
            self.server.shutdown()
        self.server.server_close()
        # §19.0 (6): a job dies with the server. The request is all this can do
        # -- the child discards its own partial output (§8.2) and a parent that
        # killed it would take that away -- so a decode may outlive the socket
        # by one poll interval and then stop itself.
        if self.session.jobs is not None:
            self.session.jobs.shutdown()


def start(ctx: Optional[verbs.Context] = None, *, port: int = 0,
          decodes: bool = True,
          audio_roots: Sequence[pathlib.Path] = ()) -> Launched:
    """Bind, and mint a token for THIS launch.

    `port=0` is an ephemeral port (INV-23): a fixed port is a promise to whatever
    else on this machine wants to answer on it, and the address is printed
    anyway. The token is `secrets.token_urlsafe`, regenerated here rather than
    stored, so closing the process is what ends access.

    `decodes=False` gives back exactly the face this was before 2026-09-16: the
    four write routes answer `not-configured` and nothing can start a child. It
    is a real configuration and not only a test convenience -- "look at what is
    already there" is what most of this page is for.
    """
    ctx = ctx or verbs.Context()
    session = Session(ctx=ctx, token=secrets.token_urlsafe(32))
    if decodes:
        session.jobs = jobs_module.Jobs(ctx.root)
        session.catalogue = sources.Catalogue(ctx.root, audio_roots)
    handler = type("BoundHandler", (RequestHandler,), {"session": session})
    server = Server((HOST, port), handler)
    return Launched(server=server, session=session, token=session.token)


# --- the launcher ---------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    """`python -m ltm.ui.serve`. **Not an `ltm serve` subcommand** (§16.0 (1)).

    `tests/test_two_faces.py` asserts the parser's subcommands ARE the verb
    table, and this face is not a verb: adding a subcommand with nothing behind
    it would mean widening that assertion with a CLI-only allowlist, which is the
    shape A-8 refuses. A separate module entry point costs a longer command and
    costs the invariant nothing -- and it makes it structurally impossible for an
    agent to reach this face through the verb table.
    """
    parser = argparse.ArgumentParser(
        prog="python -m ltm.ui.serve",
        description="Open the transcript review page in a browser on this "
                    "machine. It is served on 127.0.0.1 only and lives exactly "
                    "as long as this process.")
    parser.add_argument("--root", type=pathlib.Path, default=None,
                        help="the output root (default: the product's own)")
    parser.add_argument("--port", type=int, default=0,
                        help="0 (default) asks the OS for a free port")
    parser.add_argument("--no-browser", action="store_true",
                        help="print the address instead of opening it")
    parser.add_argument("--audio-root", type=pathlib.Path, action="append",
                        default=None, metavar="DIR",
                        help="a folder of recordings the page may start decodes "
                             "on. Repeatable. WITHOUT it the page can only "
                             "re-decode what `ltm intake` already registered -- "
                             "'the page can start decodes' and 'the page can "
                             "see your disk' are two different permissions")
    parser.add_argument("--read-only", action="store_true",
                        help="the face as it was before it could start decodes: "
                             "the four write routes refuse")
    ns = parser.parse_args(argv)

    ctx = verbs.Context(root=ns.root) if ns.root else verbs.Context()
    launched = start(ctx, port=ns.port, decodes=not ns.read_only,
                     audio_roots=tuple(ns.audio_root or ()))
    print(launched.url)
    print(f"  store: {ctx.root}")
    if ns.read_only:
        print("  read-only: this launch cannot start decodes")
    else:
        roots = ns.audio_root or []
        print("  can start decodes on: "
              + (", ".join(str(p) for p in roots) if roots
                 else "recordings already registered with `ltm intake` "
                      "(no --audio-root was named)"))
    print("  This address works in this browser session only, and only while "
          "this process is running. Ctrl-C stops it.")
    # **Flushed, and that is not a nicety.** Python block-buffers stdout the
    # moment it is not a terminal, so a launcher that prints an address and then
    # blocks forever prints it into an 8 KB buffer nobody will ever see: measured
    # here by redirecting the output to a file and getting an empty file while
    # the server was up and answering. The address is the entire point of this
    # process, and a redirected or piped launch is an ordinary way to start one.
    sys.stdout.flush()
    if not ns.no_browser:
        webbrowser.open(launched.url)
    try:
        launched.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped; the port is gone")
    finally:
        launched.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
