# BUILD-SPEC — local-transcript-kit v1.1.0

Machine-read. The human overview is `../README.md`; the builder guide is `DEVELOPER.md`.

## 1. What this pack is

A share pack of `ltm` (origin project `local-transcript-maker`, commit `e473403`; v1.0.0 was cut at `0e3728c`), a standard-library Python
CLI plus local review page that transcribes a recording with faster-whisper in a separate interpreter and marks
the transcript spans that should not be trusted. The pack adds acceptance controls, an engine setup script, the
optional media-fetch-read-kit recipe, and README pictures rebuilt from a real run.

## 2. Layout

| Path | What | Origin |
|---|---|---|
| `reference-impl/` | byte copy of the origin's shipped paths (manifest: maintainer's control layer, not shipped) except section 9 | origin |
| `reference-impl/src/ltm/` | the product | origin |
| `reference-impl/tests/` | the origin's test suite; `reference-impl/conftest.py` carries the kit block (E3) | origin + E3 |
| `reference-impl/src/ltm/AGENTS.md` | the calling contract, printed by `ltm agent guide` | origin |
| `reference-impl/skill/SKILL.md` | a skill file for coding agents | origin |
| `setup-runtime.ps1` | builds `reference-impl/runtime/` (venv + weights, SHA-256 checked) | pack |
| `verify/run_controls.py`, `verify/controls_run.txt` | acceptance controls and their recorded run | pack |
| `verify/recipe_mfp_to_ltm.py`, `verify/fixtures/` | the section 5 recipe as a program, invented mfp outputs | pack |
| `verify/fresh_clone_run.txt` | the controls in a fresh clone under an empty home | pack |
| `verify/unshipped_paths.txt` | origin paths cited by shipped text and not shipped, with a reason class | pack |
| `docs/assets/` | README pictures, their build script, the demo run record (r000 decode, r001 detect with the v1.0.0 code, r002 detect with the v1.1.0 code) and lexicon | pack |
| `reference-impl/src/ltm/detect/data/` | `syllables.tsv` (pypinyin-derived) and `simplified_variants.tsv` (OpenCC-derived, v1.1.0) with their notices; builders in `reference-impl/tools/` | origin |

## 3. Runtime contract (summary; the full contract is `reference-impl/src/ltm/AGENTS.md`)

- Python 3.12, standard library only; `python -m ltm <verb>` with `PYTHONPATH=reference-impl/src`, or `pip install -e reference-impl`.
- Verbs: `intake decode detect plan show runs accept cite open cancel`; `--json` on each gives the agent face.
  Both faces call one function (`reference-impl/tests/test_agentapi_schema.py` `test_both_faces_call_one_function`).
- Output root: `--root` > `LTM_ROOT` > `<home>/TranscriptDesk` (`reference-impl/src/ltm/store/layout.py` `default_root`).
- Engine: `LTM_SIDECAR_PYTHON` (default `reference-impl/runtime/asr-py/Scripts/python.exe`), `LTM_MODEL_DIR`
  (default `reference-impl/runtime/models/faster-whisper-large-v3`), `ffprobe` on PATH.
- Responses carry no free text (INV-12): `reference-impl/src/ltm/agentapi/schema.py` `check_response`.

## 4. Output contract

`<root>/_intake/<run>.json` (pointer to the recording, its SHA-256, optional `came_from` list of
`{url, note, at}`, append-only), `<root>/校讀/<run>/` (`_origin.json`, `時軸/*.srt`, `全文/*.txt`,
`_findings/rNNN.json`, `_plans/rNNN.json`). A revision is never rewritten; `accept` opens the next.

## 5. The mfp recipe (optional integration)

Neither product reads the other's files. The agent reads mfp's stdout and calls `ltm intake`. Fields read, all
from mfp FetchResult / probe output `schemaVersion` 1:

- probe: `results[0].manifest.items[].kind`, `.index`, `.sidecars[].kind` (`captions` | `auto-captions`),
  `results[0].manifest.captionsUnconfirmed`;
- fetch: `items[].index`, `.status`, `.path`; `posts[0].url`, `.postId`. A fetch item carries no `kind`: join by
  `index` to the probe's video items, else accept a `status: ok` item whose path ends in a video extension
  (`VIDEO_EXT` in the recipe).

