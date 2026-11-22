# AGENTS.md — driving local-transcript-maker from a program

This file is the machine-facing contract. It is normative for anything that calls
the product without a person watching. `README.md` is the human's entry point and
is deliberately not a substitute for this one.

The product makes a machine transcript's uncertainty inspectable. It does not
correct transcripts and it does not judge meaning.

---

## 1. Two faces, one implementation

```
ltm.agentapi.verbs.IMPLEMENTATIONS      # verb name -> callable
ltm.ui.cli.VERBS is IMPLEMENTATIONS     # asserted by a test, not a convention
```

`tests/test_agentapi_schema.py::test_both_faces_call_one_function` asserts the
identity. There is no second `decode` that agrees today and diverges at the first
bug fix. Call the verbs directly, or drive the CLI with `--json`; the answer is
produced by the same function either way.

In-process. **`verbs.call` is the agent entry point, not the functions
themselves**: it is what enforces `HUMAN_ONLY` and what turns every failure into a
code. Calling `verbs.accept(...)` directly works — the function exists once and the
CLI reaches it — and doing so from an agent is bypassing the boundary, not using
an alternative to it.

```python
from ltm.agentapi import verbs
ctx = verbs.Context(root=pathlib.Path("C:/Users/me/TranscriptDesk"),  # optional
                    on_progress=my_sink,        # optional, ProgressEvent -> None
                    cancellation=my_cancel)     # optional, progress.Cancellation
got  = verbs.call(ctx, "intake", path="C:/Users/me/talk.aac",
                  from_url="https://example.com/talk")   # optional, see §3
# got.came_from_count says how many sources the run records; the URL itself is
# on disk (`_intake/<run>.json`, key `came_from`), never in a response (INV-12).
done = verbs.call(ctx, "decode", run=got.run)
plan = verbs.call(ctx, "plan", run=got.run, rev=done.rev)
verbs.call(ctx, "accept", run=got.run)          # allowed since 2026-09-09 (A-21)
```

`verbs.IMPLEMENTATIONS` is every verb; `verbs.AGENT_VERBS` is the same objects
minus `HUMAN_ONLY`. Both are derived from one table, so a verb cannot be added to
one and forgotten in the other.

Out of process (`pip install -e .` gives the `ltm` entry point; otherwise put
`src` on `PYTHONPATH` and use `python -m ltm`):

```
ltm --json --root <ROOT> decode <RUN> [--no-vad] [--language zh --no-multilingual]
                                      [--languages en,zh]
                                      [--vad-threshold 0.3] [--vad-min-silence-ms MS]
                                      [--vad-speech-pad-ms MS]
                                      [--compute-type int8] [--device cpu]
```

`--languages en,zh` (I-28, 2026-10-21) narrows per-window detection to an
expected set; it needs per-window detection on and nothing pinned
(`bad-argument` / `language-set-with-pin` otherwise). The language slots still
vote over every language, so an out-of-set language still shows as a
disagreement. The `--vad-*` knobs (I-27) override faster-whisper's VadOptions
one by one; unset = the library default, and any knob with `--no-vad` is
`bad-argument` / `vad-knobs-without-vad`. `_origin.json` records the child's
`language_set` and `vad_parameters` per revision (`null` = none).

`multilingual` is ON by default (PIM v1.3 E-3): the engine re-detects the
language per decode window. A pinned `--language` without `--no-multilingual`
is refused BEFORE the engine starts with `bad-argument` / subject
`language-with-multilingual` — the engine would override the pin while reporting
that it took. `_origin.json` records `multilingual` per revision; when it is
true, `language_detected` there describes the first 30-second window only, so
read the language from the plan's stretch table, never from that field.

`--if-no-gpu {ask,cpu,fail}` exists on `decode` and is a HUMAN-face flag: under
`--json` there is never a prompt and the flag is not consulted. An agent handles
that failure by calling again with `int8`/`cpu` — see §5.

Exit codes and where the answer goes:

| Exit | Meaning | `--json` | without `--json` |
|---:|---|---|---|
| `0` | success | the response as JSON on **stdout** | prose on stdout |
| `2` | an `AgentError` refusal | an `ErrorResponse` as JSON on **stdout**, stderr empty | prose on stderr |
| `2` | a `DetectError` / `ValueError` refusal | **prose on stderr** — these carry their reason in the message and have no code | same |
| `130` | `SIGINT` became a cancel request | **prose on stderr** | same |

So: on exit `2` with `--json`, empty stdout means the refusal had no code and the
reason is on stderr in English. Treat that as unmapped rather than parsing it. The
fully machine-readable path is in-process `verbs.call`, where every failure is an
`AgentError` with a code from the closed set.

