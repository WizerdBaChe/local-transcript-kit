"""The allowlist the page validates against. PSM section 19, card GUI-3.

This is the security surface of the increment, so the tests are about the
property rather than about the code: an offered path reaches the product only if
it is one the catalogue itself listed. Every traversal case below is there to
show the check does not depend on recognising a spelling -- it is a membership
test, and `..`, a UNC share and a symlink are all refused by the same line.

The positive control is the one that keeps this honest: a path INSIDE the roots
must be accepted. A `resolve` that raised on everything would pass every refusal
test in this file and make the page unable to decode anything.
"""
from __future__ import annotations

import json
import os
import pathlib

import pytest

from ltm.store import layout
from ltm.ui import sources


def audio(directory: pathlib.Path, name: str = "talk.m4a",
          size: int = 2048) -> pathlib.Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    p.write_bytes(b"\0" * size)
    return p


def registered(root: pathlib.Path, source: pathlib.Path,
               run: str = "talk-0badc0de") -> pathlib.Path:
    """An `_intake/<run>.json` pointing at a real file, as `intake` writes it."""
    marker = layout.intake_dir(root) / f"{run}.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(
        {"schema": "ltm/origin/2", "run": run, "source": str(source.resolve()),
         "sha256": "0" * 64, "bytes": source.stat().st_size}),
        encoding="utf-8")
    return marker


# --------------------------------------------------------------------------
# what is offered
# --------------------------------------------------------------------------
def test_with_no_audio_root_only_the_store_is_offered(tmp_path):
    """The conservative default: the page cannot reach into the filesystem.

    "The page can start decodes" and "the page can see your disk" are two
    different permissions, and a server launched with no flags has only the
    first.
    """
    root = tmp_path / "store"
    kept = audio(tmp_path / "media")
    loose = audio(tmp_path / "elsewhere", "not-offered.m4a")
    registered(root, kept)

    listing = sources.Catalogue(root).listing()
    paths = [row["path"] for row in listing["sources"]]
    assert paths == [str(kept.resolve())]
    assert str(loose.resolve()) not in paths
    assert listing["note"], "an empty-ish list must say which cause it has"


def test_an_audio_root_is_walked_and_says_it_was_named(tmp_path):
    root = tmp_path / "store"
    media = tmp_path / "media"
    one = audio(media, "a.m4a")
    two = audio(media / "sub", "b.wav")
    audio(media, "notes.txt.bak")                  # not an audio suffix

    listing = sources.Catalogue(root, [media]).listing()
    names = sorted(row["name"] for row in listing["sources"])
    assert names == ["a.m4a", "b.wav"]
    assert listing["note"] is None
    assert listing["audioRoots"] == [str(media)]
    assert {row["origin"] for row in listing["sources"]} == {"audio-root"}
    assert all(row["bytes"] == 2048 for row in listing["sources"])


def test_a_file_in_both_populations_appears_once_as_the_store_row(tmp_path):
    """The store row carries the run id, which is the more useful of the two."""
    root = tmp_path / "store"
    media = tmp_path / "media"
    p = audio(media)
    registered(root, p, run="talk-0badc0de")

    rows = sources.Catalogue(root, [media]).listing()["sources"]
    assert len(rows) == 1
    assert rows[0]["origin"] == "store" and rows[0]["run"] == "talk-0badc0de"


def test_a_registered_file_that_has_moved_is_dropped(tmp_path):
    """The media is not copied (R10), so a recording can go away between runs.

    Offering a path the product cannot open would produce a decode that fails on
    the reader's behalf, after they waited for it to start.
    """
    root = tmp_path / "store"
    p = audio(tmp_path / "media")
    registered(root, p)
    p.unlink()

    assert sources.Catalogue(root).listing()["sources"] == []


def test_the_walk_is_bounded(tmp_path):
    """A root aimed at a drive letter must not turn a page load into a scan."""
    root = tmp_path / "store"
    media = tmp_path / "media"
    shallow = audio(media / "a" / "b", "near.m4a")
    deep = audio(media / "a" / "b" / "c" / "d", "far.m4a")

    rows = sources.Catalogue(root, [media]).listing()["sources"]
    paths = {row["path"] for row in rows}
    assert str(shallow.resolve()) in paths
    assert str(deep.resolve()) not in paths, (
        f"the walk went deeper than {sources.MAX_DEPTH}")


# --------------------------------------------------------------------------
# the allowlist
# --------------------------------------------------------------------------
def test_a_listed_path_is_accepted(tmp_path):
    """The positive control, and the reason every refusal below means something.

    A `resolve` that raised on everything would pass all the refusal tests and
    leave the page unable to decode anything at all.
    """
    root = tmp_path / "store"
    media = tmp_path / "media"
    p = audio(media)
    cat = sources.Catalogue(root, [media])

    offered = cat.listing()["sources"][0]["path"]
    assert cat.resolve(offered) == p.resolve()


