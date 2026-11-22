"""The double-click entry: what it decides, and what it hands to the server.

`ltm.ui.launch` exists to make ONE decision -- what `--audio-root` should be when
nobody typed one -- and every test here is about that decision or about the seam
where it reaches `serve`. The `.cmd` file beside it is deliberately ASCII
plumbing with no decisions in it, because a `.cmd` is the one thing in this
repository pytest cannot reach.
"""
from __future__ import annotations

import pathlib
import tempfile

import pytest

from ltm.store import layout
from ltm.ui import launch, serve


def root_with(*names: str) -> pathlib.Path:
    root = pathlib.Path(tempfile.mkdtemp())
    for n in names:
        (root / n).mkdir(parents=True, exist_ok=True)
    return root


def test_nothing_handed_in_falls_back_to_the_drop_folder():
    root = root_with()
    roots, notes = launch.resolve([], root)
    assert roots == [layout.dropbox(root)]
    assert roots[0].is_dir(), "the fallback is created at the moment it is used"
    assert notes == []


def test_the_drop_folder_is_not_the_store_s_own_ledger():
    """`_intake` holds POINTERS the product wrote; this holds audio a person put
    there. Handing the page the ledger folder would offer it a list of JSON files
    and no recordings -- the failure would be silent and would look like an empty
    disk."""
    root = root_with()
    roots, _ = launch.resolve([], root)
    assert roots[0] != layout.intake_dir(root)
    assert roots[0].parent == root


def test_a_folder_handed_in_is_used_and_nothing_is_created():
    """The negative half of the fallback test: a launcher that always made its
    drop folder would pass the test above while quietly littering every machine
    that never uses it."""
    root = root_with()
    given = root_with("recordings") / "recordings"
    roots, notes = launch.resolve([str(given)], root)
    assert roots == [given]
    assert notes == [], "a folder that was handed in needs no explanation"
    assert not layout.dropbox(root).exists()


def test_a_file_handed_in_widens_to_its_folder_and_says_so():
    """Explorer makes dropping a file at least as easy as dropping a folder, and
    `--audio-root` takes folders. The widening happens either way; the only
    question is whether the person is told which folder the page can now see."""
    root = root_with()
    folder = root_with("talks") / "talks"
    clip = folder / "a.m4a"
    clip.write_bytes(b"not really audio")
    roots, notes = launch.resolve([str(clip)], root)
    assert roots == [folder]
    assert len(notes) == 1
    assert clip.name in notes[0] and str(folder) in notes[0]


def test_a_path_that_is_gone_is_named_and_skipped():
    root = root_with()
    good = root_with("kept") / "kept"
    missing = good / "was-here"
    roots, notes = launch.resolve([str(missing), str(good)], root)
    assert roots == [good]
    assert any("was-here" in n for n in notes)


def test_every_path_being_gone_falls_back_and_says_that_too():
    """The dead end this module exists to prevent: a person who dropped the wrong
    thing must not get a page with an empty picker and no sentence."""
    root = root_with()
    missing = root / "nowhere-at-all"
    roots, notes = launch.resolve([str(missing)], root)
    assert roots == [layout.dropbox(root)]
    assert any("nothing handed in could be used" in n for n in notes)


def test_the_same_folder_twice_is_one_folder():
    root = root_with()
    a = root_with("one") / "one"
    b = root_with("two") / "two"
    roots, _ = launch.resolve([str(a), str(b), str(a)], root)
    assert roots == [a, b], "collapsed, and in the order they arrived"


def test_a_folder_named_with_a_dash_is_a_folder_not_a_flag():
    """There is no flag pass-through, on purpose: every argument is a path. A
    folder whose name begins with a dash is the case that would break a launcher
    that guessed."""
    root = root_with()
    odd = root_with("-recordings") / "-recordings"
    roots, notes = launch.resolve([str(odd)], root)
    assert roots == [odd]
    assert notes == []


# --- the seam: what launch hands to serve --------------------------------

class _Stub:
    """Enough of `Launched` for `serve.main` to finish without binding a port."""

    def __init__(self, audio_roots, decodes=True):
        self.audio_roots = tuple(audio_roots)
        self.decodes = decodes
        self.url = "http://127.0.0.1:0/?t=stub"
        self.stopped = False

    def serve_forever(self) -> None:
        return None

    def stop(self) -> None:
        self.stopped = True


@pytest.fixture
def captured(monkeypatch):
    seen: list[_Stub] = []

    def fake_start(ctx=None, *, port=0, decodes=True, audio_roots=()):
        seen.append(_Stub(audio_roots, decodes))
        return seen[-1]

    monkeypatch.setattr(serve, "start", fake_start)
    monkeypatch.setattr(serve.webbrowser, "open", lambda url: True)
    return seen


def test_the_folders_launch_chose_are_the_folders_serve_was_given(captured):
    """**The seam, tested as a comparison and not as a second description.** What
    `resolve` returned is compared against what `serve`'s own parser produced
    after `launch` wrote it into argv -- so a flag renamed on either side fails
    here rather than at a person's double-click."""
    root = root_with()
    a = root_with("first") / "first"
    b = root_with("second") / "second"
    chosen, _ = launch.resolve([str(a), str(b)], root)

    assert launch.main([str(a), str(b)], root=root) == 0
    assert len(captured) == 1
    assert list(captured[0].audio_roots) == chosen


def test_the_fallback_reaches_serve_too(captured):
    root = root_with()
    assert launch.main([], root=root) == 0
    assert list(captured[0].audio_roots) == [layout.dropbox(root)]


def test_the_page_is_launched_able_to_start_decodes(captured):
    """`decodes=False` is the read-only face. A launcher that handed that to a
    person who double-clicked an icon would give them a page whose buttons all
    refuse, which is the one outcome the user's ruling on this entry ruled out."""
    root = root_with()
    assert launch.main([], root=root) == 0
    assert captured[0].decodes is True


# --- the .cmd, as far as a test can reach it -----------------------------

CMD = pathlib.Path(__file__).resolve().parents[1] / "開啟校讀頁.cmd"


def test_the_launcher_script_is_ascii_and_crlf():
    """Both properties are load-bearing and both fail silently.

    cmd.exe reads a batch file's bytes through the console's code page, so a
    non-ASCII literal is mangled on a machine whose code page differs from the
    one it was written on -- and it mis-parses multi-line parenthesised blocks in
    an LF-only file, which is the shape of the interpreter search inside it.
    """
    raw = CMD.read_bytes()
    assert max(raw) < 128, "a non-ASCII byte in a .cmd is a code-page bug"
    body = raw.replace(b"\r\n", b"")
    assert b"\n" not in body, "a bare LF in a .cmd is a parser bug"


def test_the_launcher_script_runs_the_module_these_tests_cover():
    """The one link between the untestable half and the tested half."""
    text = CMD.read_text(encoding="ascii")
    assert "-m ltm.ui.launch" in text
    assert "ltm.ui.serve" not in text, (
        "the .cmd must go through launch, which is where the decision lives")
