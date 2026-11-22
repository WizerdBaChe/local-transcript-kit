# Changelog

## v1.1.0 — 2026-11-23

Re-cut from the origin `local-transcript-maker` at commit `e473403` (v1.0.0 was `0e3728c`).

- `glossary` no longer reports a lexicon term written in Simplified-only characters (验收 for 驗收) as a
  sound-alike. It is counted in the plan summary (`passed_over["script-variant"]`, also in the plan file and in
  `DetectResponse.passed_over`). One-directional: characters that are also Traditional (后, 系, 价) still flag.
  This fixes the v1.0.0 known limit "Simplified/Traditional mixing raises glossary false alarms".
- New opt-in detector `script-switch` (`ltm detect --script hant|hans`, default expectation `hant`, not in the
  default set): flags runs of cues in the other Chinese script and adds `script_cues` counts to the plan summary.
  Never converts text.
- New data `src/ltm/detect/data/simplified_variants.tsv`, derived from OpenCC (Apache-2.0) with its notice
  (`NOTICE-opencc.txt`), and its builder `tools/build_simplified_variants.py`.
- Demo run: `docs/assets/demo/desk/` gains r002 (same detect, same 15-term lexicon, new code): 15 glossary findings
  where r001 (v1.0.0 code) has 27; hero picture rebuilt from r002.
- `verify/run_controls.py` K6: the script rules on the demo run, two-sided (6 controls now).
- Not collected here (their records are not shipped): 12 test modules, one more than v1.0.0
  (`tests/test_script_switch.py`).

## v1.0.0 — 2026-11-22

First public release, cut from the origin `local-transcript-maker` at commit `0e3728c`.

- `ltm` (standard library only): `intake`, `decode`, `detect`, `plan`, `show`, `runs`, `accept`, `cite`,
  `open`, `cancel`; the local review page (`python -m ltm.ui.serve`); the agent namespace (`ltm agent
  guide | which | register`).
- New in this cut: the output root is resolved per machine (`--root` > `LTM_ROOT` > `<home>/TranscriptDesk`);
  `intake --from-url / --from-note` records where a file came from (`came_from` on the intake marker; the JSON
  response carries `came_from_count` only).
- `setup-runtime.ps1`: builds the speech runtime (faster-whisper 1.2.1, Systran/faster-whisper-large-v3 checked
  by SHA-256), asks before downloading.
- `verify/run_controls.py`: 5 acceptance controls (4 two-sided); `verify/recipe_mfp_to_ltm.py`: the optional
  media-fetch-read-kit recipe as a program.
- README pictures rebuilt by `docs/assets/build_assets.py` from a real run of the author's own narration.
- Known limits at release: Simplified/Traditional mixing raises glossary false alarms (fixed in v1.1.0); 11 test
  modules need unshipped measured recordings and are not collected here.
