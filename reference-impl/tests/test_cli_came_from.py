"""PSM card P-3: the CLI face of `came_from`.

The prose face prints each entry; the JSON face carries only the count, because
an agent-facing response holds no free text (INV-12). Rung 0: examples.
"""
from __future__ import annotations

import json

from ltm.ui import cli

URL = "https://www.youtube.com/watch?v=demoVideo01"


def _audio(tmp_path):
    p = tmp_path / "talk.m4a"
    p.write_bytes(b"not audio")
    return p


def test_prose_names_the_source(tmp_path, capsys):
    root = str(tmp_path / "TranscriptDesk")
    code = cli.main(["--root", root, "intake", str(_audio(tmp_path)),
                     "--from-url", URL, "--from-note", "downloader 2.2.0"])
    out = capsys.readouterr().out
    assert code == 0
    assert f"source: {URL}  downloader 2.2.0" in out


def test_json_carries_the_count_and_not_the_url(tmp_path, capsys):
    root = str(tmp_path / "TranscriptDesk")
    code = cli.main(["--root", root, "--json", "intake", str(_audio(tmp_path)),
                     "--from-url", URL])
    out = capsys.readouterr().out
    assert code == 0
    assert json.loads(out)["came_from_count"] == 1.0
    assert URL not in out


def test_a_bad_url_is_a_refusal_not_a_crash(tmp_path, capsys):
    root = str(tmp_path / "TranscriptDesk")
    code = cli.main(["--root", root, "--json", "intake", str(_audio(tmp_path)),
                     "--from-url", "file:///C:/x.m4a"])
    out = json.loads(capsys.readouterr().out)
    assert code != 0
    assert (out["code"], out["subject"]) == ("bad-argument", "from-url")


def test_negative_control_no_source_line_without_a_source(tmp_path, capsys):
    root = str(tmp_path / "TranscriptDesk")
    cli.main(["--root", root, "intake", str(_audio(tmp_path))])
    assert "source:" not in capsys.readouterr().out
