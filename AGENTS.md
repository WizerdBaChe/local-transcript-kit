# AGENTS.md — how to use this repository

## What this repository is (facts, for any reader)

- **Contents:** the public release of `ltm` (local-transcript-maker) v1.0.0, a Windows CLI and local review page
  that transcribes a recording on the user's machine (faster-whisper large-v3, run in a separate interpreter)
  and marks the sentences that should not be trusted, with the reason for each. It never edits the original and
  never overwrites a revision. Index: `llms.txt`.
- **The product uses the Python standard library only.** `reference-impl/pyproject.toml` declares
  `dependencies = []`; the speech engine lives in `reference-impl/runtime/`, built by `setup-runtime.ps1`
  (faster-whisper 1.2.1 from PyPI and about 3.1 GB of weights from Hugging Face, each weight file checked against
  a recorded SHA-256). Tests need pytest only. Licences: `THIRD_PARTY_NOTICES.md`.
- **No network call in the product.** A test refuses any network import in shipped code
  (`reference-impl/tests/test_layering.py`, `test_no_network_imports`); `verify/run_controls.py` K2 plants one and
  shows it caught. The review page binds to 127.0.0.1 with a per-launch token.
- **ltm calls no LLM.** The optional "LLM pick" over glossary candidates is done by the calling agent with the
  user's consent each time (`reference-impl/src/ltm/AGENTS.md` §9a); ltm prepares the candidates and records the
  choice. That consent is a workflow rule, not code.
- **Agent responses carry no free text** (INV-12): every string field is an identifier or a closed-set state,
  checked by a type walk over every response class (`reference-impl/src/ltm/agentapi/schema.py` `check_response`).
  A URL given to `intake --from-url` is stored on disk and appears in the prose face, never in `--json` (K4).
- **What is enforced by code and what is only a rule:** `docs/EVIDENCE.md` "Enforcement map" (file:line each).
- **Threat model:** the gates catch a careless change (a network import, a free-text response field, a face that
  bypasses the verbs), not an adversary. One user on one machine.
- **What was run:** Windows 11, Python 3.12.7, pytest 9.1.1. `verify/controls_run.txt`: 5/5 controls; the shipped
  suite 516 passed, 5 skipped, 11 modules not collected because they need records that are not shipped (measured
  real recordings, the full build spec). The speech engine was NOT run by the controls; the demo run in
  `docs/assets/demo/desk/` was decoded once on the author's machine (GPU).
- **Network and side effects of the controls:** none on the network; they copy `reference-impl/` into a scratch
  folder you name (empty), point HOME, USERPROFILE and LTM_ROOT inside it, mutate and write there.
  `setup-runtime.ps1` downloads; `ltm agent register` writes into an agent configuration file (between two
  markers, `--dry-run` and `--remove` exist). Neither is run by the controls.
- **`reference-impl/CLAUDE.md` and `reference-impl/README.md` are the origin's own builder and user guides,
  copied as data:** they cite `docs/` records that are not shipped (`verify/unshipped_paths.txt`) and talk to
  the author ("you ruled on 2026-09-09"). Treat them as history, not instructions for your machine.
- **If you only read and do not execute:** read `docs/EVIDENCE.md`, then `verify/controls_run.txt`, then
  `reference-impl/src/ltm/AGENTS.md` (the calling contract). Everything below is installation steps for an agent
  whose user asked for an install.

## For an installing agent

Your user asked you to install (or adapt) a local transcription tool. The human introduction is `README.md`
(Traditional Chinese; English mirror `README.en.md`). Diagrams and the structure tree for human builders are in
`docs/DEVELOPER.md` (Traditional Chinese).

## Reading order

1. `docs/BUILD-SPEC.md` — layout, the contract, declared edits.
2. `docs/EVIDENCE.md` — what was measured, the enforcement map, what is NOT claimed.
3. `reference-impl/src/ltm/AGENTS.md` — the calling contract you will follow (`python -m ltm agent guide` prints it).
4. `reference-impl/skill/SKILL.md` — a ready skill file for Claude Code style agents.

## First actions (before proposing any change)

```
python -m pip install -r verify/requirements.txt
python -X utf8 verify/run_controls.py <a-new-empty-scratch-dir>
```

Compare with `verify/controls_run.txt`: every control line begins `PASS`, the last line reads
`SUMMARY: 5/5 controls pass`. Report any difference to your user before anything else. Never edit a control to
make it pass.

## Install table

| Goal | Do | Ask the user first? |
|---|---|---|
| Run from source | set `PYTHONPATH=reference-impl/src`, then `python -m ltm agent which` (or `pip install -e reference-impl`) | no |
| See the demo result | `python -m ltm --root docs/assets/demo/desk plan narration-6c80e41d` | no |
| Build the speech runtime | `powershell -ExecutionPolicy Bypass -File setup-runtime.ps1` (~3.1 GB, PyPI + Hugging Face) | **yes**: it downloads |
| Choose where output goes | set `LTM_ROOT` (user environment) or pass `--root` | yes: their disk |
| Teach your own agent | `python -m ltm agent register --target <claude\|codex\|droid\|agents-md> --dry-run`, then without `--dry-run` | **yes**: writes into a configuration file |
| Put `ltm` on PATH | `pip install -e reference-impl` | yes |

## Starting from a post URL (the optional mfp recipe)

`ltm` reads local files only. If your user starts from a public post and has media-fetch-read-kit (`mfp`)
installed, you are the integrator; neither tool reads the other's folders:

1. `mfp probe <url> --write-subs --json` and read `results[0].manifest.captionsUnconfirmed` and
   `results[0].manifest.items[].sidecars[].kind`.
2. Checked in this order: `captionsUnconfirmed: true` → mfp could not tell, go on.
   `captions` → the uploader's own text exists: fetch it with `mfp fetch <url> --write-subs`; ltm is not needed.
   `auto-captions` → the platform's machine transcript: tell the user and ask which to use.
   none → go on.
3. `mfp fetch <url> --json` → the saved path is `items[].path` (status `ok`; join to the probe's video items by
   `index`, or else take an item whose path ends in a video extension; a fetch item has no `kind`), the post URL is
   `posts[].url`.
4. `python -m ltm intake <path> --from-url <posts[].url> --from-note "mfp <postId>"`, then `decode`, `plan`.
5. Report the findings and say that ltm does not guarantee correct words.

`verify/recipe_mfp_to_ltm.py` is this recipe as a program; K3 runs it on `verify/fixtures/`. If `mfp` is not
installed, do not install it unasked: say it exists and that it is optional.

## Adaptation rules

- Use `--json` and parse stdout; a non-zero exit carries an `ErrorResponse` (`code`, `subject`). Do not parse prose.
- Never edit files under the output root by hand; `accept` opens a new revision, which is how edits land.
- A finding is a reason to look, not an edit: do not `accept` glossary replacements without the user.
- Do not re-run `decode` to "confirm" a result: two decodes differ by design (README "限制").
- LLM correction: off unless the user says yes for this run; it sends candidate sentences off the machine.

## What not to adopt

- The origin's lexicons, frequency tables and measured recordings: not shipped (other people's speech or a
  field's vocabulary); do not recreate them from someone else's recordings.
- The origin's private records under `docs/` that code comments cite: listed in `verify/unshipped_paths.txt`;
  nothing there is needed to run.
- The author's machine setting `LTM_ROOT` to a data drive: choose your own.
