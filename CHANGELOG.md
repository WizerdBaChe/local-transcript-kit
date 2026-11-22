# Changelog

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
- Known limits at release: Simplified/Traditional mixing raises glossary false alarms; 11 test modules need
  unshipped measured recordings and are not collected here.
