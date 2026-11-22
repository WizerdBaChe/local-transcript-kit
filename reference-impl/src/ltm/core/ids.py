"""Identity: the content hash, and a filesystem-safe stem.

PSM section 2 lists exactly two names here -- `content_hash` and `run_stem` --
and that is the whole module on purpose. The `<stem>-<hash8>` composition, the
serial format and every other naming convention belong to `store.layout`, which
owns the disk vocabulary; `core` must not know the layout, or the layering gate
is measuring nothing.

The hash is of the recording's CONTENT, not its path (PSM section 7), so the
same talk copied elsewhere resolves to the same run and two different recordings
that happen to share a filename do not collide.
"""
from __future__ import annotations

import hashlib
import pathlib
import re
import unicodedata

CHUNK = 1 << 20

#: Characters Windows forbids in a path component, plus the separators. The stem
#: ends up on disk, so this is a correctness rule, not a style one.
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def content_hash(path: str | pathlib.Path) -> str:
    """Full-file SHA-256, streamed. Never a sample of the first N bytes.

    A prefix hash makes two recordings with the same leading silence one run,
    and the failure is invisible: the second decode lands in the first one's
    folder and its serial chain continues someone else's.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def run_stem(filename: str) -> str:
    """A filesystem-safe stem, without inventing a new name for the file.

    NFC first: Windows hands back composed forms while macOS-authored filenames
    arrive decomposed, and two spellings of one name would otherwise hash to one
    run but write to two folders.
    """
    stem = pathlib.PurePath(filename).stem
    stem = unicodedata.normalize("NFC", stem)
    stem = _FORBIDDEN.sub("_", stem).strip(" .")
    # A stem that reduces to nothing still has to be a directory name; the hash
    # suffix keeps it unique, so a constant placeholder is safe here.
    return stem or "untitled"
