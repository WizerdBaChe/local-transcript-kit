# EVIDENCE — what was measured, what enforces what, what is not claimed

## Measured for this release (2026-11-22, Windows 11, Python 3.12.7)

| What | Result | Record |
|---|---|---|
| Acceptance controls | 5/5 pass | `verify/controls_run.txt` |
| Shipped test suite (in the controls' scratch copy) | 516 passed, 5 skipped, 0 failed; 11 modules not collected (records not shipped) | `verify/controls_run.txt` K1 |
| Same suite at the origin, with every record present | 796 passed | origin run before the cut (not re-runnable here) |
| Fresh clone under an empty home | see file | `verify/fresh_clone_run.txt` |
| Demo decode: the author's own narration, 17 min 16 s | 483 sentences, one language stretch (zh, agreement 1.000), 1036.5 s on the author's GPU, 0 findings without a lexicon | `docs/assets/demo/desk/校讀/narration-6c80e41d/` |
| Same run, `detect` with 15 script terms (correct forms only) | 27 glossary findings; true mishearings include 假單→价单, 假別→价别, 人事→人士, 選課→選客 (several), 重試→重视, 亞穩態→亚文太, 瑞士起司→瑞士启示; false alarms include correct words written in Simplified (验收, 学务处, 旧系统, 选课, 入口网站) | `_findings/r001.json`, `_plans/r001.json` there |
| mfp probe of the same video (public, the author's own) | one video item, sidecar `captions` zh-TW srt, `captionsUnconfirmed` false: the recipe would stop at step 2 for this post | run 2026-11-22, not shipped (the fixtures in `verify/fixtures/` copy its shape with invented ids) |

Numbers from the origin's own design records (cue counts of other recordings, decode variance, compute-type
costs) are cited by the shipped `reference-impl/README.md` and code comments; their records are listed in
`verify/unshipped_paths.txt` and are not re-measured here.

## Enforcement map

| Rule the docs state | Enforced by | Kind |
|---|---|---|
| No network import in the product | `reference-impl/tests/test_layering.py:393` `test_no_network_imports` (module list at :83-85); positive control :416 | code (test), K2 two-sided |
| Agent responses carry no free text (INV-12) | `reference-impl/src/ltm/agentapi/schema.py:197` `check_response`; `reference-impl/tests/test_agentapi_schema.py:27`, negative control :78 | code |
| The URL from `--from-url` stays out of `--json` | `reference-impl/src/ltm/agentapi/schema.py` (the response has only `came_from_count`); `reference-impl/tests/test_cli_came_from.py:30` | code, K4 two-sided |
| `--from-url` accepts only http(s) with a host, no whitespace or control characters, ≤ 2048 | `reference-impl/src/ltm/store/came_from.py:35` `_SHAPE`, :54 `clean_url` | code |
| A plain re-intake keeps earlier `came_from` rows | `reference-impl/src/ltm/store/came_from.py:100` `merged`; `reference-impl/tests/test_intake_came_from.py` | code |
| Output root `--root` > `LTM_ROOT` > home | `reference-impl/src/ltm/store/layout.py:32` `default_root`; `reference-impl/tests/test_root.py` | code, K5 |
| CLI and agent call one function | `reference-impl/tests/test_agentapi_schema.py:168`; `reference-impl/tests/test_two_faces.py:150` (a face may not import the store or engine) | code |
| The review page binds to 127.0.0.1 with a per-launch token | `reference-impl/src/ltm/ui/serve.py:78` `HOST`, :1369 `token_urlsafe(32)`; `reference-impl/tests/test_ui_serve.py:310` | code |
| The engine's weights are the expected files | `setup-runtime.ps1` SHA-256 table | code (at setup only) |
| LLM correction runs only after the user says yes, each time | the calling agent (`reference-impl/src/ltm/AGENTS.md` §9a); ltm itself makes no LLM call | process only |
| Do not accept glossary replacements without the user | `AGENTS.md` adaptation rules | process only |
| Do not install media-fetch-read-kit unasked | `AGENTS.md`, `reference-impl/skill/SKILL.md` | process only |
| A finding needs two independent signals (except a 5-fold repeat) | `reference-impl/src/ltm/detect/` (thresholds.py, candidates.py); calibration tests are among the modules not collected here | code; its calibration is not re-checked in this pack |

## Not claimed

- That a transcript is correct, or that every error is found: the detectors cover repeats, decoder anomalies and
  glossary sound-alikes only.
- Detector calibration on a public machine: the tests that hold it need the author's measured recordings.
- Any platform but Windows 11; any Python but 3.12.
- Bit-reproducible decodes (GPU float16 is not).
- That `setup-runtime.ps1`'s downloads stay available or unchanged upstream; a changed file fails its SHA-256.
