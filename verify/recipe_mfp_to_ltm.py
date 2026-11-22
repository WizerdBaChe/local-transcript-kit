"""The mfp -> ltm recipe, written out as a program (docs/BUILD-SPEC.md section 5).

Not part of the product. It is what an agent does by hand when a user starts from a post URL:
neither tool reads the other's folders, so the agent carries the facts between them.

Usage:
  python -X utf8 verify/recipe_mfp_to_ltm.py --probe probe.json [--fetch fetch.json]
                                              [--root <ltm root>] [--ltm-src <path to src>]

  --probe  the stdout of `mfp probe <url> --write-subs --json`
  --fetch  the stdout of `mfp fetch <url> --json` (needed only when step 2 says to go on)

Steps (the same numbers as AGENTS.md "Starting from a post URL"):
  1. read the probe: which sidecars does each video item carry?
  2. `captions` -> the uploader's own text exists: stop, fetch it with mfp `--write-subs`.
     `auto-captions` -> the platform's machine transcript: stop and ask the user which to use.
     none (or `captionsUnconfirmed`) -> go on.
  3. read the fetch result: the saved path of each video item, and the post URL.
  4. `ltm intake <path> --from-url <post url> --from-note "mfp <postId>"`.
  5. (the agent then runs ltm decode / plan and reports; not run here.)

Exit codes: 0 = intake done (prints the run line); 10 = stopped at step 2 with captions;
11 = stopped at step 2 with auto-captions; 2 = the inputs do not have the expected shape.
Reads only the fields pinned in docs/BUILD-SPEC.md section 5 (mfp FetchResult schemaVersion 1).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

STOP_CAPTIONS, STOP_AUTO, BAD_SHAPE = 10, 11, 2
VIDEO_EXT = (".mp4", ".mkv", ".webm", ".mov", ".m4a", ".mp3", ".aac", ".wav", ".opus", ".flac")


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def step2(probe: dict) -> tuple[str, list[int]]:
    """('captions' | 'auto-captions' | 'none', video item indices) for the first post."""
    if probe.get("schemaVersion") != 1 or not probe.get("results"):
        raise ValueError("probe: expected schemaVersion 1 with results[]")
    manifest = probe["results"][0].get("manifest") or {}
    videos = [i for i in manifest.get("items", []) if i.get("kind") == "video"]
    kinds = {s.get("kind") for i in videos for s in i.get("sidecars", [])}
    indices = [i["index"] for i in videos]
    if manifest.get("captionsUnconfirmed"):
        return "none", indices
    if "captions" in kinds:
        return "captions", indices
    if "auto-captions" in kinds:
        return "auto-captions", indices
    return "none", indices


def step3(fetch: dict, indices: list[int]) -> tuple[str, str, str]:
    """(saved path, post url, post id) of the first saved video item."""
    if fetch.get("schemaVersion") != 1 or not fetch.get("posts"):
        raise ValueError("fetch: expected schemaVersion 1 with posts[]")
    post = fetch["posts"][0]
    for item in fetch.get("items", []):
        path = item.get("path") or ""
        if item.get("status") == "ok" and (item.get("index") in indices or path.lower().endswith(VIDEO_EXT)):
            return path, post["url"], post.get("postId") or ""
    raise ValueError("fetch: no saved video item (status ok with a path)")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="The mfp -> ltm recipe, written out as a program.")
    ap.add_argument("--probe", required=True)
    ap.add_argument("--fetch")
    ap.add_argument("--root")
    ap.add_argument("--ltm-src", help="run `python -m ltm` with this src on PYTHONPATH")
    a = ap.parse_args(argv)
    try:
        verdict, indices = step2(_load(a.probe))
        print(f"step 2: video sidecars -> {verdict}")
        if verdict == "captions":
            print("stop: the uploader's captions are the text; fetch them with `mfp fetch <url> --write-subs`")
            return STOP_CAPTIONS
        if verdict == "auto-captions":
            print("stop: the platform has a machine transcript; ask the user whether to use it or run ltm")
            return STOP_AUTO
        if not a.fetch:
            print("step 3 needs --fetch (the stdout of `mfp fetch <url> --json`)")
            return BAD_SHAPE
        path, url, post_id = step3(_load(a.fetch), indices)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
        print(f"inputs do not have the expected shape: {e}")
        return BAD_SHAPE
    print(f"step 3: saved {path}")
    cmd = [sys.executable, "-m", "ltm"] + (["--root", a.root] if a.root else []) + [
        "intake", path, "--from-url", url, "--from-note", f"mfp {post_id}".strip()]
    env = dict(os.environ)
    if a.ltm_src:
        env["PYTHONPATH"] = a.ltm_src
    print("step 4: " + " ".join(cmd[1:]))
    done = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8")
    sys.stdout.write(done.stdout)
    sys.stderr.write(done.stderr)
    return done.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
