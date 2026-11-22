"""The language map: slots, per-stretch agreement, and BR-4.

PSM §5.5.1 / §5.5.2, against `docs/evidence/stretch_agreement_2026-09-09.py`.

The three things this file is really guarding, none of which is a number:

* **`agreement` is per stretch.** Round 6 (RC-3) found the schema defining it per
  `Stretch` while the document's only worked figure, 0.806, was a whole-file
  number over 72 slots -- so the one example computed a different quantity from
  the one the schema requires, and a reader could not tell whether the formula
  even produced different values per stretch. It does: 0.000 to 1.000.
* **The boundary comes from the primary signal alone.** The audio side votes into
  `agreement` and never cuts. Asserted by INVERTING the audio side and checking
  the boundaries do not move -- a claim about which signal is load-bearing, tested
  by removing the other one's information.
* **A `LangSlot` is not a `core.Window`.** 73 against 79 on the same recording,
  and `Window` has no language field. Six rounds of this document called both
  "window"; the test that keeps them apart reads the module's own source, because
  the populations differing is a symptom and the naming is the cause.
"""
from __future__ import annotations

import inspect
import pathlib
from dataclasses import dataclass

import pytest

from fixtures import decodes
from ltm.core import model
from ltm.detect import langmap

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "ltm" / "detect"

IN_REPO = decodes.in_repo()
AUDIO = decodes.audio_side()

#: What the instrument printed on the baseline (`stretch_agreement_*.txt`).
BASELINE_STRETCHES = 18
BASELINE_SLOTS = 73
BASELINE_WINDOWS = 79
BASELINE_UNDECIDABLE = [5]
BASELINE_BELOW_HALF = 5
BASELINE_DECIDABLE_AGREEMENT = 0.806
BASELINE_ALL_SLOTS_AGREEMENT = 0.795


@dataclass(frozen=True, slots=True)
class Bit:
    """A minimal `langmap.Timed`: start, end, text and nothing else.

    Deliberately not a `core.Cue`: a `Cue` cannot exist without a `stretch_id`,
    and the stretches are what this module computes. That the protocol is
    satisfiable by a three-field object is the point -- `langmap` runs before the
    cues do.
    """

    start: float
    end: float
    text: str


ZH = "這是一段中文的內容需要至少二十個字才算得出來"
EN = "this sentence carries plenty of latin letters for the primary signal"


def _grid(labels, *, slot=langmap.SLOT):
    """One `Bit` per 30-second slot, each filled with text of the given language.

    `None` means a slot with too little text to decide -- the case that must
    INHERIT rather than cut a boundary.
    """
    out = []
    for i, lab in enumerate(labels):
        text = {"zh": ZH, "en": EN, None: "嗯"}[lab]
        out.append(Bit(i * slot, i * slot + slot - 1.0, text))
    return out


def test_slot_population_is_not_the_window_population():
    """RF-2: two populations on one recording, and no shared vocabulary.

    The arithmetic half (73 slots from duration, a different number of windows
    from the decode metrics) and the naming half (this module never mentions a
    `Window`) are both asserted, because the populations differing is what a
    reader notices and the shared name is what caused it.
    """
    for rec in IN_REPO.values():
        assert langmap.slot_count(rec.duration) == BASELINE_SLOTS
        assert len(rec.windows) != BASELINE_SLOTS
    source = (SRC / "langmap.py").read_text("utf-8")
    body = source.split('"""', 2)[2]        # skip the module docstring, which
    #                                         must be free to explain the trap
    for forbidden in ("Window", "window_id"):
        assert forbidden not in body, forbidden


