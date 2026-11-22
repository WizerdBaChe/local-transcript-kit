"""Machine acceptance controls for local-transcript-kit (docs/BUILD-SPEC.md section 7).

Usage (from the repo root):  python -X utf8 verify/run_controls.py <new-scratch-dir>

<new-scratch-dir> must not exist or must be empty; reference-impl/ is copied there, mutated and left
for inspection. HOME / USERPROFILE / LTM_ROOT point into the scratch dir, so nothing here reads or writes
your own transcript folder. Offline: no network call, no speech model is loaded (the engine is never
started), no LLM is called. Needs: Python 3.12+ and pytest (verify/requirements.txt).

Controls (evidence rung 0: examples; the two-sided ones print the asserted value for the shipped input AND
for a defective one):
  K1  the shipped test suite passes; prints passed / failed / skipped and the modules not collected
  K2  layering (no network import in the product): failures on the shipped tree vs a tree with one planted
      `import urllib.request` (two-sided)
  K3  the mfp -> ltm recipe: a post with no captions reaches `ltm intake` and records its URL; a post with
      the uploader's captions stops at step 2 (two-sided: recorded rows 1 vs 0)
  K4  an agent-facing response carries no free text: the URL appears in the prose face, not in `--json`
      (two-sided)
  K5  output root: `LTM_ROOT` wins; unset, the root is <home>/TranscriptDesk (two values printed)
  K6  the script rules on the shipped demo recording: glossary findings with the simplified-spelling rule vs
      with it switched off (two-sided); `script-switch` runs for each expected script (two values printed)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if len(sys.argv) != 2 or sys.argv[1].startswith("-"):
    sys.exit(__doc__)
work = Path(sys.argv[1]).resolve()
if work.exists() and any(work.iterdir()):
    sys.exit(f"refusing: {work} exists and is not empty")
if work == REPO or REPO in work.parents or work in REPO.parents:
    sys.exit(f"refusing: {work} overlaps the repo {REPO}")
work.mkdir(parents=True, exist_ok=True)

RI = work / "reference-impl"
shutil.copytree(REPO / "reference-impl", RI, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
HOME = work / "home"
HOME.mkdir()
BASE = {k: v for k, v in os.environ.items() if k not in ("LTM_ROOT", "PYTHONPATH")}
ENV = dict(BASE, PYTHONPATH=str(RI / "src"), PYTHONIOENCODING="utf-8", HOME=str(HOME), USERPROFILE=str(HOME),
           LTM_ROOT=str(work / "desk"), CLAUDE_CONFIG_DIR=str(HOME / "claude"),
           LTM_CLIP60=str(work / "no-such-clip.aac"))
URL = "https://www.youtube.com/watch?v=demo-Vid-02"
results: list[tuple[str, bool]] = []


def run(cmd: list[str], cwd: Path = RI, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, env=env or ENV, capture_output=True, text=True, encoding="utf-8")


def record(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}", flush=True)


def pytest_counts(out: str) -> dict[str, int]:
    tail = out.strip().splitlines()[-1] if out.strip() else ""
    return {k: int(n) for n, k in re.findall(r"(\d+) (passed|failed|skipped|errors?)", tail)}


try:
    import pytest
    print(f"ENV  python {sys.version.split()[0]}, pytest {pytest.__version__}", flush=True)
except ImportError:
    sys.exit("needs pytest: python -m pip install -r verify/requirements.txt")

# K1 -------------------------------------------------------------------------------------------------
p = run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"])
c = pytest_counts(p.stdout)
ignored = re.search(r"local-transcript-kit: not collected \(records not shipped\): ([^\n]+)", p.stdout)
record("K1 shipped suite", p.returncode == 0 and c.get("failed", 0) == 0 and c.get("errors", c.get("error", 0)) == 0,
       f"passed {c.get('passed', 0)}, failed {c.get('failed', 0)}, skipped {c.get('skipped', 0)}; not collected: "
       f"{len(ignored.group(1).split(',')) if ignored else 0} module(s)")

# K2 -------------------------------------------------------------------------------------------------
LAYER = ["tests/test_layering.py"]
clean = pytest_counts(run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *LAYER]).stdout)
mut = work / "mutant-layering"
shutil.copytree(RI, mut, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
target = mut / "src" / "ltm" / "store" / "came_from.py"
target.write_text(target.read_text(encoding="utf-8") + "\nimport urllib.request  # planted by K2\n", encoding="utf-8")
bad = pytest_counts(run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *LAYER], cwd=mut,
                        env=dict(ENV, PYTHONPATH=str(mut / "src"))).stdout)
record("K2 layering control", clean.get("failed", 0) == 0 and bad.get("failed", 0) >= 1,
       f"failures shipped={clean.get('failed', 0)} (passed {clean.get('passed', 0)}), "
       f"planted urllib import={bad.get('failed', 0)}")

# K3 -------------------------------------------------------------------------------------------------
FIX = REPO / "verify" / "fixtures"
media = work / "demo-Vid-02.mp4"
media.write_bytes(b"not really media; intake only hashes it")
fetch = work / "fetch_none.json"
fetch.write_text((FIX / "fetch_none.json").read_text(encoding="utf-8").replace("<SCRATCH>", work.as_posix()),
                 encoding="utf-8")
RECIPE = REPO / "verify" / "recipe_mfp_to_ltm.py"


def came_from_rows(root: Path) -> int:
    return sum(len(json.loads(m.read_text(encoding="utf-8")).get("came_from", []))
               for m in (root / "_intake").glob("*.json")) if (root / "_intake").is_dir() else 0


root_a, root_b = work / "recipe-none", work / "recipe-captions"
a = run([sys.executable, "-X", "utf8", str(RECIPE), "--probe", str(FIX / "probe_none.json"), "--fetch", str(fetch),
         "--root", str(root_a), "--ltm-src", str(RI / "src")], cwd=work)
b = run([sys.executable, "-X", "utf8", str(RECIPE), "--probe", str(FIX / "probe_captions.json"),
         "--root", str(root_b), "--ltm-src", str(RI / "src")], cwd=work)
ra, rb = came_from_rows(root_a), came_from_rows(root_b)
record("K3 mfp->ltm recipe", a.returncode == 0 and ra == 1 and b.returncode == 10 and rb == 0,
       f"no captions: exit {a.returncode}, came_from rows {ra}; uploader captions: exit {b.returncode}, rows {rb}")

# K4 -------------------------------------------------------------------------------------------------
root4 = work / "k4"
prose = run([sys.executable, "-m", "ltm", "--root", str(root4), "intake", str(media), "--from-url", URL])
js = run([sys.executable, "-m", "ltm", "--root", str(root4), "--json", "intake", str(media), "--from-url", URL])
try:
    count = json.loads(js.stdout).get("came_from_count")
except json.JSONDecodeError:
    count = None
record("K4 no free text in --json", URL in prose.stdout and URL not in js.stdout and count == 1.0,
       f"URL in prose face={URL in prose.stdout}, URL in --json={URL in js.stdout}, came_from_count={count}")

# K5 -------------------------------------------------------------------------------------------------
probe = [sys.executable, "-c", "from ltm.store import layout; print(layout.default_root())"]
with_var = run(probe).stdout.strip()
without = run(probe, env={k: v for k, v in ENV.items() if k != "LTM_ROOT"}).stdout.strip()
record("K5 output root", Path(with_var) == work / "desk" and Path(without) == HOME / "TranscriptDesk",
       f"LTM_ROOT set -> {Path(with_var).relative_to(work).as_posix()}; unset -> "
       f"{Path(without).relative_to(work).as_posix() if work in Path(without).parents else without}")

# K6 -------------------------------------------------------------------------------------------------
# The script rules on the shipped demo recording (the test modules that pin them are not collected here).
# Glossary: the 15-term lexicon over the 483 cues gives 15 findings, 12 simplified spellings passed over;
# with the one-directional rule switched off in the same code, the 2026-11-22 count (27) comes back.
# script-switch: the same cues give a different number of runs for each expected script.
desk6 = work / "k6-desk"
shutil.copytree(REPO / "docs" / "assets" / "demo" / "desk", desk6)
LEX = REPO / "docs" / "assets" / "demo" / "lexicon-campus-systems.json"
RUN6 = "narration-6c80e41d"
OFF = ("import sys; from ltm.detect import soundalike; soundalike.same_word = lambda w, t: False; "
       "from ltm.ui import cli; sys.exit(cli.main(sys.argv[1:]))")


def detect6(*extra: str, rule_off: bool = False) -> dict:
    head = [sys.executable, "-c", OFF] if rule_off else [sys.executable, "-m", "ltm"]
    p = run([*head, "--json", "--root", str(desk6), "detect", RUN6, "--rev", "r000", *extra])
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {}


g_on = detect6("--lexicon", str(LEX))
g_off = detect6("--lexicon", str(LEX), rule_off=True)
s_hant, s_hans = detect6("--script", "hant"), detect6("--script", "hans")
n_on, n_off = g_on.get("findings", {}).get("glossary"), g_off.get("findings", {}).get("glossary")
passed = g_on.get("passed_over", {}).get("script-variant")
runs_hant, runs_hans = s_hant.get("findings", {}).get("script-switch"), s_hans.get("findings", {}).get("script-switch")
cues = {k: int(v) for k, v in s_hant.get("script_cues", {}).items()}
record("K6 script rules on the demo",
       (n_on, passed, n_off, runs_hant, runs_hans) == (15, 12, 27, 2, 3)
       and cues == {"hant": 68, "hans": 363, "none": 52},
       f"glossary findings {n_on or 0:g} with the rule (passed over {passed or 0:g}), {n_off or 0:g} with it off; "
       f"script-switch runs {runs_hant or 0:g} expecting hant, {runs_hans or 0:g} expecting hans; "
       f"cues by script hant/hans/none = {cues.get('hant')}/{cues.get('hans')}/{cues.get('none')}")

n = sum(ok for _, ok in results)
print(f"SUMMARY: {n}/{len(results)} controls pass")
sys.exit(0 if n == len(results) else 1)