Decision, in this order: `captionsUnconfirmed` true → go on; `captions` → stop (exit 10, the uploader's text exists); `auto-captions` → stop and ask (exit 11);
otherwise intake with `--from-url posts[0].url --from-note "mfp <postId>"` (exit 0). Malformed input → exit 2.
review-when: mfp changes `schemaVersion` of its probe or fetch output.

## 6. Engine setup

`setup-runtime.ps1`: Python 3.12 venv at `reference-impl/runtime/asr-py`, `faster-whisper==1.2.1`; files
`config.json model.bin preprocessor_config.json tokenizer.json vocabulary.json` of Hugging Face
`Systran/faster-whisper-large-v3` checked against SHA-256 values in the script (taken from the author's working
copy). Asks before downloading unless `-Yes`. Writes nothing outside `reference-impl/runtime/`.

## 7. Acceptance controls (`verify/run_controls.py`)

| Id | Asserts | Two-sided |
|---|---|---|
| K1 | the shipped suite passes; prints passed / failed / skipped and the not-collected modules | no |
| K2 | `tests/test_layering.py`: 0 failures shipped, at least 1 with a planted `import urllib.request` | yes |
| K3 | recipe: no-captions fixture → exit 0 and 1 `came_from` row; captions fixture → exit 10 and 0 rows | yes |
| K4 | `intake --from-url`: URL in the prose face, absent from `--json`, `came_from_count` 1.0 | yes |
| K5 | `default_root()` with `LTM_ROOT` set vs unset (home inside the scratch folder) | yes (two values) |
| K6 | the script rules on a scratch copy of the demo desk: `detect` with the 15-term lexicon gives 15 glossary findings and `passed_over` 12; with `soundalike.same_word` forced to false in the same code, 27; `detect --script hant` gives 2 `script-switch` runs, `--script hans` 3; cues by script hant/hans/none 68/363/52 | yes |

Evidence rung 0 (examples). The speech engine is never started by the controls.

## 8. README pictures

`docs/assets/build_assets.py` serves `docs/assets/demo/desk/` read-only with the shipped product and captures the
run page (hero, asserts the page shows as many findings as the newest shipped plan holds, 15 for r002); draws the flow from its `FLOW`
table with an overlap check (0) and a known-bad control (boxes stacked, must be > 0). The desk itself came once
from `docs/assets/capture_demo_desk.py` on the author's machine (paths to the recording rewritten to
`recording/<name>`). Revision r002 was added on 2026-11-23 by running `ltm detect narration-6c80e41d --rev r000
--lexicon docs/assets/demo/lexicon-campus-systems.json` with the v1.1.0 code on a scratch copy and copying
`_plans/r002.json`, `_findings/r002.json` and `_origin.json` back; r001 is kept as the v1.0.0 result.

## 9. Declared edits (reference-impl is byte-identical to the origin except these)

| Id | File | Change |
|---|---|---|
| E1 | `tests/test_store.py` | the consent grantor's name in one fixture → `owner` |
| E2 | `CLAUDE.md` | the author's machine-specific `LTM_ROOT` value → "a data drive" |
| E3 | `conftest.py` | appended kit block: modules whose records are not shipped are left out of collection (`collect_ignore`), one test is skipped, and the run names them; inert when the records exist. From v1.1.0 it also lists `tests/test_script_switch.py` (it reads the frozen decodes) |
| R1 | design prose (all files) | the author's workspace path prefix → `<WORKSPACE>/`; `~/.claude/` → `<CLAUDE_HOME>/` |

Kept as at the origin, on purpose: `reference-impl/pyproject.toml` version `0.1.0.dev0` (the pack's version is the
tag); the origin's own data-drive paths in prose, test defaults and fixtures (`D:/output/...`), and the tool project
names it cites (e.g. `voice-clone-lab` in `tools/build_syllables.py`).

Not shipped: `docs/` of the origin except `docs/gui-contract.json` and `docs/gui-mapping-2026-09-10.md` (tests
read them), `tools/` except `tools/build_syllables.py` and `tools/build_simplified_variants.py`, the origin's asset-library manifest. List:
`verify/unshipped_paths.txt`.

## 10. Licences

Code MIT (`LICENSE`), documentation and the demo run record CC BY 4.0 (`LICENSE-DOCS`), third parties in
`THIRD_PARTY_NOTICES.md`.
