---
name: local-transcript-maker
description: Transcribe a local recording and show where the transcript should not be trusted, using the local `ltm` CLI (local-transcript-maker). Use when the user wants a recording turned into a transcript or subtitles (「把這段錄音轉成逐字稿」「幫我出字幕」「轉寫這個 wav/m4a」), wants an existing ltm transcript checked or corrected (「哪裡可能聽錯」「用詞表校正」「術語錯字」), or asks about a run under the ltm output root ($LTM_ROOT, else <home>/TranscriptDesk). Consent-gated LLM correction (Claude Sonnet) is OFF by default and runs only after asking. Not for downloading media from a URL (→ media-fetch-pipeline), not for speech-subtitle-lite's fixed pipeline.
---

# local-transcript-maker (`ltm`)

A local tool that transcribes a recording and makes its uncertainty visible. You
call it; you do not reimplement any of it. **The contract is `ltm agent guide`**
(the packaged `AGENTS.md`); read it before the first call in a session. This file
says only when to call and what never to do.

## The ordinary flow

```text
ltm intake <audio>                         -> run id
ltm decode <run>                           -> r000 (large-v3, per-window language)
ltm detect <run> --rev r000 [--lexicon <talk.json>]
ltm plan <run> --rev <detect rev>          -> findings, ops (--op N), candidates (--candidate N)
ltm accept <run> --rev <detect rev> [--op N ...] [--candidate N ...]
```

Add `--json` and parse stdout. Every `accept` writes a NEW revision; nothing is
edited in place. `accept` with no `--op` applies every op in the plan.

## Starting from a post URL (you are the integrator)

ltm never downloads and never reads another tool's folders. When the user starts
from a URL, a downloader fetches the file and YOU pass two things across: the
local path and where it came from. With media-fetch-pipeline (`mfp`) installed:

1. `mfp probe <url> --write-subs --json` — if an item's sidecars include
   `kind: captions`, the platform already has a human-written transcript: say so,
   fetch it with `mfp fetch <url> --write-subs`, and offer ltm only to compare.
   `auto-captions` is machine recognition too: ask which the user wants.
2. Otherwise `mfp fetch <url> --json`, take `items[]` with `status: ok` (a video)
   and its `path`, and `posts[].url`.
3. `ltm intake <path> --from-url <posts[].url> --from-note "mfp <version> <postId>"`,
   then the ordinary flow above.

`mfp` is optional: if `mfp agent-guide` does not answer, do not install it
unasked; ask the user for a local file instead. ltm does not promise a character
accuracy; it marks where the transcript should not be trusted.

## Correction lexicons

`<root>/_詞表/*.json` (`ltm-lexicon/1`) are read on every `glossary` run; a talk's
own list (slide terms, names) is passed with `--lexicon`. A declared wrong form
becomes a `replace` op; a sound-alike becomes a dormant candidate, applied only
when named. Language case is read per cue from the text — no need to tell the
tool whether a talk is pure Mandarin or mixed with English.

A VIDEO with burned-in subtitles: `tools/burnsub_lexicon.py` (repo) drafts a talk
lexicon from them, but it is EXPERIMENTAL. Read its STATUS header before the first
run: it helps only when the subtitles are in the spoken language, it has never been
measured on such a video, and run `BV1GGxr6iEEW_p1_00_1080p-89002b1a`'s r001/r002
are trial output, never accepted. Such a video is the trigger in
`<CLAUDE_HOME>/outputs/burnsub-lexicon-20261102/提案單_燒錄字幕詞表調整_20261102.md`:
do item 1 there and record the result in that file.

## AI use (LLM correction) — off by default

User ruling 2026-10-30: off by default; **ask first, every time**; on a clear yes,
run Claude Sonnet at medium effort over the glossary candidates, after the lexicon
and frequency passes. The question must say the transcript text leaves this
machine (to Anthropic) and how much (the pack states its character count). The
model picks candidate NUMBERS only. After `accept`, feed the corrections back
with `python -m ltm.detect.review learn` into the talk's `.learned.json`, so the
next run of the same series needs no model. Exact procedure and the cheaper
alternatives: `AGENTS.md` §9a (steps 8–9).

## Never

- Send transcript text or audio anywhere without that consent (R1).
- Log in, or carry or ask for credentials (R6).
- Touch media-fetch-pipeline's tree or reuse its folder names (R8).
- Add a git remote or push in the ltm repository (R11).
- Read a `undetermined` stretch as a language, or a note as a finding.
