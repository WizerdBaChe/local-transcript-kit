"""Build src/ltm/detect/data/simplified_variants.tsv from OpenCC's STCharacters.txt.

`glossary` reads the TSV to tell a lexicon term written in simplified characters
(验收 for 驗收) from a term written as another word (价单 for 假單). The product
never imports OpenCC and never converts the displayed text: the table is used
only inside a comparison.

One row per SIMPLIFIED-ONLY character: a character OpenCC maps to traditional
forms that do not include the character itself. 验 -> 驗 and 发 -> 發,髮 are rows;
系 (-> 系,係,繫) and 后 (-> 后,後) are not, because they are also correct
traditional characters, so a transcript that writes them where the term has 係 or
後 may be a real wrong word and must still be flagged. A traditional character
(發) never stands in for another (髮): it has no row.

Usage:
    python -X utf8 tools/build_simplified_variants.py <STCharacters.txt> <TWVariants.txt>

Source as built on 2026-11-23: OpenCC dictionaries STCharacters.txt and
TWVariants.txt (Apache-2.0, OpenCC by BYVoid) as shipped in opencc-python-reimplemented 0.1.7,
<WORKSPACE>/voice-clone-lab/envs/evalkit/Lib/site-packages/opencc/dictionary/.
The notice ships beside the table (data/NOTICE-opencc.txt).
"""
from __future__ import annotations

import pathlib
import sys

OUT = (pathlib.Path(__file__).resolve().parents[1] / "src" / "ltm" / "detect"
       / "data" / "simplified_variants.tsv")


def _pairs(path: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        key, _, rest = line.partition("\t")
        vals = [t for t in rest.split() if t]
        if len(key) == 1 and vals and all(len(t) == 1 for t in vals):
            out[key] = vals
    return out


def main(st: str, tw: str) -> int:
    # TWVariants adds the Taiwan form of a traditional character OpenCC writes in
    # its own standard: 启 -> 啓 there, and a Taiwan lexicon writes 啟.
    tw_of = _pairs(tw)
    rows = []
    for simp, trad in _pairs(st).items():
        if simp in trad:
            continue
        forms = [f for t in trad for f in (t, *tw_of.get(t, ()))]
        rows.append(f"{simp}\t{','.join(dict.fromkeys(forms))}")
    rows.sort()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(rows)} simplified-only characters -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