---

## 2. Environment

| Variable | Meaning | Default |
|---|---|---|
| `LTM_ROOT` | output root, when `--root` / `Context(root=...)` is not given | `<home>\TranscriptDesk` |
| `LTM_SIDECAR_PYTHON` | interpreter that has faster-whisper installed | `<checkout>\runtime\asr-py\Scripts\python.exe` |
| `LTM_MODEL_DIR` | model weights directory | `<checkout>\runtime\models\faster-whisper-large-v3` |

`<checkout>` is the repository root, derived from where the package sits; from an
installed wheel it does not resolve, so set both variables there.

`ffprobe` must be on `PATH`: the decode denominator is measured with it and is
never taken from the engine (see §7, E-2). The product itself imports only the
standard library. Apart from the caller-supplied output root, no shipping file
names a path outside the checkout.

**No network.** There is no network call on the core path, by invariant (INV-18).
An agent must not add one, must not offer to upload a transcript, and must not
route the text through a remote model. Recording and transcript text never leave
the machine (R1, INV-11).

### 2.1 Finding the tool, and finding this file

```
ltm agent which              # is `ltm` installed, and where; which interpreter
ltm agent which --json       # {"onPath","path","interpreter","importable"}
ltm agent guide              # prints THIS file
ltm agent guide --path       # prints where this file is
ltm agent guide --json       # {"guide","guidePath","which"}
```

`agent` is a **namespace, not a verb**: it is the one sub-command of `ltm` that
is not in `AGENT_VERBS`, it has no response type, and nothing under it reads or
writes a store. It answers questions about the tool itself, which a caller has
before it can call a verb at all. `tests/test_two_faces.py` holds both halves of
that claim — the parser's sub-commands are `AGENT_VERBS` plus exactly this one
name, and a property test reads this namespace's module and requires that it
never imports a store, an engine or a detector, never builds a `Context`, and
never calls a verb.

**`onPath` and `importable` are different facts and may disagree.** A checkout
with the package importable and nothing on `PATH` is the ordinary state of this
repository: `python -m ltm ...` works there and `ltm ...` does not. Read the two
separately; do not treat a false `onPath` as "the product is not here".

**This file ships inside the package** (`ltm/AGENTS.md`) and there is exactly one
copy. The repository root holds a pointer at it and no contract text. An
installed `ltm` has no repository to read, which is why the contract lives where
the code does — so `ltm agent guide` answers identically from a checkout and from
an installed wheel. **Never cache a copy of this file**: ask for it.

**Nothing here installs anything, and the product never edits `PATH`.** `which`
reports; installing is a command a person runs (`README.md` has it).

---

## 3. Verbs

| Verb | Required | Optional | Returns |
|---|---|---|---|
| `intake` | `path` | `from_url`, `from_note` | `IntakeResponse` |
| `decode` | `run` | `audio`, `vad`, `language`, `multilingual`, `condition_on_previous_text`, `language_slots`, `compute_type`, `device`, `duration_seconds`, `language_set`, `vad_threshold`, `vad_min_silence_ms`, `vad_speech_pad_ms` | `DecodeResponse` |
| `detect` | `run` | `rev`, `enabled`, `terms`, `word_level`, `lexicons` | `DetectResponse` |
| `plan` | `run` | `rev` | `PlanResponse` |
| `accept` | `run` | `rev`, `ops`, `decline`, `candidates`, `picked_by` | `AcceptResponse` |
| `runs` | — | — | `RunsResponse` |
| `show` | `run` | `rev` | `ShowResponse` |
| `open` | `path` | — | `OpenResponse` (returns the path; launches nothing) |
| `cite` | `run`, `rev`, `granted_by` | `scope`, `method` | `CiteResponse` |
| `cancel` | `state` | — | `CancelResponse` |

`rev` omitted means the run's high-water revision.

**`glossary` reads correction lexicons** (2026-10-30). `lexicons` is a tuple of
paths to `ltm-lexicon/1` JSON files (format: `ltm/detect/lexicon.py`); naming one
turns `glossary` on, and every `*.json` directly in `<root>/_詞表/` is read too.
A finding's `evidence.kind` says what matched: 1 a declared wrong form (severity
`warn`, actions `replace`/`keep`, and ONE `replace` op in the plan), 2 homophone,
3 near-homophone, 4 Latin sound-alike (severity `info`, actions `check`/`keep`, no
op). **`accept` with no `ops` takes every op, glossary replaces included** — pass
`ops` when only some should land. The replacement text is on disk in the plan,
never in a response (INV-12). An unreadable lexicon is `bad-argument: lexicon`.

