"""Rebuild every README picture of local-transcript-kit from files this repo ships.

Usage (from the repo root):  python -X utf8 docs/assets/build_assets.py

  hero.png           the review page of the shipped demo run (docs/assets/demo/desk/), served by
                     the shipped product (`python -m ltm.ui.serve --read-only`) and captured with
                     playwright chromium: overall panel plus the first findings, @2x.
  flow.zh-TW.png     the README flow, drawn from FLOW below (each node names the passage it depicts:
  flow.en.png        table in docs/DEVELOPER.md "README 流程圖對照"), with a geometry self-check.

Needs: Python 3.12+, playwright + chromium (`python -m pip install playwright` then
`python -m playwright install chromium`). Offline: the page is served on 127.0.0.1 and reads only
the shipped desk; no speech model, no network.
The demo desk itself is NOT rebuilt here (that needs the recording and the speech engine):
docs/assets/capture_demo_desk.py copied it from a real run once.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ASSETS = Path(__file__).resolve().parent
REPO = ASSETS.parents[1]
DESK = ASSETS / "demo" / "desk"
RUN = "narration-6c80e41d"

# (id, row, col, zh-TW label, English label, main path?)  -- passages: docs/DEVELOPER.md
FLOW = [
    ("url", 0, 0, "貼文網址（選用）", "Post URL (optional)", False),
    ("mfp", 0, 1, "media-fetch-read-kit：\n有沒有上傳者字幕？", "media-fetch-read-kit:\nuploader captions?", False),
    ("subs", 0, 2, "有：直接用字幕\n（不必轉寫）", "Yes: use them\n(no transcription)", False),
    ("rec", 1, 0, "1 本機錄音／影片", "1 A local recording", True),
    ("intake", 1, 1, "2 intake：登記\n（可記來源網址）", "2 intake: register\n(source URL optional)", True),
    ("decode", 1, 2, "3 decode：本機辨識\n（faster-whisper）", "3 decode: local ASR\n(faster-whisper)", True),
    ("detect", 2, 2, "4 detect：重複、解碼器異常、\n詞表音近字", "4 detect: repeats, decoder\nalarms, term sound-alikes", True),
    ("plan", 2, 1, "5 校讀頁：哪幾句可疑、\n為什麼", "5 review page: which\nsentences, and why", True),
    ("decide", 2, 0, "6 你決定：採納＝開新一版\n舊版永遠保留", "6 you decide: accept = new\nrevision, old kept", True),
]
EDGES = [("url", "mfp"), ("mfp", "subs"), ("mfp", "rec"), ("rec", "intake"), ("intake", "decode"),
         ("decode", "detect"), ("detect", "plan"), ("plan", "decide")]
EDGE_LABEL = {("mfp", "rec"): ("沒有：存下影片", "None: save the video")}
W, H, GX, GY, PAD = 300, 92, 90, 70, 30


def _flow_html(lang: int) -> tuple[str, int, int]:
    pos = {n[0]: (PAD + n[2] * (W + GX), PAD + n[1] * (H + GY)) for n in FLOW}
    width = PAD * 2 + 3 * W + 2 * GX
    height = PAD * 2 + 3 * H + 2 * GY
    boxes, lines = [], []
    for nid, _, _, zh, en, main in FLOW:
        x, y = pos[nid]
        text = (zh, en)[lang].replace("\n", "<br>")
        cls = "main" if main else "side"
        boxes.append(f'<div class="box {cls}" style="left:{x}px;top:{y}px">{text}</div>')
    for a, b in EDGES:
        (ax, ay), (bx, by) = pos[a], pos[b]
        if ay == by:   # same row: from the facing side
            x1, x2 = (ax + W, bx) if bx > ax else (ax, bx + W)
            y1 = y2 = ay + H / 2
        else:          # down a row: bottom to top
            x1, x2, y1, y2 = ax + W / 2, bx + W / 2, ay + H, by
        main = a not in ("url", "mfp")
        lines.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" class="{"m" if main else "s"}" '
                     f'marker-end="url(#{"am" if main else "as"})"/>')
        if (a, b) in EDGE_LABEL:
            # beside the start of the line, on the side it moves away from (never on the stroke)
            lines.append(f'<text x="{x1 + 14}" y="{y1 + 26}">{EDGE_LABEL[(a, b)][lang]}</text>')
    svg = (f'<svg width="{width}" height="{height}" style="position:absolute;left:0;top:0">'
           '<defs><marker id="am" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto">'
           '<path d="M0,0 L10,5 L0,10 z" fill="#1f6feb"/></marker>'
           '<marker id="as" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto">'
           '<path d="M0,0 L10,5 L0,10 z" fill="#8c959f"/></marker></defs>' + "".join(lines) + "</svg>")
    css = ("body{margin:0;background:#fff;font-family:'Microsoft JhengHei','Noto Sans TC',sans-serif}"
           ".box{position:absolute;width:%dpx;height:%dpx;box-sizing:border-box;border-radius:10px;display:flex;"
           "align-items:center;justify-content:center;text-align:center;font-size:19px;line-height:1.35;padding:6px}"
           ".main{background:#ddf4ff;border:2px solid #1f6feb;color:#0a3069}"
           ".side{background:#f6f8fa;border:2px dashed #8c959f;color:#424a53}"
           "line.m{stroke:#1f6feb;stroke-width:3}line.s{stroke:#8c959f;stroke-width:2.5;stroke-dasharray:7 5}"
           "text{font-size:16px;fill:#424a53}") % (W, H)
    return f"<html><head><meta charset='utf-8'><style>{css}</style></head><body>{svg}{''.join(boxes)}</body></html>", width, height


def _overlaps(page) -> int:
    """Geometry self-check: number of box pairs whose rectangles intersect (must be 0)."""
    rects = page.eval_on_selector_all(".box", "els => els.map(e => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; })")
    return sum(1 for i, a in enumerate(rects) for b in rects[i + 1:]
               if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3])


def build_flow(pw) -> None:
    browser = pw.chromium.launch()
    for lang, tag in ((0, "zh-TW"), (1, "en")):
        html, w, h = _flow_html(lang)
        page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=2)
        page.set_content(html)
        n = _overlaps(page)
        # known-bad control: the same boxes moved onto one spot must be caught
        bad = page.evaluate("() => { const b = [...document.querySelectorAll('.box')]; const s = b.map(e => e.style.left); "
                            "b.forEach(e => e.style.left = '30px'); return s; }")
        n_bad = _overlaps(page)
        page.evaluate("s => [...document.querySelectorAll('.box')].forEach((e, i) => e.style.left = s[i])", bad)
        if n or not n_bad:
            sys.exit(f"STOP flow.{tag}: overlapping boxes {n} (must be 0); known-bad control found {n_bad} (must be > 0)")
        page.screenshot(path=str(ASSETS / f"flow.{tag}.png"))
        print(f"flow.{tag}.png  {w}x{h} @2x; overlaps {n}, known-bad control {n_bad}")
    browser.close()


def build_hero(pw) -> None:
    env = dict(os.environ, PYTHONPATH=str(REPO / "reference-impl" / "src"), PYTHONIOENCODING="utf-8")
    env.pop("LTM_ROOT", None)
    srv = subprocess.Popen([sys.executable, "-X", "utf8", "-m", "ltm.ui.serve", "--root", str(DESK), "--no-browser",
                            "--read-only"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           encoding="utf-8")
    try:
        assert srv.stdout is not None
        url = ""
        deadline = time.time() + 30
        while time.time() < deadline and not url:
            m = re.search(r"(http://127\.0\.0\.1:\d+)/\?t=(\S+)", srv.stdout.readline())
            url = m and f"{m.group(1)}/run?run={RUN}&t={m.group(2)}" or ""
        if not url:
            sys.exit("STOP hero: the review page did not print its address")
        base = url.split("/run")[0]
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 1150}, device_scale_factor=2)
        page.goto(base + "/?t=" + url.split("&t=")[1])
        page.wait_for_load_state("networkidle")
        page.goto(base + f"/run?run={RUN}")
        page.wait_for_load_state("networkidle")
        found = page.eval_on_selector_all("h2", "els => els.map(e => e.textContent)")
        plans = sorted((DESK / "校讀" / RUN / "_plans").glob("r*.json"))
        want = len(json.loads(plans[-1].read_text(encoding="utf-8"))["findings"])
        if not any(f"{want} finding" in t for t in found):
            sys.exit(f"STOP hero: the run page did not show the {want} findings of the newest shipped plan "
                     f"({plans[-1].name}): {found}")
        page.screenshot(path=str(ASSETS / "hero.png"), clip={"x": 0, "y": 0, "width": 1280, "height": 1150})
        print(f"hero.png  1280x1150 @2x from {RUN} (headings: {found})")
        browser.close()
    finally:
        srv.terminate()


if __name__ == "__main__":
    with sync_playwright() as pw:
        build_flow(pw)
        build_hero(pw)
