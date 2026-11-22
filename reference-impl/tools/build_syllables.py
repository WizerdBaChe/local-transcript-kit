"""Build src/ltm/detect/data/syllables.tsv from pypinyin's pinyin_dict.json.

The product reads the TSV and never imports pypinyin (zero new dependencies). The
table is toneless and lower-case: `glossary` compares how a character SOUNDS, and
a decoder that writes 海枯 for 海哭 or 光線 for 光纖 has already lost the tone.

Usage:
    python -X utf8 tools/build_syllables.py <path to pypinyin/pinyin_dict.json>

Source as built on 2026-10-30: pypinyin 0.55.0 (MIT, Copyright (c) 2016 mozillazg),
<WORKSPACE>/voice-clone-lab/envs/lab/Lib/site-packages/pypinyin/pinyin_dict.json.
The MIT notice ships beside the table (data/NOTICE-pypinyin.txt).
"""
from __future__ import annotations

import json
import pathlib
import sys
import unicodedata

OUT = pathlib.Path(__file__).resolve().parents[1] / "src" / "ltm" / "detect" / "data" / "syllables.tsv"
RANGES = ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF))


def toneless(s: str) -> str:
    s = s.replace("ü", "v").replace("ǖ", "v").replace("ǘ", "v").replace("ǚ", "v").replace("ǜ", "v")
    s = "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))
    return s.lower()


def main(src: str) -> int:
    data = json.loads(pathlib.Path(src).read_text(encoding="utf-8"))
    rows = []
    for key, value in data.items():
        cp = int(key)
        if not any(a <= cp <= b for a, b in RANGES):
            continue
        # pypinyin lists the most common reading FIRST; the order is kept, because
        # `soundalike` compares primary readings only (a rare reading such as
        # 失 yi makes 一些 read like 失諧).
        syl = list(dict.fromkeys(toneless(p) for p in value.split(",") if p))
        if syl:
            rows.append(f"{chr(cp)}\t{','.join(syl)}")
    rows.sort()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(rows)} characters -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
