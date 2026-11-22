"""Copy one finished ltm run into docs/assets/demo/desk/ so the README pictures can be rebuilt anywhere.

Usage:  python -X utf8 docs/assets/capture_demo_desk.py <ltm root> <run id>

Run once, on the machine that decoded the recording (decoding needs the speech engine and the
recording itself, neither of which ships). What it copies is the run's own record: the intake
marker, `_origin.json`, the transcript (`全文/`, `時軸/`), `_findings/` and `_plans/`. What it
changes: every absolute path to the recording becomes `recording/<file name>`, so the copy names
no folder of the machine it came from. The engine's stderr log is not copied.
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEST = HERE / "demo" / "desk"
KEEP_DIRS = ("全文", "時軸", "_findings", "_plans")
ABS_PATH = re.compile(r'[A-Za-z]:(?:\\\\|\\|/)(?:[^"\\/]+(?:\\\\|\\|/))*([^"\\/]+\.(?:wav|m4a|mp3|aac|mp4|flac|opus))')


def scrub(text: str) -> str:
    return ABS_PATH.sub(lambda m: f"recording/{m.group(1)}", text)


def main(root: Path, run: str) -> None:
    src = root / "校讀" / run
    marker = root / "_intake" / f"{run}.json"
    if not src.is_dir() or not marker.is_file():
        sys.exit(f"no run {run} under {root}")
    if DEST.exists():
        shutil.rmtree(DEST)
    files = [marker, src / "_origin.json"] + [f for d in KEEP_DIRS for f in sorted((src / d).glob("*")) if f.is_file()]
    for f in files:
        rel = f.relative_to(root)
        out = DEST / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(scrub(f.read_text(encoding="utf-8")), encoding="utf-8", newline="")
    left = [p for p in DEST.rglob("*") if p.is_file() and re.search(r"(?<![A-Za-z])[A-Za-z]:[\\/]", p.read_text(encoding="utf-8"))]
    print(f"copied {len(files)} files into {DEST}; files still naming a drive path: {len(left)}")
    if left:
        sys.exit(f"STOP: {[str(p.relative_to(DEST)) for p in left]}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(Path(sys.argv[1]), sys.argv[2])
