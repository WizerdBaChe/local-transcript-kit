"""The language map: 30-second slots, two independent signals, one `Stretch` set.

PSM sections 5.5.1 and 5.5.2. `docs/evidence/stretch_agreement_2026-09-09.py` is
the definition this module reproduces; where the two disagree, **the script is
right** and `tests/test_langmap.py` is what says so.

**The unit here is a `LangSlot`, and it is NOT `core.Window`** (PIM section 2.5.35,
round 7 RF-2). Two different populations on the same recording -- **73** slots
(duration / 30 s) against **79** windows (runs of equal decode metrics) -- and
`Window` has no language field at all, so language voting cannot be a walk over
windows. Nothing here ever writes a label back onto a `Window`. Naming both
"window" is how a reader ends up looking for `Window.language` and finding
nothing, which is exactly what happened for six rounds.

Two signals, and which one may cut a boundary is not symmetric:

* **primary, text side** -- the slot's CJK-to-Latin character mix. Free, and it
  is the ONLY signal that cuts a boundary.
* **second, audio side** -- `detect_language` on the slot's audio, one encoder
  pass per slot (21.7 s against a 189.8 s decode, 11%). It never cuts anything;
  it votes into `agreement` and nowhere else. It arrives here as DATA, because
  `detect` may import `core` only -- the engine measures it and hands it over.

`Stretch.agreement` is the fraction of slots in THAT stretch where the two agree,
one number per stretch (0.000 to 1.000 across the baseline's 18). Whole-file
figures exist and are **summaries** -- `FileSummary` exists so that a caller
cannot print one without the word.

**No smoothing, and no knob for it.** Measured, every width is worse than none:
0.806 raw, 0.778 (k=3), 0.750 (k=5), 0.722 (k=7), 0.681 (k=11), against an
all-Chinese floor of 0.639. Fewer stretches looks tidier and is bought by erasing
real English passages, so the parameter is absent rather than defaulted to 1.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Sequence

from ..core import model

#: The grid, aligned to 0, matching Whisper's own decode window.
SLOT = 30.0
#: Below this many letters of either script the text side is undecidable.
MIN_LETTERS = 20
#: A final partial slot narrower than this is not a slot at all (see `slot_count`).
MIN_SLOT = 1.0
#: A cut point snaps to the nearest silence within this many seconds (PIM
#: section 2.5.4). The known 1950 s boundary has a gap starting 0.9 s away, so
#: 3.0 s reaches it without crossing a 30 s slot edge.
SNAP = 3.0
#: A gap between consecutive cues counts as silence when it is STRICTLY wider than
#: this. Strictly, because that is the count the evidence measured -- 16 gaps over
#: 1 s on the baseline, and 24 if the boundary is inclusive: this decode's
#: timestamps are quantised to 0.5 s and eight gaps land on exactly 1.00 s, which
#: is as likely to be quantisation as measured silence. Silence is a snap TARGET,
#: never a trigger: most of the 16 are unrelated to language (the 58 s gap at
#: 134-192 s is a Q&A pause).
MIN_SILENCE = 1.0
#: The engine returns Malay for English speech in this recording. **Measured**
#: (`docs/evidence/langmap_truth_2026-09-09.py`), not assumed, and it stays a
#: two-entry tuple rather than a general alias table because one measurement
#: licenses exactly one alias.
AUDIO_AS_EN = ("en", "ms")


class Timed(Protocol):
    """Anything with a start, an end and text.

    A protocol rather than `Sequence[core.Cue]` because this module runs BEFORE
    the cues exist: a `Cue` cannot be constructed without a `stretch_id`, and the
    stretches are what this module computes. `engine.contract.RawSegment` and
    `core.Cue` both satisfy it, and `detect` still imports nothing but `core`.
    """

    start: float
    end: float
    text: str


@dataclass(frozen=True, slots=True)
class LangSlot:
    """One 30-second cell of the grid, with both signals and its final label.

    `audio_side` is a plain `str`, not `core.Language`: the encoder may name a
    language this product has no label for, and coercing "the encoder said
    Japanese" into `undetermined` would turn a measurement into an admission of
    ignorance. They are different facts, so they get different spellings.
    """

    index: int
    start: float
    end: float
    text_side: Optional[str]      # None = undecidable (fewer than MIN_LETTERS)
    audio_side: str
    label: str                    # after inheritance; what cuts the boundary

    @property
    def agrees(self) -> bool:
        return self.audio_side == self.label


@dataclass(frozen=True, slots=True)
class FileSummary:
    """A whole-file figure, carrying the word "summary" in its type.

    Three defensible numbers exist on the baseline and **only the per-stretch one
    is the field**: 0.806 over the 72 decidable slots, 0.795 over all 73, and the
    18 per-stretch values. This type exists so that printing the first two is
    impossible without saying which population they came from -- the PIM quoted
    0.806 for two versions while the schema defined `agreement` per stretch, and
    a reader could not tell the two apart.
    """

    decidable: float
    decidable_population: int
    all_slots: float
    population: int

    def __str__(self) -> str:
        return (f"summary only (the field is per stretch): "
                f"{self.decidable:.3f} over {self.decidable_population} "
                f"decidable slots, {self.all_slots:.3f} over all "
                f"{self.population}")


def _cjk(s: str) -> int:
    # The evidence script's range, deliberately not widened to the CJK extension
    # blocks: 0.806 was measured with this range, and a wider one would change
    # the number with no measurement behind it.
    return sum(1 for c in s if "一" <= c <= "鿿")


def _latin(s: str) -> int:
    return sum(1 for c in s if c.isascii() and c.isalpha())


def slot_count(duration: float) -> int:
    """INV-1's population for everything in this module.

    The last partial slot counts when it holds at least `MIN_SLOT` of audio:
    2183.68 s gives **73** slots and not 72, because dropping a 23.68 s tail
    silently shortens the recording -- and not 74, because
    `langmap_probe_2026-09-09.py` skipped a tail under a second and this module has
    to agree with the instrument that produced the archived audio side. A
    `detect_language` call on half a second is not a measurement.

    `engine/sidecar.py` re-declares this arithmetic (it runs under an interpreter
    that does not have the product on its path) and `tests/test_engine.py` asserts
    the two agree, because the audio side arriving with a different slot count is
    exactly the "partial pass" `slots()` refuses.
    """
    if duration <= 0:
        raise model.ModelError(f"duration must be positive: {duration}")
    whole, rest = divmod(duration, SLOT)
    n = int(whole) + (1 if rest >= MIN_SLOT else 0)
    if n == 0:
        raise model.ModelError(
            f"a recording of {duration} s is shorter than one slot's minimum "
            f"({MIN_SLOT} s); it has no language map rather than a one-slot one")
    return n


def text_side(text: str) -> Optional[str]:
    """The primary signal. `None` means undecidable, which is not a label.

    Undecidable is a third outcome on purpose: a slot with four words in it is
    not evidence of a language boundary, and forcing it to pick would put a
    boundary wherever somebody paused.
    """
    c, l = _cjk(text), _latin(text)
    if c + l < MIN_LETTERS:
        return None
    return "en" if l > c else "zh"


def normalise_audio(lang: str) -> str:
    return "en" if lang in AUDIO_AS_EN else lang


def slot_text(items: Sequence[Timed], start: float, end: float) -> str:
    return " ".join(i.text for i in items if i.end > start and i.start < end)


def slots(items: Sequence[Timed], duration: float,
          audio: Sequence[str]) -> tuple[LangSlot, ...]:
    """The grid, both signals, and the inherited label per slot.

    `audio` is required and must cover every slot. A missing audio side is
    refused rather than defaulted, because the default would be silent: with no
    second signal every `agreement` computes to 0.0, BR-4 reads 0.0 as 「這段分不
    出來」, and the product would report "cannot tell them apart" for a run where
    nobody looked. That is E-1's defect in another field -- "not reported" and
    "reported as no" must not share a spelling.
    """
    n = slot_count(duration)
    if len(audio) != n:
        raise model.ModelError(
            f"the audio side covers {len(audio)} slots and this recording has "
            f"{n}; a partial pass is refused rather than averaged, because a "
            f"slot nobody measured is not a disagreement")
    if any(not a for a in audio):
        raise model.ModelError("an empty audio label is not a measurement")

    primary = [text_side(slot_text(items, i * SLOT, (i + 1) * SLOT))
               for i in range(n)]
    labels = _inherit(primary)
    return tuple(
        LangSlot(index=i, start=i * SLOT, end=min((i + 1) * SLOT, duration),
                 text_side=primary[i], audio_side=normalise_audio(audio[i]),
                 label=labels[i])
        for i in range(n))


def _inherit(primary: Sequence[Optional[str]]) -> list[str]:
    """An undecidable slot inherits the stretch it sits in. One quiet slot is
    not a language boundary.

    Backwards first, then forwards for a leading gap. If EVERY slot is
    undecidable the result is `undetermined`, not a language: the evidence
    script defaults to "zh" there, and that branch never ran on the baseline
    (1 of 73 slots is undecidable). An unexercised default that names a
    language is a claim; `undetermined` is the word BR-4 already has for
    having nothing to go on.
    """
    out: list[Optional[str]] = list(primary)
    for i in range(len(out)):
        if out[i] is None:
            j = i - 1
            while j >= 0 and out[j] is None:
                j -= 1
            out[i] = out[j] if j >= 0 else None
    for i in range(len(out) - 1, -1, -1):
        if out[i] is None:
            out[i] = out[i + 1] if i + 1 < len(out) else "undetermined"
    return [x or "undetermined" for x in out]


def silence_gaps(items: Sequence[Timed],
                 min_gap: float = MIN_SILENCE) -> tuple[tuple[float, float], ...]:
    """Gaps between consecutive items. The DEFAULT and verified source.

    Not `EngineReport.vad_kept`: under E-2(b) that field has no meaning when VAD
    did not run, and the one method that actually hit the known 1950 s boundary
    used segment gaps (1949.1 -> 1957.0, a 7.9 s gap straddling it) without
    consulting VAD at all. PIM v0.4 called this method "guessing" and it is the
    only method with a measured hit.
    """
    out = []
    for a, b in zip(items, items[1:]):
        if b.start - a.end > min_gap:
            out.append((a.end, b.start))
    return tuple(out)


def snap(t: float, gaps: Sequence[tuple[float, float]],
         window: float = SNAP) -> float:
    """Move a cut point to the nearest silence START within `window`.

    The start of the gap rather than its middle: the measured case is a 7.9 s gap
    beginning 0.9 s before the known boundary, and a midpoint would land 3.1 s
    past it -- outside the window that was calibrated to reach it.
    """
    best, dist = t, window
    for start, _end in gaps:
        d = abs(start - t)
        if d <= dist:
            best, dist = start, d
    return best


def stretches(items: Sequence[Timed], duration: float,
              audio: Sequence[str]) -> tuple[model.Stretch, ...]:
    """Runs of the primary label, snapped to silence, with agreement per stretch.

    Two rules that look like one and are not:

    * **The boundary is the primary signal's, always.** The audio side never cuts.
      The retired annealing signal must not come back as a third boundary signal
      either -- its 0/11 independence was measured against a DIFFERENT primary,
      and an independence measured against A does not vouch for the pair B
      (PIM v0.8 section 2.5.32). It survives as S4(b) in the loop detector, which
      is where a decoder-distress measure belongs.
    * **The LABEL may degrade to `undetermined`; the boundary does not move.**
      `agreement < 0.5` means no label has majority support from two independent
      signals, so the stretch reports `undetermined` -- but it is still that
      stretch, cut where the primary cut it. Five of the baseline's 18 degrade.
      0.5 is a DEFINITION ("no majority"), not a tuned threshold: there is no
      ground truth here, so a swept optimum would only measure how alike the two
      signals are. Do not "improve" it with a sweep.
    """
    cells = slots(items, duration, audio)
    gaps = silence_gaps(items)

    runs: list[tuple[int, int]] = []
    start = 0
    for i in range(1, len(cells) + 1):
        if i == len(cells) or cells[i].label != cells[start].label:
            runs.append((start, i - 1))
            start = i

    out = []
    for k, (a, b) in enumerate(runs):
        agree = sum(1 for c in cells[a:b + 1] if c.agrees)
        # INV-1: the population is the slot count inside THIS stretch, which is
        # also why the number is not comparable between stretches of unequal
        # length -- a 1-slot stretch's 0.000 is one slot, not a trend.
        agreement = agree / (b - a + 1)
        label = cells[a].label
        language = label if agreement >= model.NO_MAJORITY else "undetermined"
        if language not in ("zh", "en", "undetermined"):
            # The primary can only say zh/en/undetermined, so this is
            # unreachable by construction; it raises rather than coercing,
            # because coercing an unknown label into `undetermined` would hide a
            # bug in the signal behind a legitimate-looking verdict.
            raise model.ModelError(f"primary produced an unknown label: {label!r}")
        t0 = 0.0 if a == 0 else snap(cells[a].start, gaps)
        # The last slot runs past the end of the audio (73 x 30 = 2190 s against
        # 2183.68 s), so the final stretch is clamped: a stretch claiming audio
        # that does not exist would let `_stretch_at` resolve a timestamp beyond
        # the recording.
        t1 = duration if b == len(cells) - 1 else snap(cells[b].end, gaps)
        out.append(model.Stretch(id=f"st{k:03d}", start=t0, end=t1,
                                 language=language, agreement=agreement))
    return tuple(out)


def summary(cells: Sequence[LangSlot]) -> FileSummary:
    """Both whole-file figures at once, so neither can be quoted alone."""
    if not cells:
        raise model.ModelError("no slots: a summary needs a population")
    decidable = [c for c in cells if c.text_side is not None]
    if not decidable:
        raise model.ModelError(
            "every slot is undecidable; there is no text side to agree with")
    return FileSummary(
        decidable=sum(1 for c in decidable if c.agrees) / len(decidable),
        decidable_population=len(decidable),
        all_slots=sum(1 for c in cells if c.agrees) / len(cells),
        population=len(cells))
