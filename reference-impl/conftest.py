"""Put `src` on `sys.path` so the suite runs from a clean checkout.

`python -m pytest -q` with no install at all is deliberate: the product depends
on the standard library only, the tests depend on pytest only, and a suite that
needs a packaging step before it can fail is one more thing between a change and
the red bar. `pyproject.toml` is still there for `pip install -e .`, which is
what makes `python -m ltm` work as a command.
"""
import pathlib
import sys

SRC = pathlib.Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# --- local-transcript-kit: records this copy does not ship -------------------------------------
# These modules check the code against measurements from the owner's own recordings (real
# speech, so not publishable) or against the full build spec. They run in the origin; here they
# are left out of collection and listed, never silently passed. See docs/EVIDENCE.md.
_HERE = pathlib.Path(__file__).resolve().parent
_NEEDS = {
    "docs/evidence/cond_experiment_segments.json": [
        "tests/test_detect.py", "tests/test_detect_candidates.py", "tests/test_detect_signals.py",
        "tests/test_langmap.py", "tests/test_progress.py", "tests/test_signal_spec_agreement.py",
        "tests/test_glossary_review.py", "tests/test_script_switch.py",
        "tests/test_layering.py::test_the_evidence_scan_has_something_to_scan_and_both_controls_hold"],
    "docs/evidence/glossary_controls_2026-10-30.json": ["tests/test_glossary.py"],
    "docs/psm-transcript-tool-2026-09-09.md": ["tests/test_states.py", "tests/test_invariant_map.py"],
    "docs/evidence/README.md": ["tests/test_evidence_index.py"],
}
_ABSENT = [(m, need) for need, mods in _NEEDS.items() if not (_HERE / need).is_file() for m in mods]
# a whole module reads its record at import, so it cannot be collected; a single test is skipped
collect_ignore = [m for m, _ in _ABSENT if "::" not in m]
_SKIP = {m: need for m, need in _ABSENT if "::" in m}


def pytest_collection_modifyitems(config, items):
    import pytest
    for item in items:
        need = _SKIP.get(item.nodeid)
        if need:
            item.add_marker(pytest.mark.skip(reason=f"needs {need}, not shipped in local-transcript-kit"))


def pytest_report_header(config):
    if _ABSENT:
        return [f"local-transcript-kit: {len(collect_ignore)} module(s) not collected and "
                f"{len(_SKIP)} test(s) skipped (their records are not shipped; docs/EVIDENCE.md)"]


def pytest_terminal_summary(terminalreporter):
    if collect_ignore:
        terminalreporter.write_line(
            "local-transcript-kit: not collected (records not shipped): "
            + ", ".join(pathlib.Path(m).name for m in collect_ignore))
