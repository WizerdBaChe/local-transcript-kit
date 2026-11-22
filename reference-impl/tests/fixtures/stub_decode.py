"""One `LTM_STUB` decode, shared by every test that needs a whole pipeline.

Here rather than in one of the test modules because two test files need it and a
test importing another test file is a dependency nobody declared. It describes a
90-second recording, and every property of it is load-bearing:

* **Twelve 7.5-second segments** over three 30-second language slots, so the
  language map has a grid with more than one cell in it.
* **Texts over `langmap.MIN_LETTERS`** (20 letters of one script). Under that every
  slot is undecidable and the map runs without ever producing a label -- a pipeline
  test would then be green on a product whose language map did nothing.
* **A language boundary in the middle**: six Mandarin cues then six English ones, so
  a stretch boundary exists to be found.
* **Six identical cues in a row**, twice. S3 needs five, so this decode contains
  something the detector must find -- which is what makes `accept` testable instead
  of skippable, and a skipped acceptance test is a gate that switched itself off.
* **A repeated unit inside each cue** (each text is one sentence twice). That is S2's
  structure, so INV-6's two independent signals are both available and a deletion
  proposal is legal rather than downgraded to a note.
"""
from __future__ import annotations

ZH = "我們今天要談的是這個實驗的結果還有它的限制在哪裡" * 2
EN = "the point of this experiment is to show where the limits actually are" * 2

#: Total audio the decode claims, in seconds. 12 x 7.5 = 90, which is three whole
#: 30-second slots and no partial tail -- so `slot_count`'s tail rule is not what
#: these tests are exercising (`test_engine.py` owns that).
DURATION = 90.0

DECODE = {"segments": 12, "gap": 0.01, "seg_seconds": 7.5,
          "texts": [ZH] * 6 + [EN] * 6,
          "slot_langs": ["zh", "zh", "en"]}


def fake_weights(d):
    """A weights folder the preflight accepts: every file it requires, by name.

    The stub engine never opens them. An empty folder used to be enough, which is
    exactly the hole a real snapshot without its config.json fell through.
    """
    from ltm.engine import runner
    d.mkdir(parents=True, exist_ok=True)
    for alts in runner.REQUIRED_MODEL_FILES:
        (d / alts[0]).write_text("{}", encoding="utf-8")
    return d