def test_the_final_partial_slot_counts_only_when_it_holds_a_second_of_audio():
    """2183.68 s is 73 slots -- **not 72, and not `ceil`'s 74 either.**

    Both bounds are load-bearing and both were wrong once. Dropping a 23.68 s tail
    silently shortens the recording, so `floor` is out. But plain `ceil` is out too,
    and that is the half this test asserted for one commit: the archived audio side
    was produced by `langmap_probe_2026-09-09.py`, which SKIPPED a final tail under
    a second, so a `ceil` module expects 74 slots where the instrument delivered 73
    and `slots()` refuses the whole file as a partial pass. A `detect_language` call
    on half a second is not a measurement, and the module has to agree with the
    instrument that measured the data it is fed.

    Both sides of `MIN_SLOT`, which is what makes it a threshold and not a default.
    """
    assert langmap.slot_count(2183.68) == 73        # 72 whole + a 23.68 s tail
    assert langmap.slot_count(60.0) == 2            # exactly 2, no tail at all
    assert langmap.slot_count(60.0 + langmap.MIN_SLOT) == 3      # the tail counts
    assert langmap.slot_count(60.01) == 2                        # ...and here not
    # A recording shorter than the tail minimum has no map rather than a one-slot
    # one, and a zero-length one is refused outright: an empty population is not a
    # population (INV-1).
    assert langmap.slot_count(langmap.MIN_SLOT) == 1
    with pytest.raises(model.ModelError):
        langmap.slot_count(0.5)
    with pytest.raises(model.ModelError):
        langmap.slot_count(0.0)


def test_the_primary_signal_needs_twenty_letters_of_either_script():
    """Both sides of MIN_LETTERS, which is what makes it a threshold and not a
    default: 19 letters is undecidable, 20 is a label."""
    assert langmap.text_side("a" * 19) is None
    assert langmap.text_side("a" * 20) == "en"
    assert langmap.text_side("一" * 20) == "zh"
    # A mixed slot goes to whichever script has more, and equality is not English:
    # the tie goes to `zh` because `l > c` is the test the evidence script ran.
    assert langmap.text_side("a" * 10 + "一" * 10) == "zh"
    assert langmap.text_side("a" * 11 + "一" * 10) == "en"


def test_malay_is_read_as_english_because_it_was_measured():
    """`ms` -> `en` is a measurement (`langmap_truth_*`), not a guess.

    The engine returns Malay for English speech in this recording. The alias table
    stays two entries long on purpose: one measurement licenses one alias, and a
    general alias map would be a rule with no evidence behind it.
    """
    assert langmap.normalise_audio("ms") == "en"
    assert langmap.normalise_audio("en") == "en"
    assert langmap.normalise_audio("zh") == "zh"
    assert langmap.normalise_audio("ja") == "ja"      # unknown stays itself


def test_undecidable_slot_inherits_its_stretch():
    """One quiet slot is not a language boundary (§5.5.1).

    Two-sided: the undecidable slot in the middle of an English run inherits
    English and the run stays ONE stretch; the known-false control is the same
    grid with a decidable Chinese slot in that position, which must cut it into
    three.
    """
    labels = ["en", "en", None, "en", "en"]
    cells = langmap.slots(_grid(labels), 5 * langmap.SLOT, ["en"] * 5)
    assert cells[2].text_side is None
    assert cells[2].label == "en"
    assert len({c.label for c in cells}) == 1
    assert len(langmap.stretches(_grid(labels), 5 * langmap.SLOT,
                                 ["en"] * 5)) == 1

    loud = ["en", "en", "zh", "en", "en"]
    assert len(langmap.stretches(_grid(loud), 5 * langmap.SLOT,
                                 ["en"] * 5)) == 3


def test_a_leading_undecidable_slot_inherits_forwards():
    """Backwards first, then forwards -- a file that opens quietly has no earlier
    stretch to inherit from, and inventing one would put a boundary at 30 s."""
    cells = langmap.slots(_grid([None, None, "zh", "zh"]), 4 * langmap.SLOT,
                          ["zh"] * 4)
    assert [c.label for c in cells] == ["zh"] * 4


def test_an_entirely_undecidable_file_says_undetermined_rather_than_a_language():
    """The evidence script defaults to "zh" here and that branch never ran.

    An unexercised default that names a language is a claim; `undetermined` is the
    word BR-4 already has for having nothing to go on. This is a deliberate
    deviation from the script, recorded because it is the only one.
    """
    items = [Bit(0.0, 29.0, "嗯"), Bit(30.0, 59.0, "嗯")]
    cells = langmap.slots(items, 60.0, ["zh", "zh"])
    assert [c.label for c in cells] == ["undetermined"] * 2
    with pytest.raises(model.ModelError):
        langmap.summary(cells)          # no text side to agree with