def test_the_same_file_spelled_differently_is_still_accepted(tmp_path):
    """The allowlist resolves before it compares, so spellings collapse.

    Refusing a correct path is the other failure mode, and the one that sends a
    reader looking for a bug in the product. On Windows the case of the drive
    letter and of the name are both ordinary variation.
    """
    root = tmp_path / "store"
    media = tmp_path / "media"
    p = audio(media)
    cat = sources.Catalogue(root, [media])

    roundabout = media / "sub" / ".." / p.name
    assert cat.resolve(str(roundabout)) == p.resolve()
    if os.name == "nt":
        assert cat.resolve(str(p).upper()) == p.resolve()


@pytest.mark.parametrize("spelling", [
    "{outside}",
    "{media}/../outside/secret.m4a",
    "{media}/./../outside/secret.m4a",
    "//server/share/secret.m4a",
    r"\\server\share\secret.m4a",
    "C:/Windows/System32/config/SAM",
    "/etc/passwd",
    "",
    "   ",
])
def test_a_path_that_was_never_offered_is_refused(spelling, tmp_path):
    """One code, and not one line of string inspection anywhere behind it.

    `..` in the middle, a UNC share, an absolute system path, an empty string:
    the catalogue refuses them all with the same membership test, which is
    exactly the property. A check that recognised spellings would need a case
    for each of these and would lose to the next one.
    """
    root = tmp_path / "store"
    media = tmp_path / "media"
    audio(media)
    outside = audio(tmp_path / "outside", "secret.m4a")
    cat = sources.Catalogue(root, [media])

    offered = spelling.format(outside=outside, media=media)
    with pytest.raises(sources.NotOffered):
        cat.resolve(offered)


def _link(target: pathlib.Path, at: pathlib.Path) -> str:
    """A symlink if this platform allows one, otherwise a hard link.

    **Never a skip.** `AGENTS.md` section 10 says this suite runs green with zero
    skips, and A-26 is the ruling behind it: a skip is honest and is not a
    measurement, so a control that quietly stopped running on the platform this
    product actually ships on would be worse than no control. Windows refuses an
    unprivileged symlink and allows a hard link, so the test asserts whichever
    kind it got -- they have genuinely different semantics and both are worth
    stating.
    """
    try:
        at.symlink_to(target)
        return "symlink"
    except (OSError, NotImplementedError):
        os.link(target, at)
        return "hardlink"


def test_a_link_planted_in_a_root_is_resolved_not_trusted(tmp_path):
    """What a link in an audio root means, stated for each kind.

    A SYMLINK resolves to its target, so the listing names the real file and the
    target is what the allowlist holds. A HARD LINK is not a pointer at all --
    it is a second name for the same bytes, indistinguishable from the first --
    so it is listed under its own path, inside the root, and the target outside
    is still not offered.

    Either way the rule is the same one line: resolve, then look up. The reason
    to write it down is that the two READ differently to a person, and somebody
    will one day assume the hard-link case laundered a path when it did not.
    """
    root = tmp_path / "store"
    media = tmp_path / "media"
    media.mkdir(parents=True, exist_ok=True)
    outside = audio(tmp_path / "outside", "secret.m4a")
    kind = _link(outside, media / "link.m4a")

    cat = sources.Catalogue(root, [media])
    listed = [row["path"] for row in cat.listing()["sources"]]

    if kind == "symlink":
        assert listed == [str(outside.resolve())], (
            "the listing should name the real file, not the link")
        # and that IS an offer: the link was placed inside a root the launcher
        # named, which is the launcher saying these files are reachable
        assert cat.resolve(str(outside)) == outside.resolve()
    else:
        assert listed == [str((media / "link.m4a").resolve())]
        # the OTHER name for the same bytes was never offered, and is refused
        with pytest.raises(sources.NotOffered):
            cat.resolve(str(outside))


def test_the_refusal_carries_a_code_and_not_a_diagnosis(tmp_path):
    """The page sends back what it was given; anything else is not explainable.

    A refusal that speculated about why would be telling the caller about the
    filesystem, which is the thing being refused.
    """
    cat = sources.Catalogue(tmp_path / "store")
    with pytest.raises(sources.NotOffered) as e:
        cat.resolve(str(tmp_path / "nothing.m4a"))
    assert sources.NotOffered.code == "not-offered"
    assert "why" not in str(e.value).lower()


def test_a_file_that_appears_after_the_listing_is_offerable(tmp_path):
    """Rebuilt per call: a recording dropped in a root works without a restart.

    The negative control for that is the previous test -- rebuilding must not
    mean accepting anything.
    """
    root = tmp_path / "store"
    media = tmp_path / "media"
    media.mkdir(parents=True)
    cat = sources.Catalogue(root, [media])
    assert cat.listing()["sources"] == []

    later = audio(media, "arrived.m4a")
    assert cat.resolve(str(later)) == later.resolve()
