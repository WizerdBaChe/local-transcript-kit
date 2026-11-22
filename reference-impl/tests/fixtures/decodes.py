"""The three decodes of PSM §6.3, as `core.Recording` objects.

One place, because three test files need the same conversion and three copies of
it would drift -- and a drift here moves every asserted number at once.

**`cond_experiment_segments.json` is a FROZEN FIXTURE and must be read, never
regenerated** (PIM §4.1.6, round 6 RD-2). A re-run on the same machine, model and
audio gives 958 segments with a 23x run of '去' where the saved file has 936 with a
27x run of '對': the DIRECTION of every degradation metric reproduces and none of
the specifics do. So `412-438` is a property of that archived decode, not of the
experiment, and a test that decodes live would fail intermittently -- or pass by
luck and hide that nothing was exercised.

**The baseline decode is IN this repository too, since 2026-09-09 (user ruling,
UAT I-17).** It used to arrive through `LTM_BASELINE_PAYLOAD` so that no shipping
file named an external path -- an R8 consequence, not a convenience. The variable
is gone, and R8 is better off for it: a file that is here names no path at all.
Two things forced the change on the same evening:

* the copy the variable pointed at **moved**, because it was the OTHER product's
  evidence and that product took it back into its own repository. Nineteen tests
  went from green to skipped without a line of this repository changing, which is
  a calibration that depends on somebody else's directory layout;
* a skip is honest but it is not a measurement. `LTM_BASELINE_PAYLOAD` unset was
  the ordinary case on this machine, so the labelled control set was usually NOT
  being checked -- and the run said `19 skipped` rather than saying so.

**What was copied is the SEGMENTS, not the payload.** The original's `engine`
block names the model under the other product's output root, and a shipping
fixture may not carry that (R8). The four hits for `分析` that remain are the
speaker saying the ordinary Chinese word for "analyse"; R8 matches path
components and string literals, never free substrings, and
`cond_experiment_segments.json` has carried eight of them since it landed.
Provenance -- the capture commit and what was dropped -- is in
`docs/evidence/README.md`, where prose is allowed to name both sides.
"""
from __future__ import annotations

import json
import pathlib
from typing import Any, Optional, Sequence

from ltm.core import model

HERE = pathlib.Path(__file__).resolve().parent
FROZEN = HERE.parent.parent / "docs" / "evidence" / "cond_experiment_segments.json"
LANGMAP_RAW = HERE.parent.parent / "docs" / "evidence" / "langmap_raw_2026-09-09.json"

#: The baseline decode, frozen in this repository beside its sibling.
BASELINE_SEGMENTS = HERE.parent.parent / "docs" / "evidence" / \
    "baseline_segments_2026-09-08.json"

#: The three decodes, by the names PSM §6.3's table uses.
BASELINE = "baseline"
COND_TRUE = "cond=True"
COND_FALSE = "cond=False"


def _metrics(seg: dict[str, Any]) -> tuple[float, float, float, float]:
    return (seg["avgLogprob"], seg["temperature"],
            seg["compressionRatio"], seg["noSpeechProb"])