def test_a_missing_audio_side_is_refused_rather_than_defaulted():
    """E-1's shape in another field: "not measured" must not read as "disagreed".

    With no audio side every `agreement` computes to 0.0, BR-4 reads 0.0 as
    「這段分不出來」, and the product would report "cannot tell them apart" for a run
    where nobody looked. So a short list raises, and so does an empty label.
    """
    items = _grid(["zh", "zh"])
    with pytest.raises(model.ModelError):
        langmap.slots(items, 60.0, ["zh"])
    with pytest.raises(model.ModelError):
        langmap.slots(items, 60.0, ["zh", ""])
    assert len(langmap.slots(items, 60.0, ["zh", "zh"])) == 2


def test_there_is_no_smoothing_knob():
    """Measured, every width is worse than none (0.806 raw to 0.681 at k=11).

    So the parameter is ABSENT rather than defaulted to 1: a default invites a
    caller to raise it, and raising it buys fewer stretches by erasing real English
    passages. This test is what makes "absent" a property of the asset instead of
    a sentence in a docstring.
    """
    for fn in (langmap.slots, langmap.stretches):
        params = set(inspect.signature(fn).parameters)
        assert not params & {"smooth", "k", "window", "kernel"}


def test_the_boundary_comes_from_the_primary_signal_alone():
    """Invert the audio side: boundaries identical, agreement different (§5.5.2).

    This is the assertion that keeps the retired annealing signal out too -- any
    second signal that cuts would move these numbers. Its 0/11 independence was
    measured against a DIFFERENT primary, and an independence measured against A
    does not vouch for the pair B.
    """
    rec = IN_REPO[decodes.COND_FALSE]
    straight = langmap.stretches(rec.cues, rec.duration, AUDIO)
    flipped = langmap.stretches(
        rec.cues, rec.duration,
        ["en" if a == "zh" else "zh" for a in AUDIO])
    assert [(s.start, s.end) for s in straight] == [(s.start, s.end)
                                                   for s in flipped]
    assert [s.agreement for s in straight] != [s.agreement for s in flipped]


def test_cut_points_snap_to_silence_and_only_within_three_seconds():
    """PIM §2.5.4, with the measured case and a known-false control.

    The known boundary at 1950 s has a 7.9 s gap starting 1949.1 -- 0.9 s away, so
    ±3.0 s reaches it. A gap 4 s away must NOT pull the cut: a snap window wide
    enough to always find something is not a snap, it is a second boundary rule.
    """
    gaps = ((1949.1, 1957.0),)
    assert langmap.snap(1950.0, gaps) == 1949.1
    assert langmap.snap(1955.0, gaps) == 1955.0        # 5.9 s away, stays put
    assert langmap.snap(1950.0, ()) == 1950.0          # nothing to snap to


def test_silence_is_a_snap_target_never_a_trigger():
    """16 gaps over 1 s on the baseline, 3 over 5 s, and 18 stretches.

    If silence triggered a cut the two counts would be related; they are not, and
    the 58 s gap at 134-192 s is a Q&A pause in the middle of one language.
    """
    rec = decodes.baseline()
    gaps = langmap.silence_gaps(rec.cues)
    assert len(gaps) == 16
    assert len([g for g in gaps if g[1] - g[0] > 5.0]) == 3
    assert len(langmap.stretches(rec.cues, rec.duration,
                                 AUDIO)) == BASELINE_STRETCHES


def test_agreement_is_per_stretch():
    """18 values on the baseline, spanning the full interval (BR-4, RC-3).

    A whole-file number cannot say WHICH passages the product cannot tell apart,
    which is the entire reason this product exists.
    """
    rec = decodes.baseline()
    stretches = langmap.stretches(rec.cues, rec.duration, AUDIO)
    assert len(stretches) == BASELINE_STRETCHES
    values = [s.agreement for s in stretches]
    assert min(values) == 0.0 and max(values) == 1.0
    assert len(set(values)) > 1           # the formula is not a constant