Also on every glossary finding: `evidence.script`, the cue's language case read
from its own text (1 zh-only, 2 mixed, 3 en-only; no user label is needed and the
decode's `stretch_table` cannot supply it — a mixed talk measures `zh` throughout),
and, when `<root>/_詞表/_字頻/*.csv` exists (columns `字`, `字頻`; the user's NAER
files, never shipped), `evidence.prior` — a character-frequency log ratio that
RANKS and never decides (measured AUC 0.68).

**A lexicon term written in simplified characters is not a finding** (2026-11-23).
验收 for 驗收 is the right word in another script: `glossary` passes it over and
`DetectResponse.passed_over["script-variant"]` (and the plan file's `passed_over`)
counts how many. The key is present, zero included, whenever `glossary` ran, and
absent when it did not. One direction only: a character that is itself a correct
traditional one (發, 后, 系) never stands in for another (髮, 後, 係), so 頭發 for
頭髮 is still a finding. The displayed text is never converted.

**`script-switch` says where the text left the expected Chinese script**
(2026-11-23). Enable it by name or by passing `script="hant"|"hans"` (CLI
`--script`; default expectation `hant`; any other value is `bad-argument` /
`script`, refused before the store is read). One `info` finding per run of the
other script (at least 2 cues; cues of neither script do not break a run),
anchored at offset 0 of the run's first cue, actions `check`/`keep`, never an op.
Evidence: `cues`, `last` (cue index), `start`, `end`, `simplified`,
`traditional`, `expect` (0 hant, 1 hans). `DetectResponse.script_cues` (and the
plan file's `script_cues`) counts cues by script (`hant`, `hans`, `none`); empty
when the detector did not run. Not in the default set.

**A sound-alike's replace is a dormant candidate** (2026-10-30). `PlanResponse.
candidates` lists them, position and code only, in the order `accept` indexes.
`accept` never applies a candidate it was not given: `candidates=(N, ...)` applies
those; with no `ops` alongside, it applies ONLY those. `picked_by` (`human`, or
`advisor:<model>@<prompt version>`) is written into the edit record. Bad index →
`bad-argument: candidate`; overlapping selection → `bad-argument: overlap`.

**`accept` is agent-callable** (user ruling 2026-09-09, A-21). `HUMAN_ONLY` is
empty; the mechanism remains and is read at call time, so a verb put back into it
is refused with `human-only` rather than `unknown-verb`. INV-3 and INV-4 did not
move: no write without a script, and the revert is proven in memory before
anything is written.

**Every revision records which face made it.** `_origin.json`'s `revisions[]`
entries carry `face: "human" | "agent"`. An ABSENT `face` means the round predates
the field — **do not read it as `human`**. This is what replaced the refusal:
while adoption was human-only the guarantee was enforced, and now it is
observable instead.

**`cite` requires a human authorisation record** (R14 / INV-20): `granted_by`,
a timestamp, a `scope` of exactly one `<run>/<revision>`, and a `method` from
`("ui-dialog", "cli-flag", "agent-relayed")`. There are no standing grants. An
agent relaying a grant must use `method="agent-relayed"` and must name the human
who gave it; fabricating a grant is the one failure mode this record exists to
make visible afterwards.

**`decode` settings that change the transcript.** `vad=True`, `language=None` and
`multilingual=True` are the shipping defaults. `docs/`'s measured cue counts were
produced with `vad=False, language="zh"` and differ substantially. An agent
comparing against a number in `docs/` must pass the same settings, and must read
them back from `_origin.json` rather than assume them — see §6.

**Reproducing those numbers needs a third flag now, and asking for two of them
is refused.** `language="zh"` alone is `bad-argument` / `language-with-multilingual`
before the engine starts (E-3(f)), because per-window detection would override the
pin while the record said it took. The settings that produced `docs/`'s counts are
therefore `vad=False, language="zh", multilingual=False`. *(2026-09-16: this
paragraph used to name only the first two, so an agent following it was refused by
the product — a machine-facing contract asking for something the product rejects.
Found while answering round 12; neither verifier reads this file.)*

**`compute_type` and `device` are closed sets checked before the child is
spawned.** `verbs.COMPUTE_TYPES` is `("float16", "int8_float16", "int8",
"float32")` and `verbs.DEVICES` is `("auto", "cpu", "cuda")`; `language` must
match `verbs.LANGUAGE_CODE` (`[a-z]{2,3}`). A value outside the set is
`AgentError("bad-argument", subject="compute-type" | "device" | "language")` —
refused here, because these travel to another interpreter where a typo comes
back as `crashed` and costs a model load first. **`float16` is the default and
it needs a working GPU**; a machine without one uses `compute_type="int8",
device="cpu"` and gets the same transcript more slowly. Both values are recorded
per revision: numeric precision is one of the things that decides whether two
decodes of one file agree (§6), so a record naming the language but not the
arithmetic is half a record.

---

## 4. Response types are closed, and so are their strings

Every response is a frozen dataclass in `ltm.agentapi.schema`. **INV-12: no
free-text field reaches an agent.** Enforced structurally, not by review:

- A string-typed field is either an id field (`path`, `run`, `rev`, `id`,
  `sha256`, and their plurals by rule) or a `Closed` subclass.
- `Identifier` accepts `[A-Za-z0-9][A-Za-z0-9._:-]*` and nothing else.
- `StateName` accepts membership in `progress.STATES` — which is how
  `failed(engine)` is expressible without widening the identifier pattern.
- `tests/test_agentapi_schema.py` walks every type in `RESPONSES` and fails on a
  field that is neither.

Consequence for a caller: a response can be logged, matched and dispatched on
without sanitising. Nothing in it is prose, so nothing in it can carry an
instruction. Transcript text is reachable only by opening the files a response
names.

Numbers are `float` even when they are counts and even when they are booleans
(`health["vad_enabled"] == 1.0`). This is deliberate: one numeric type means a
consumer never has to ask which fields are booleans wearing integer clothes.

---

## 5. Refusals

`AgentError(code, subject)` — `code` from `ErrorResponse.CODES`, `subject` an
`Identifier` or `None`:

| Code | Means |
|---|---|
| `human-only` | the verb has no agent form. **No verb is in this set today** (A-21); the code and the mechanism remain for the next one that needs to be a person's act |
| `unknown-verb` | no such verb |
| `bad-argument` | arguments do not fit the verb; `subject` names which |
| `no-such-run` | no run by that name under this root |
| `no-such-revision` | that revision does not exist or left no plan |
| `token-layer-absent` | word-level alignment was required and milestone 1 has no token layer. **Nothing was written** |
| `grant-missing` | `cite` without a valid authorisation record. **Nothing was written** |
| `engine-failed` | the sidecar did not finish; `subject` is the engine failure code |
| `engine-failed` / `compute-unsupported` | **the one engine failure an agent should answer rather than report.** This machine cannot do the arithmetic that was asked for — almost always `float16` with no usable GPU. Call `decode` again with `compute_type="int8", device="cpu"`: same transcript, several times slower. That second call IS the consent; there is no prompt on this face and no automatic fallback anywhere. The code is a measured capability (the child asks ctranslate2 what this machine supports), never a reading of an error message |
| `cancelled` | cancelled; partial output discarded, nothing written |

The run id is never echoed into a refusal: it is a filename plus a hash, so it can
be CJK or contain spaces, and `subject` is an `Identifier`.

Engine failure codes (`EngineFailure.CODES`) name **what was observed**, never a
guessed cause: `silent`, `exited`, `crashed`, `unparsable`, `no-report`,
`interpreter-missing`, `audio-missing`, `model-missing`, `vad-kept-impossible`,
`probe-missing`, `probe-failed`, `compute-unsupported`, `cancelled`. `silent`
means no output before the deadline and carries no theory about why.
(`compute-unsupported` was missing from this list while the table ten lines above
already gave it a row of its own -- one file contradicting itself, found in
round 13, R13a-S2.) The child's stderr goes to a file; the failure carries the
path, never the text.

**`cancelled` is the code for a stop this caller asked for**, added 2026-09-16.
Until then a cancelled decode came back as `exited`, which is what a child that
died on its own also reports — so a person who pressed cancel was told their run
had crashed, and the only way for a caller to tell the two apart was to remember
that it had sent a cancel. That is the parent deciding an outcome that is the
child's to report, which is exactly what R13 separates. `decode` now maps it to
the refusal `cancelled` (with `subject` = the phase it stopped in), the same
answer `detect` has always given; everything else still maps to `engine-failed`.

---

## 6. The store

```
<root>/
  _intake/<run>.json                    pointer to the media; the media is NOT copied
  校讀/<run>/
    _origin.json                        identity, revisions, per-revision decode provenance
    時軸/<stem>.<rev>.<lang>.srt
    全文/<stem>.<rev>.<lang>.txt
    _findings/<rev>.json
    _plans/<rev>.json
    _edits/<rev>.json
  _citable/…                            only with a grant
```

`<run>` is `<stem>-<hash8>` where the hash is of the media **content**. The store
vocabulary under the root is constant, not configurable.

`_origin.json` carries `schema: "ltm/origin/2"`. `read_origin` refuses any other
value rather than reading it partially. **`decode` is a mapping keyed by
revision**:

```json
{"decode": {"r000": {"vad": true, "language_asked": null,
                     "language_detected": "zh", "cues": 699,
                     "compute_type": "float16", "device": "auto",
                     "text_sha256": "…", ...},
            "r001": {"vad": false, "language_asked": "zh", ...}}}
```

`text_sha256` fingerprints the transcript text, and it is the field to compare
when asking whether two revisions are the same decode. **Do not use the cue
count for that.** A decode on this machine is not bit-reproducible: **seven runs
with identical arguments produced seven distinct transcripts** (661–728 cues),
and **two of the seven proposed deletions while five proposed none**
(`docs/evidence/positive_control_2026-09-09.txt`). An agent that re-decodes and
compares counts will report "same result" for transcripts that differ.

**Consequence for anything automated: one decode does not establish that a
transcript is clean.** `findings == 0` is a statement about one sample from a
distribution whose median largest-same-text-group sits exactly on S3's threshold
of five. An agent that needs a dependable answer decodes more than once and
reports the intersection, and says how many times it ran. The product does not
do this for you, and whether it should is a user decision that has not been
taken (`docs/uat-2026-09-09.md` A-4).

Read it with `ltm.store.write.decode_record(record, rev)`. Never index
`record["decode"]` directly: it is not a flat mapping and was one in schema `/1`,
which is exactly the defect the version bump marks (`docs/issues-2026-09-09.md`,
I-5).

Revisions are append-only. A round that changed nothing is still a round and still
consumes a serial — declining is a conclusion, and a store where "we decided not
to" is invisible cannot be audited.

---

## 7. Facts a caller must not re-derive

- **Duration comes from `ffprobe`, never from the engine.** On a `--no-vad` run
  the engine's own number was measured 10.00 s longer than the audio; a progress
  bar that reaches 103% is worse than one that stalls at 99%.
- **`agreement < 0.5` is `undetermined`.** A definition, not a tuned threshold.
  Do not re-interpret an `undetermined` stretch as a low-confidence language.
- **An empty `slots` tuple is ambiguous on its own.** `EngineResult.language_slots`
  (and `_origin.json`'s `language_slots`) is what separates "not asked" from
  "asked and delivered nothing". Never read an empty tuple as disagreement.
- **`n >= 2` signals → Finding; `n == 1` → Note; `n == 0` → recorded, not
  surfaced.** A Note is not a weak Finding: it is never proposed for deletion.
  **Two clauses were added on 2026-09-09 and they are mirror images — both are
  properties of the CANDIDATE's kind, never adjustments to `n`:**
  - **A-20:** a run of 4 identical cues is nominated and is a Note *whatever `n`
    says* (`Scored.capped`, `Note.reason == "near-threshold"`).
  - **A-25:** a run of 5+ identical cues is a Finding *whatever `n` says*
    (`Scored.stands_alone`). **Do not expect `evidence["signals"] >= 2` on a
    Finding** — an `engine-loop` Finding over a full run may carry `signals: 1`,
    and that is the rule working, not a bug. Length is itself evidence and `n`
    had no way to say so.

  `Note.reason` is a closed set: `single-signal`, `no-action`, `near-threshold`.
- **A decode with `counts["cues"] == 0` succeeded** (I-22, 2026-09-27). It is a
  round like any other; read `health["vad_kept"]` for why it has no words:
  `0.0` = VAD found no speech in the whole file, a positive number = VAD kept
  that many seconds and the engine still wrote nothing, `-1.0` = VAD did not run
  or did not report, so nothing says whether anyone speaks. Do not retry it as a
  failure, and do not answer it with `vad=False` unasked: measured, VAD off
  writes invented text over silence.
- **A `sparse-cue` Finding is about TIME as much as text** (I-23, 2026-09-27).
  A cue ≥ 15 s long at ≥ 3 s per unit (CJK character or Latin word) is either
  a real sentence whose timestamps were stretched over a pause, or 15+ s of
  sound the engine wrote almost nothing for. It never carries an edit op: do not
  propose deleting its words, and do not seek to its `start` expecting speech
  there. It is in the default set, beside `engine-loop`.
- **An `uncovered-span` Finding is a stretch of ≥ 60 s with no cue at all**
  (I-27, 2026-10-21). Its evidence carries `start`, `end`, `seconds` and
  `vad_enabled`; the span is EMPTY and sits at the edge of the neighbouring cue
  (offset 0 of the cue after the gap, or the end of the last cue for a tail).
  VAD dropping quiet speech is the likeliest cause, a real silence the other;
  the action is to listen and, if someone is talking, re-decode with `vad=False`
  or a lower `vad_threshold` — never unasked, for the reason above. In the
  default set.
- **A `near-threshold` note is the one to re-read, not the one to dismiss.** It
  means the evidence was there and the run was one cue short of the bar — on a
  different decode of the same audio it would have been a Finding. That is not a
  metaphor: seven decodes with identical arguments produced seven distinct
  transcripts whose largest same-text group ranged 2–10.
- **Read the reason off `SpanRef.reason`, and read its ABSENCE as absence.**
  `plan` carries it per note (A-24). `None` means the round was written before
  the field existed — it is a third value, not `single-signal`, and a caller that
  collapses the two is inventing a measurement. **Never infer the reason from
  `n`**: the near-threshold case is exactly the one where `n >= 2` and the
  disposition is still a note.
- **Only repetition nominates.** Nothing is flagged for reading oddly. If a caller
  wants semantic judgement, milestone 1 does not have it and must not be told it
  does.
- **An unverified marker over an empty population is said in words, not printed as
  `0`.** `unverified_scope` returns the population beside the count for this
  reason; a bare `0` reads as "everything passed", which is its opposite.

---

## 8. Progress and cancellation

`ProgressEvent(state, phase, elapsed, done=None, total=None, unit=None, name=None,
detail={})`. `name` is an identifier ("which model", "which detector"), `detail`
holds numbers only; neither can carry a line of transcript, which is INV-12
enforced where the value is made rather than where it is serialised.

*(2026-09-16: this line used to stop at `name`, and the PSM's copy of the same
tuple used to stop at `unit` and omit `name`. One type, three documents, three
different field lists, each missing a different field — read the dataclass in
`src/ltm/core/progress.py` if this line and the code ever disagree again.)*

States (12, read off `progress.EDGES`, never listed separately):
`queued`, `decoding`, `detecting`, `proposing`, `clean`, `inconclusive`,
`awaiting-decision`, `applying`, `done`, `cancelled`, `failed(engine)`,
`failed(proof)`.

Phases and their denominators (`progress.PHASE_UNITS`):

| Phase | Unit |
|---|---|
| `load-model` | *(none, and can have none)* |
| `transcribe-call` | *(none, and can have none)* |
| `decode` | `audio-seconds` |
| `language-slots` | `slots` |
| `detect` | `cues` |
| `apply` | `ops` |

INV-22 / R13, both limits:

- **(a)** at most 1.0 s between events in every non-terminal state;
- **(b)** a phase with a denominator reports it. `total is None` is legal only for
  the two phases in `NO_DENOMINATOR`.

`CANCELLABLE` is derived from the edge list (`queued`, `decoding`, `detecting`,
`awaiting-decision`), never listed. A cancel request is answered within one event
interval. A cancelled round **writes nothing** and does not advance the serial.
`ProgressEvent.name` is an identifier (which model, which detector), not a
sentence — the same closure as §4, applied at the source.

---

## 9. Boundaries an agent must respect

1. **Do not add a network call, a remote model, or an upload** anywhere on the
   core path (INV-18, INV-11, R1). The one exception is §9a, it is outside the
   product, and it needs the user's yes every time.
2. **Do not log in anywhere and do not carry, store, or request credentials** (R6).
3. **You may apply edits, and the record says you did.** `accept` is agent-callable
   (A-21) and the revision carries `face: "agent"`. INV-3/INV-4 still hold: no
   write without a script, and a failed revert proof writes nothing. Adopting an
   edit the plan did not propose is not possible; adopting one nobody asked for is.
4. **Do not manufacture a citation grant.** `cite` needs a real human's
   authorisation, scoped to one revision.
5. **Do not read or write `media-fetch-pipeline`'s tree**, and do not reuse its
   on-disk vocabulary. The separation is on disk, not only in code (R8), and
   `tests/test_layering.py` enforces it over shipping files with both controls.
6. **Do not present a Note as a Finding**, an `undetermined` stretch as a language,
   or a count over an empty population as a pass.
7. **Do not assume the defaults produced the numbers in `docs/`.** Read the
   settings back from `_origin.json` for the revision you are quoting.

## 9a. AI use: the LLM pick over glossary candidates

User ruling 2026-10-30: 「預設不開啟LLM使用  但可以（必須先）詢問得到同意後使用sonnet
med去跑校正（先假設只有Claude能用）」, and 「並且跑在詞頻校正後」.

1. **Off by default.** Never run it because a plan has candidates, because an
   earlier run was approved, or because another session asked. Each run needs the
   user's own yes, in this conversation, for this run and revision.
2. **Ask first, and say that the text leaves the machine.** This step sends
   transcript text to Anthropic (Claude), which R1 otherwise forbids; the ruling
   overrides R1 for this step only. Build the pack first so the question can state
   its size, then ask, for example: 「要用 Claude Sonnet（推理強度 medium）從 N 個
   候選裡挑出該改的嗎？這會把約 C 個字的逐字稿片段（每個候選的前後各一句）送出這台電腦，
   交給 Anthropic 處理。」 No answer, or anything but a clear yes, means no.
3. **Order.** `detect` with the lexicons (and `_字頻/` if present) first; the
   frequency `prior` is already in the pack; the LLM pick is last.
4. **The product holds no LLM client.** You (the calling Claude session) dispatch
   a subagent with model Sonnet, effort medium, and give it exactly
   `python -m ltm.detect.review prompt <pack.json>`'s output. Nothing else of the
   transcript goes in.

       python -m ltm.detect.review pack <root>/校讀/<run>/_plans/<rev>.json <pack.json>
       python -m ltm.detect.review prompt <pack.json>          # -> the subagent
       python -m ltm.detect.review picks <pack.json> <reply>   # -> --candidate N ...
       ltm accept <run> --rev <rev> --candidate N ... --picked-by advisor:<model-id>@glossary-pick-1

5. **The model picks numbers, never text.** `picks` refuses any reply that is not
   exactly `{"picks": [...]}` with numbers from the pack. A refused reply is
   reported to the user, not repaired or retried with a looser parse.
6. **Write-back is a new revision** through `accept`; nothing is edited in place,
   and the edit record names the picker. Tell the user how many were picked and
   that `plan`/`show` on the new revision lists them.
7. **The pack lives in your scratch space**, not in the store, and is not kept
   after the run unless the user asks.
8. **Feed the corrections back** (user ruling 2026-10-30: 「詞庫可以反饋增加(LLM改完也要將修正內容加進去)」):

       python -m ltm.detect.review learn <root>/校讀/<run>/_edits/<rev>.json <root>/_詞表/talks/<talk>.learned.json

   Each applied glossary replace becomes a declared wrong form (`source` `advisor`
   or `human`), so the next detect with that file proposes it as a replace with no
   model at all. The target is the talk's own file, passed with `--lexicon`;
   moving a learned form into the standing `_詞表/*.json` is the user's call
   (光線→光纖 was right in one talk and is light in another).
9. **Spend less.** Run steps 1–2 first and read the learned files of the same
   series; if few candidates remain, show them to the user instead of asking for
   a model run; when a model run is wanted, put every version of one recording in
   ONE subagent call (the fixed per-call cost dominated: dropping the neighbouring
   lines saved about 4%). Measured choices: `docs/glossary-lexicon-2026-10-30.md` §13.

---

## 10. Tests as the contract's proof

```powershell
python -m pytest -q
```

**All green, 0 skip.** The count is deliberately not written here: it moves every
time a test is added, and a stale number in a machine-facing contract is worse
than no number — an agent that compares its own run against it reports a false
mismatch. Read the count from the run. No environment variable is needed for any
of them: the
frozen baseline decode moved into the repository on 2026-09-09
(`docs/evidence/baseline_segments_2026-09-08.json`, A-26), so there is no
configuration of a machine in which the labelled control set goes unchecked while
the run prints green. `-m real_sidecar` selects the tests that start the actual
engine; they run by default too.

**Never regenerate that fixture by re-decoding**, and it does not skip when
absent — it raises. It is a frozen reference and decodes are not reproducible
(I-1): a fresh one would be a different recording wearing the reference's name,
and every frozen number measured against it would change meaning in silence.
Restore it from git.

Where a claim in this file is checked:

| Claim | Test |
|---|---|
| both faces are one function | `test_agentapi_schema.py::test_both_faces_call_one_function` |
| no free-text field in a response | `test_agentapi_schema.py` (walks `RESPONSES`) |
| every verb is agent-callable, gate still live | `test_agentapi_schema.py::test_every_verb_is_agent_callable_and_the_gate_still_works` |
| a revision says which face wrote it | `test_agentapi_verbs.py::test_an_agent_can_adopt_and_the_revision_says_an_agent_did` |
| a near-threshold run is capped at a note | `test_detect_candidates.py::test_every_run_length_around_the_two_thresholds`, `test_detect_signals.py::test_the_cap_is_what_holds_a_near_run_at_a_note` |
| a full run proposes on one signal, and only a full run | `test_detect_signals.py::test_a_full_run_alone_proposes_and_the_carve_out_stops_there`, `::test_single_signal_yields_note_not_proposal`, `::test_no_known_false_control_can_ever_become_a_proposal` |
| R8 covers the scripts that execute but do not ship | `test_layering.py::test_no_evidence_script_names_the_other_products_disk`, `::test_the_evidence_scan_has_something_to_scan_and_both_controls_hold` |
| a note carries its reason, and absence stays absence | `test_agentapi_verbs.py::test_a_note_carries_the_reason_it_is_a_note_all_the_way_to_the_reader`, `::test_a_note_written_before_the_reason_existed_says_so_instead_of_guessing`, `test_cli.py::test_every_note_reason_has_its_own_sentence_and_none_of_them_is_borrowed` |
| a decode survives timestamps past the end | `test_engine.py::test_the_engines_timestamps_may_run_past_the_end_and_the_run_survives`, `::test_a_decode_that_ends_on_time_reports_no_overshoot` |
| no GPU is a code, not a guess | `test_engine.py::test_the_real_child_says_which_failure_this_is_rather_than_crashed`, `::test_the_no_gpu_code_is_a_capability_answer_not_a_message_match` |
| duration comes from `ffprobe` | `test_engine.py::test_the_decode_denominator_comes_from_ffprobe` |
| per-revision decode provenance | `test_store.py::test_a_second_decode_does_not_overwrite_the_first_ones_settings` |
| the evidence's configuration is reachable | `test_agentapi_verbs.py::test_the_configuration_the_evidence_was_measured_with_is_reachable` |
| a refusal under `--json` is data | `test_cli.py::test_a_refusal_under_json_is_data_and_without_json_is_prose` |
| the record fingerprints the transcript | `test_agentapi_verbs.py::test_the_record_fingerprints_the_transcript_not_just_its_size` |
| the arithmetic is askable, a typo in it is refused | `test_agentapi_verbs.py::test_the_arithmetic_is_askable_and_a_typo_in_it_is_refused` |
| the file a failure names is not empty | `test_engine.py::test_the_file_a_failure_names_has_something_in_it` (positive control: `stderr_late`) |
| cancel is answered within one interval | `test_progress.py`, `test_engine.py` |
| word-level fails loudly | `test_detect.py::test_word_level_request_fails_naming_the_absent_layer` |
| a candidate is applied only when named | `test_glossary_review.py::test_accept_with_no_selection_never_applies_a_candidate` |
| the LLM reply is numbers from the pack or nothing | `test_glossary_review.py::test_known_true_a_well_formed_reply_is_read`, `::test_known_false_anything_but_numbers_from_the_pack_is_refused` |
| the disk separation (R8) | `test_layering.py` |
| every INV has a test | `test_invariant_map.py` |
| `agent` is the one sub-command that is not a verb | `test_two_faces.py::test_every_verb_the_parser_offers_is_a_verb_the_product_has` |
| and nothing under it reaches the product | `test_two_faces.py::test_nothing_under_the_namespace_reaches_the_product` (10 positive controls, one per way in) |
| this file ships, and there is one copy | `test_agents_contract.py::test_the_packaged_guide_is_there_and_the_root_file_points_at_it`, `::test_the_root_pointer_carries_no_contract_text_of_its_own`, `test_entry_points.py::test_the_packaged_guide_is_declared_as_package_data` |
| `guide` prints it byte for byte | `test_agent_namespace.py::test_guide_prints_the_packaged_file_byte_for_byte` |
| `onPath` and `importable` can disagree | `test_agent_namespace.py::test_which_reports_discovery_and_installation_as_separate_facts` |
| `register` touches nothing outside its markers | `test_agent_namespace.py::test_register_leaves_both_halves_of_the_file_byte_for_byte`, `::test_remove_restores_the_file_byte_for_byte`, `::test_a_crlf_file_stays_crlf`, `::test_a_file_with_broken_markers_is_refused_not_repaired` |
| `ltm` and `python -m ltm` are one function | `test_entry_points.py::test_the_console_script_resolves_to_this_main`, `::test_the_module_entry_point_is_the_same_function` |

---

## 11. What is deliberately absent in milestone 1

- **`Advisor`** — the semantic judgement model. Not disabled: absent. No module,
  no `consulting` state, no advisor fields. INV-10 / INV-19 / INV-21 are written
  and marked milestone 2. *Superseded in part, 2026-10-30:* the consent-gated pick
  of §9a runs OUTSIDE the product (the caller's subagent), may only choose among
  lexicon candidates by number, and leaves one trace inside: `picked_by` on the
  edit record.
- **The token layer** — word-level alignment. An explicit `word_level=True`
  request fails with `token-layer-absent` and writes nothing.
- **Reasoning models** — out by ruling; thinking mode destabilises the output
  format.
- **Any git remote** — this repository is local-only by ruling. Commit; never push.