def recording(segments: Sequence[dict[str, Any]], *, id: str,
              duration: Optional[float] = None) -> model.Recording:
    """Build the four layers from one decode's segments.

    **Windows are runs in time order, not distinct metric tuples** (PSM §6.1 /
    PIM §4.1.4). The baseline has 79 runs and 77 distinct tuples, so counting
    distinct would silently shrink every denominator by two -- and this helper is
    where that choice would be invisible.

    The single `Stretch` is a CONTAINER, not a language verdict: every `Cue` must
    name one (INV-1), and this file has no business asserting a language. It is
    `undetermined` with `agreement` 0.0, which is BR-4's own spelling for "no
    label has majority support from two independent signals" -- here because
    neither signal was consulted. `test_langmap.py` computes the real ones.
    """
    duration = duration or max(s["end"] for s in segments)
    stretch = model.Stretch(id="st-fixture", start=0.0, end=duration,
                            language="undetermined", agreement=0.0)

    windows: list[model.Window] = []
    window_of: list[str] = []
    prev: Optional[tuple[float, float, float, float]] = None
    for seg in segments:
        m = _metrics(seg)
        if prev == m and windows:
            windows[-1] = model.Window(
                id=windows[-1].id, start=windows[-1].start, end=seg["end"],
                avg_logprob=m[0], temperature=m[1],
                compression_ratio=m[2], no_speech_prob=m[3])
        else:
            windows.append(model.Window(
                id=f"w{len(windows):03d}", start=seg["start"], end=seg["end"],
                avg_logprob=m[0], temperature=m[1],
                compression_ratio=m[2], no_speech_prob=m[3]))
        window_of.append(windows[-1].id)
        prev = m

    cues = tuple(
        model.Cue(id=f"c{i}", start=seg["start"], end=seg["end"],
                  text=seg["text"], window_id=window_of[i],
                  stretch_id=stretch.id)
        for i, seg in enumerate(segments))
    return model.Recording(
        id=id, path=f"<fixture:{id}>", duration=duration,
        sha256="0" * 64,
        report=model.EngineReport(
            vad_kept=None, vad_enabled=False,
            condition_on_previous_text=None,
            multilingual=None,
            degenerated=any(s["temperature"] >= 1.0 for s in segments)),
        stretches=(stretch,), windows=tuple(windows), cues=cues)


def frozen() -> dict[str, Any]:
    return json.loads(FROZEN.read_text("utf-8"))


def in_repo() -> dict[str, model.Recording]:
    """The two decodes that need no external prerequisite.

    Together they ARE the two-sided calibration: `cond=True` is the positive
    control (a decode with a loop, which must yield a proposal) and `cond=False`
    the negative one (the shipping default, which must yield none). A build that
    can only run these two still has a calibrated detector; what it has lost is
    the labelled control set.
    """
    full = frozen()
    return {
        COND_TRUE: recording(full["True"]["segments"], id=COND_TRUE,
                             duration=full["True"]["summary"]["duration"]),
        COND_FALSE: recording(full["False"]["segments"], id=COND_FALSE,
                              duration=full["False"]["summary"]["duration"]),
    }


def baseline_segments() -> list[dict[str, Any]]:
    """The baseline decode. **Read, never regenerated** -- same rule as its sibling.

    INV-17 used to be served here by a skip that named the variable rather than the
    path. It is now served better: the labelled control set (the 9x loop, idx 550,
    idx 346) is checked on every run, so there is no configuration of this machine
    in which the suite reports green without having checked it.

    An absent file is a hard failure, not a skip. A frozen fixture that has gone
    missing is a broken repository, and answering that with a skip is the shape
    INV-17 exists to forbid.
    """
    if not BASELINE_SEGMENTS.is_file():
        raise FileNotFoundError(
            f"the frozen baseline is missing: {BASELINE_SEGMENTS.name}. Do NOT "
            f"regenerate it by decoding -- decodes are not reproducible (I-1) and "
            f"a fresh one would be a different recording under this one's name. "
            f"Restore it from git.")
    return json.loads(BASELINE_SEGMENTS.read_text("utf-8"))["segments"]


def baseline() -> model.Recording:
    segs = baseline_segments()
    return recording(segs, id=BASELINE, duration=2183.68)


def audio_side() -> list[str]:
    """The per-slot audio labels, measured once and archived in the repository.

    `detect.langmap` takes them as data because `detect` may import `core` only --
    the encoder pass belongs to the engine. That the numbers are archived is what
    makes the language map testable without a GPU, and it is also why the file is
    read rather than recomputed: `detect_language` on 73 slots is 21.7 s.
    """
    rows = json.loads(LANGMAP_RAW.read_text("utf-8"))
    return [row[2] for row in rows]