def test_below_half_is_undetermined():
    """5 of 18 (§5.5.1), and 0.5 is a DEFINITION -- no majority.

    Both directions are asserted: every stretch under 0.5 reports `undetermined`
    and every stretch at or above it reports a language. `core.Stretch` enforces
    the same rule in `__post_init__`, so this test is checking that the mapping
    HAPPENED -- the type can only refuse an inconsistent pair, it cannot make the
    detector degrade the label.
    """
    rec = decodes.baseline()
    stretches = langmap.stretches(rec.cues, rec.duration, AUDIO)
    low = [s for s in stretches if s.agreement < model.NO_MAJORITY]
    assert len(low) == BASELINE_BELOW_HALF
    assert all(s.language == "undetermined" for s in low)
    assert all(s.language in ("zh", "en")
               for s in stretches if s.agreement >= model.NO_MAJORITY)
    assert model.NO_MAJORITY == 0.5


def test_the_disagreement_is_concentrated_not_spread():
    """§5.5.2's finding, which is what changed open item B's shape.

    All 9 Mandarin stretches at 1.000 and all 4 single-slot English stretches at
    0.000. A file-level 0.806 would have hidden both facts; owning the formula did
    not produce a trustworthy number, but it did let the product say WHICH passages
    it cannot tell apart.
    """
    rec = decodes.baseline()
    cells = langmap.slots(rec.cues, rec.duration, AUDIO)
    stretches = langmap.stretches(rec.cues, rec.duration, AUDIO)
    runs, start = [], 0
    for i in range(1, len(cells) + 1):
        if i == len(cells) or cells[i].label != cells[start].label:
            runs.append((start, i - 1))
            start = i
    assert len(runs) == len(stretches)
    zh = [s for (a, _b), s in zip(runs, stretches) if cells[a].label == "zh"]
    en_single = [s for (a, b), s in zip(runs, stretches)
                 if cells[a].label == "en" and a == b]
    assert len(zh) == 9 and {s.agreement for s in zh} == {1.0}
    assert len(en_single) == 4 and {s.agreement for s in en_single} == {0.0}


def test_undecidable_slots_on_the_baseline_are_where_the_instrument_found_them():
    rec = decodes.baseline()
    cells = langmap.slots(rec.cues, rec.duration, AUDIO)
    assert len(cells) == BASELINE_SLOTS
    assert len(rec.windows) == BASELINE_WINDOWS
    assert [c.index for c in cells if c.text_side is None] == BASELINE_UNDECIDABLE


def test_the_whole_file_figures_are_labelled_as_summaries():
    """0.806 over 72 decidable slots, 0.795 over all 73 -- and neither is the field.

    The type is what enforces the labelling: printing one figure without its
    population is not expressible, because `FileSummary.__str__` carries both and
    the word "summary". The PIM quoted 0.806 for two versions while the schema
    defined `agreement` per stretch.
    """
    rec = decodes.baseline()
    got = langmap.summary(langmap.slots(rec.cues, rec.duration, AUDIO))
    assert round(got.decidable, 3) == BASELINE_DECIDABLE_AGREEMENT
    assert got.decidable_population == 72
    assert round(got.all_slots, 3) == BASELINE_ALL_SLOTS_AGREEMENT
    assert got.population == BASELINE_SLOTS
    text = str(got)
    assert "summary" in text and "per stretch" in text
    assert "72" in text and "73" in text


def test_stretches_cover_the_recording_with_no_gap_and_no_overlap():
    """Every `Cue` must resolve to exactly one stretch (INV-1), so the set has to
    tile [0, duration]. The last slot runs past the end of the audio (73 x 30 =
    2190 s against 2183.68), which is why the final stretch is clamped."""
    for rec in IN_REPO.values():
        stretches = langmap.stretches(rec.cues, rec.duration, AUDIO)
        assert stretches[0].start == 0.0
        assert stretches[-1].end == rec.duration
        for a, b in zip(stretches, stretches[1:]):
            assert a.end == b.start
            assert a.start < a.end
