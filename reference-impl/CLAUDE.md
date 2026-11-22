# local-transcript-maker

A local tool that makes a machine transcript's uncertainty VISIBLE. The pain
it exists for: a machine-made transcript is wrong in ways the reader cannot
see, and the reader has no way to know WHERE to distrust it. Single machine,
single user, no login, no remote, nothing leaves the box.

**Milestone 1 is built.** `src/ltm/` is the product, `tests/` is its suite
(`python -m pytest -q`), and the design record in `docs/` is still where the
WHY lives — read the documents before proposing anything, and do not
reconstruct context from `media-fetch-pipeline`'s repository. This paragraph
used to say "there is no source tree yet"; it stayed that way through the
whole of milestone 1, which is the drift this file warns about two sections
below. **A statement about the repository's own state gets re-read whenever
the state changes, or it becomes the most confident wrong sentence here.**

## Operating level

ops-relaxation: L1 (core relaxed) — the standing ruling for an Opus-tier
main loop. A cheap/mid-tier main loop still runs L0.

Written bare, not in backticks: the environment's health check treats a
backticked line as a quoted EXAMPLE rather than a declaration, deliberately,
and read this file as having no level at all for as long as the quotes were
there.

## Read these first

| File | What it holds |
|---|---|
| `docs/concept-transcript-tool-2026-09-08.md` | Concept Note / CIM — settled |
| `docs/pim-transcript-tool-2026-09-08.md` | PIM + semantic contract — still a DRAFT; §13 declares its own gaps |
| `docs/verification_*.md` | Every hard-gate round, one report per verifier, committed unmodified |
| `docs/evidence/` | Scripts and raw output behind the measured numbers. A number in the PIM with nothing here is a number nobody can re-run |
| `tools/` | Design-time instruments that check the documents (not product code). `pim_struct_check.py` is the pre-flight the author runs so the independent verifier does not spend a round on defects a regex catches |
| `docs/handoff-local-transcript-maker-2026-09-09.md` | Where this product came from, what deliberately stayed in `media-fetch-pipeline`, and the inventory as measured on 2026-09-09 |
| `docs/psm-transcript-tool-2026-09-09.md` | The work cards milestone 1 was built from — **and every increment since**: §15 the schema amendments, §16 the face, §17 E-3, §18 the `ltm` command and its agent surface, §19 the page that starts decodes, §20 silence as an answer and `sparse-cue` (I-22/I-23), §21 the divergence map (cards DV-0..DV-9, written before the code, not built). The file name still says 09-09 and its header still says v4; each section's own status banner is what dates it |
| `docs/architecture-2026-09-09.md` | What the code actually looks like and why it is cut that way — including §6, the known weaknesses |
| `docs/issues-2026-09-09.md` | Open defects, each with what was measured |
| `docs/uat-2026-09-09.md` | What only a person can accept, and what is still waiting for them |
| `docs/adr-gui-stack-2026-09-10.md` | The human interface: technology chosen, options rejected, and the order the user ruled it must be built in |
| `docs/uat-audio-corpus-2026-09-10.md` | The three recordings kept to be tested against from now on — every property measured rather than asserted, what the corpus CANNOT measure, and I-20 |
| `docs/prior-art-whisper-2026-09-10.md` | An outsourced survey of the whisper ecosystem against six named problems. **Its header says which single claim was verified locally; everything else is a lead, not a fact** |
| `docs/prior-art-threads-six-asr-2026-09-27.md` | An external case (a public post comparing six local ASR models), each claim marked agrees/contradicts/new against the rulings here and in speech-subtitle-lite, plus proposed measurement rounds M1–M3. The post's numbers are unverified. **M1 was ruled and ran** (user, 2026-09-27): `docs/m1-silence-probe-2026-09-27.md`. **M2/M3 are downgraded** (user, same day: 「M23既然沒有有效幫助就降級」) — not scheduled; the doc names what would reopen them |
| `docs/breeze-eval-2026-10-22.md` | Breeze ASR 25 vs large-v3 (and a glossary prompt) measured on ASCEND, synthetic lab sentences and the corpus. **M2 was reopened at minimum size for it** (its named reopen condition: someone wants to compare two models' accuracy). Three decisions open in its §7; the converted model sits in `runtime/models/breeze-asr-25-ct2-fp16/` and is not wired into any default |
| `docs/design-divergence-map-2026-10-22.md` | Cross-check layer 1 (divergence map), Breeze as the companion decoder beside large-v3. Concept + PIM amendment (INV-24..INV-33); **the verification gate passed** (round 4 B, round 5 A; all reports are `docs/verification_divergence-map_2026-10-22_*.md`); user rulings in its frontmatter and §11; build cards are PSM §21; nothing built yet. Its numbers come from `docs/evidence/divergence_{probe,blockwise,gold_lab2}_2026-10-22.*`; the §9 falsifier ran on a user-corrected reference kept outside the repo (R1) |
| `docs/glossary-lexicon-2026-10-30.md` | The text-layer correction lexicon and the `glossary` detector that reads it: user rulings (lexicons in `<root>/_詞表/`; a declared wrong form carries a replace op, a sound-alike only flags), the INV-9 control pair, and the measurement against the 2026-10-30 talk's 101 hand corrections. **Its §5.1 says the ceiling: only 31 of 96 errors have a lexicon term as their correct form** |
| `docs/prior-art-audio-preprocessing-2026-09-27.md` | An outsourced survey of audio pre-processing before ASR (VAD, loudness, denoising, dereverberation, gating). **Its header names the three claims verified and one it gets backwards** (the VAD-default "fix" for a deleted quiet speaker) |

**Version numbers live in each document's own header, and nowhere else.**
This table used to repeat them and went two versions stale, which is the same
drift the PIM itself was blocked for twice. A pointer cannot drift; a copy can.

The design ladder is **full-ladder** (CIM §0.6): the verification hard gate
may not be skipped, and no PSM starts until it passes. **Milestone 1 went
through it** — nine rounds, one report per verifier, all in `docs/`.

**What was built after that did not, then round 10 ran.** The human interface
exists as of 2026-09-11 (`src/ltm/ui/serve.py`, PSM §16 cards F-1..F-6, INV-23
with both of its controls), and so do the six schema amendments before it
(§15). This paragraph used to say round 10 was still owed against PIM v1.1 —
**that sentence is now the drift this file warns about at the top.** The
user's ruling deferred round 10 past v1.1 to let the implementation line build
first; round 10 actually ran against **v1.2**
(`docs/verification_pim-v1.2_2026-09-11_K.md`, `..._L.md`), judged **BLOCKED,
four ways** — K-BLOCK-1, K-BLOCK-2, L-BLOCK-1 from the two verifiers, plus a
fourth the author found against nobody's assignment and is the worst of the
four: `multilingual` silently overrides a pinned language at decode time. All
four are answered in **PIM v1.3** (commit `6f941a3`). **The price this
paragraph used to name — "if round 10 changes INV-23, the face changes with
it" — did not come due**: none of the four touch INV-23, so the face built
above stands unchanged.

**Round 9** (against v1.0, BLOCKED on I-1/J-1 + four should-fix, answered in
v1.1) was never recorded in PIM §11.1 when it ran; v1.3 backfilled the row and
named the omission itself as a finding rather than guess a number.

**Round 11 ran on 2026-09-16, against v1.3, while E-3 was being built.** The
question this paragraph used to leave open was settled by the model under the
L1 decision charter (logged in that session's process ledger), not by the user:
let round 11 trail the implementation, the way round 10 trailed v1.1. Judged
**BLOCKED, one each** (`docs/verification_pim-v1.3_2026-09-16_M.md`, `..._N.md`)
— both were round-10 sentences left standing where the round-10 fix did not
look. Answered in **PIM v1.4**. The verifiers ran on sonnet, not opus (the
subagent model cap), so read that round as what sonnet catches;
`docs/gate-operations-2026-09-09.md` has the dispatch shape.

**E-3 is shipped** (PSM §17, cards M-1..M-6): `decode` defaults to per-window
language detection, a pinned `--language` needs `--no-multilingual`, and I-20
is fixed on the recording that found it (0 → 7,294 Chinese characters in that
run — **a single measurement; the same flags on the same file have since given
7,288 to 7,297**, see PIM §13 gap O; UAT B-40–B-42).

**The `ltm` command and the page that starts decodes are built** (2026-09-16,
PSM §18 cards CLI-1..CLI-6 and §19 cards GUI-1..GUI-6). `ltm` is a real console
script with an `ltm agent guide|which|register` namespace; `AGENTS.md` now ships
INSIDE the package (`src/ltm/AGENTS.md`) and the file at the repo root is a
pointer, not a copy. The browser face gained its first write path — four JSON
routes, a decode in a child process, an allowlist the offered path must be a
member of, and a panel that shows progress, cancels, and says so when something
fails. Manual acceptance is UAT B-43..B-51 and is **waiting on the user**; the
browser half that pytest cannot reach was measured through a real browser and
the transcript is `docs/evidence/gui4_dom_run_2026-09-16.md`. **This increment
has not been through the verification gate** — the same debt the paragraph below
names, and the next thing built owes it too.

**2026-09-17: the double-click entry, and the compute types measured.** User
ruling: a `.cmd` at the repository root plus a desktop shortcut, with a dropped
folder becoming `--audio-root` and `layout.dropbox(root)` (`_錄音/`) as the
fallback. §16.0 (1) stands — this is `ltm.ui.launch`, a module entry, not a verb.
Separately, `docs/evidence/compute_type_cost_2026-09-17.*` answers "can int8
replace float32": the product never ran float32, and on this card **both int8
types save 1,888 MiB and turn a decode whose longest repeated run is 1 segment
into one whose longest is 14** — so `float16` stays the default until the user
says otherwise. Both of these are also outside the verification gate.

**2026-09-27: silence is an answer, and a stretched cue is a finding** (PSM §20,
I-22/I-23, user ruling 「I22I23都照建議執行」). A decode with zero segments is now a
round with no words, and `health["vad_kept"]` says why; `no-segments` left
`EngineFailure.CODES` for `no-report`. The default detector set has **two**
members now — `sparse-cue` (≥ 15 s at ≥ 3 s per unit, never deletes) joined
`engine-loop` after its INV-9 pair held on the real engine. Manual acceptance is
UAT B-52..B-57, and B-57 (loosen the bar or not) is a user question. **Outside
the verification gate** too: the PIM does not yet say any of this, and §20's
banner names the three amendments owed.

**2026-10-21: a language set, VAD knobs, and an uncovered span is a finding**
(I-27/I-28, user's decision sheet answer 「I-27＋I-28」). `decode --languages en,zh`
narrows per-window detection to a set by filtering the model's own
`detect_language` answer in the sidecar (faster-whisper 1.2.1 has no such
parameter); the language slots stay unrestricted. `--vad-threshold`,
`--vad-min-silence-ms`, `--vad-speech-pad-ms` override VadOptions one field at a
time; defaults unchanged. The default detector set has **three** members —
`uncovered-span` (≥ 60 s with no cue, never deletes) joined after its control
pair held on stored real decodes. **Outside the verification gate**, and no PSM
cards were written first; the issue entries carry the design and the evidence.

**2026-10-30: `glossary` runs** (`docs/glossary-lexicon-2026-10-30.md`). It reads
correction lexicons (`ltm-lexicon/1` JSON) from `<root>/_詞表/` and `--lexicon
PATH`, finds declared wrong forms (term-match) and sound-alikes (CJK homophones
and near-homophones from a pypinyin-derived table, Latin consonant skeletons),
and has its control pair, so it left `UNCONTROLLED`; it is still not in
`DEFAULT`. A declared hit proposes a `replace` op, so **`accept` with no `--op`
now applies glossary replaces too**. **Outside the verification gate**; the PIM
amendments owed are listed in that document's §7.

**2026-10-30, second round: language case, character frequency, and the
consent-gated LLM pick** (same document §9–§12). Each glossary finding carries
`script` (the cue's case read from its text; the decode's `stretch_table` says
`zh` throughout a mixed talk) and, if `<root>/_詞表/_字頻/*.csv` exists, a
`prior` that ranks only (AUC 0.68). A sound-alike's replace is a DORMANT
candidate (`plan.candidates`), applied only by `accept --candidate N`.
`ltm.detect.review` packs candidates, holds the versioned prompt and parses a
reply into numbers; the product holds no LLM client. The skill is
`skill/SKILL.md` (synced to `<CLAUDE_HOME>/skills/local-transcript-maker/`); the
AI-use rules are `src/ltm/AGENTS.md` §9a. The LLM step needs the user's yes each
time. Outside the verification gate.

**2026-10-30, third round: Sonnet post-correction while growing the lexicon is
the settled route** (same document §13–§14). First consented run: Sonnet medium
kept every right fix on both versions with 2/0 other noise; `review learn` folds
accepted corrections into the talk's `_詞表/talks/<talk>.learned.json`, which
then proposes them as replaces with no model (21/24 right, 0 noise, same
recording). Word frequency is deferred by user ruling (experience + term lists
instead); the COCT table was found and not downloaded. Breeze vs large-v3 on
this talk: §14.1 — Breeze fewer term errors, no punctuation; default unchanged
(breeze-eval D1 is still the user's). N = 1 talk.

**2026-11-22: the root follows the machine, and intake records where a file came
from** (PSM §22, cards P-1..P-4; user rulings for the public edition
`local-transcript-kit`). The default root is `--root` > `LTM_ROOT` >
`<home>/TranscriptDesk`; `intake --from-url/--from-note` appends to the marker's
`came_from` list and the agent response carries only `came_from_count` (INV-12).
Generic by design: the mfp binding is an agent recipe (`skill/SKILL.md`), and R5,
R8 and R10 are unchanged. Outside the verification gate; the PIM amendments owed
are named in §22's banner.

**Round 12 ran on 2026-09-16 against v1.4** (`..._R12a.md`, `..._R12b.md`),
BLOCKED six ways, all answered in **v1.5**. **Round 13 ran the same day against
v1.5** — seven BLOCK from the structure axis, two from the numbers axis, and
both of the latter landed on evidence committed six hours earlier. Answered in
**v1.6**. The debt now is what the next thing built owes: the same ladder.

## Rulings in force — do not re-litigate

| # | Ruling | Origin |
|---|---|---|
| R1 | Everything runs **local**. No recording and no transcript text leaves the machine — plain text is a sufficient leak source on its own. **Superseded in part (2026-10-30)** for one step only: with the user's yes each time, candidate snippets may go to Claude Sonnet for the glossary pick (`src/ltm/AGENTS.md` §9a; CIM BR-13 note). | user, BR-13; user 2026-10-30 「預設不開啟LLM使用  但可以（必須先）詢問得到同意後使用sonnet med去跑校正」 |
| R2 | Judgement models run **local only**. | user |
| R3 | Reasoning models (DeepSeek R1, Qwen 3.5+) are **out** — thinking mode destabilises the output format. | user |
| R4 | The product must be **agent-drivable**, not GUI-only. | user |
| R5 | This product and `media-fetch-pipeline` are **completely separate**. Shared code is not a goal. | user |
| R6 | Never logs in. No credentials carried, stored, or requested. | mfp D-2, carried forward |
| R7 | Scope ceiling: 附帶功能 are「現況等值搬遷」only; nothing is redesigned inside milestone 1. | CIM §0.5 |
| R8 | **The separation is on disk, not only in code.** This product has its own output root and its own store vocabulary. Not one directory name, subfolder name or marker filename is shared with mfp, and neither product reads the other's tree at runtime. | user, 2026-09-09 |
| R9 | **The speech runtime lives inside this checkout, in `runtime\` (git-ignored)**: the speech interpreter (`runtime\asr-py`) and the model weights (`runtime\models\`). Nothing is shared with another product any more — mfp 2.0 removed ASR (D-162), which left this product the only consumer of the former shared folder `<WORKSPACE>/_runtimes\`, folded in here on 2026-09-19. Defaults derive from the repo root; `LTM_SIDECAR_PYTHON` / `LTM_MODEL_DIR` still override. Original ruling (2026-09-09): local infrastructure MAY be shared, and only the interpreter and the weights. | user, 2026-09-09; revised 2026-09-19 |
| R10 | **Transcripts made before this product get in through the ordinary door.** There is no migration verb and no importer that knows mfp's layout. The product has a generic intake (it needs one anyway, for arbitrary audio and arbitrary subtitle files); anything older is handed to that, by the user, or not at all. R8 therefore has **zero** exceptions. | user, 2026-09-09 |
| R11 | **No git remote. Local only.** Commit freely; never add a remote and never push. A remote is added when the user says so, not when a workflow finds it convenient. | user, 2026-09-09 |
| R12 | **The judgement model (`Advisor`) is out of milestone 1 entirely** — not disabled by default, *absent*. No `advisor` module, no `consulting` state, no advisor fields. INV-10/19/21 stay written and are marked milestone 2. Rationale and scope table: PIM §0.1. *Still true inside the product*; the consent-gated pick runs in the caller's subagent and leaves only `picked_by` on the edit record (PIM §0.1 note, 2026-10-30). | user, 2026-09-09 |
| R13 | **Cancellable, and progress must be real.** `detecting` is cancellable alongside `queued`/`decoding`/`awaiting-decision`. Every non-terminal state emits progress at least once a second, phases with a denominator report it, and a cancel request is answered within one event interval. The user's words: 「單純反灰然後等待很久…更多時間是空等」. Contract: PIM §6.1 / INV-22. | user, 2026-09-09 |
| R14 | **`_citable/` is agent-drivable, but every entry carries a human authorisation record** (`grantedBy`/`grantedAt`/`scope`/`method`), scoped to a single `<run>/<revision>`. No standing grants. Missing record → the verb fails loudly. Contract: PIM §9.5 / INV-20. | user, 2026-09-09 |
| R15 | **The human interface is a browser tab served by the standard library** — one process, zero new dependencies, no build step. A window of its own (pywebview) wraps the same page later if it is ever wanted; the page is the invariant, the shell is swappable. Rejected with reasons: Electron+React, NiceGUI, FastAPI. Full table: `docs/adr-gui-stack-2026-09-10.md`. | user, 2026-09-10 |
| R16 | **The command layer is settled before a face wraps it.** The user's words: 「指令層還不清晰就包上 GUI，結果還沒正確做好映射導致兩邊要改」. So the seam is written down first (`docs/gui-mapping-2026-09-10.md` + `gui-contract.json`), measured against real runs, and only then does an interface get built. | user, 2026-09-10 |
| R17 | **Three layers, three clean answers — never one compromise across all of them.** What identifies a thing (technical), how the code finds it (implementation), and what a person is shown (user-facing) are separate questions; each takes the cleanest answer for itself. The user's words: 「技術面跟實作面和使用者面要分開，各自選最乾淨的作法」, and what the person sees must be 直觀簡單. First application: a run's identity is the content hash, the lookup is by hash, and the folder keeps the readable filename — see PIM §9.2. | user, 2026-09-10 |

## R8 stated as a property of the assets, so it can be checked

R8 is the ruling most likely to erode by accident, because the cheapest way
to write any store is to copy the one that already works. So it is written as
a property of the assets, and the assets it binds are **the ones that execute
or ship** — source, tests, configuration, packaging, fixtures:

- No shipping file may contain mfp's on-disk vocabulary: `分析`, `字幕檔`,
  `文字檔`, `_inbox`, `_reference`, `_source.json`, `逐字稿`, `_captions`,
  `MediaGrabbed`.
- No shipping file may import from `mfp`, name a path under mfp's output
  root, or read `%APPDATA%\media-fetch-pipeline\`.
- No shipping file may name a path outside this product's own tree and its
  own output root. (Until 2026-09-19 the R9 runtime paths were the one
  exception; the runtime is inside the tree now, so there is none.)

**Prose is exempt and must be** — `docs/` and this file name both sides on
purpose, because a boundary nobody may describe cannot be reviewed. The first
draft of this rule said "no file in this repository", which this file violated
in the same commit that introduced it: a rule whose own repository fails it on
day one is not strict, it is mis-scoped, and the reader who hits the false
positive learns to ignore the check.

**Match path components and string literals, never free substrings.** `分析`
and `字幕檔` are also ordinary Chinese words, so a substring scan fires on any
prose that discusses the boundary — it did, on the PIM's own §9.4, the first
time this check was run. A gate that cries wolf on correct text is one readers
learn to skip.

**This is now a test rather than a paragraph**: `tests/test_layering.py`, and
it ships with both controls — `tests/fixtures/layering_bad.py_txt`, which DOES
name one of these as a path component and every check must flag, and
`tests/fixtures/prose_ok.md`, which merely discusses them and no check may
flag. The paragraph stays because it says WHY; the test is what says whether.

## The store, as this product names it

Settled by the user on 2026-09-09. Every name here was chosen for one
property: **no token in this column appears anywhere in mfp's**. That is what
makes R8 checkable by a machine rather than remembered by a reader.

| Slot | This product | mfp's, for contrast — never reuse |
|---|---|---|
| Output root (default) | `--root` > `$LTM_ROOT` > `<home>/TranscriptDesk` (PSM §22 P-1, 2026-11-22); the owner's machine sets `LTM_ROOT` to a data drive | `D:/output/MediaGrabbed` |
| Store | `校讀/` | `分析/` |
| Timed files (SRT, VTT) | `時軸/` | `字幕檔/` |
| Plain text | `全文/` | `文字檔/` |
| Run marker | `_origin.json` | `_source.json` |
| Intake | `_intake/` | `_inbox/` |
| Drop folder (a person fills it; the product only reads it) | `_錄音/` | `_inbox/` |
| Worth citing | `_citable/` | `_reference/` |
| Correction lexicons (a person writes them; `glossary` reads them) | `_詞表/` | — (mfp's glossary family was removed in 2.0.0) |
| Config home | `%APPDATA%\local-transcript-maker\` | `%APPDATA%\media-fetch-pipeline\` |

    <root>/
      校讀/
        <run>/
          時軸/    talk.zh.srt
          全文/    talk.zh.txt
          _origin.json
      _intake/
      _citable/

`校讀` names what the product does — say where the reader should distrust the
text — rather than where the text came from. The store is not called after a
verb mfp also owns, on purpose.

**The output root is configurable and the store vocabulary is not.** A user
may point the root anywhere; the names below it are the product's contract
with itself and are constants, not settings. Two products writing the same
folder name under a root the user happened to aim at both is exactly the
collision R8 exists to prevent.

## Windows notes

- Write file content with the Write/Edit tools, not heredocs or `>>` — Write
  emits LF, appends onto a CRLF file produce a mixed file, and `Out-File`
  prepends a BOM. Endings are pinned by the committed `.gitattributes`.
- Pass a commit message to git with `-F <file>`, never inline: PowerShell
  5.1's native-argument encoder does not escape `"` or `[`, and the
  truncation is silent.
