"""local-transcript-maker: make a machine transcript's uncertainty visible.

Everything runs on one machine. No recording and no transcript text leaves the
box (R1/BR-13), the product never logs in and holds no credentials (R6), and no
module outside `ui` may import a network library (INV-11/INV-18).

Layering, enforced by `tests/test_layering.py`:

    core     stdlib and core itself -- nothing else (allowlist, INV-15)
    engine   stdlib, core
    detect   stdlib, core
    store    stdlib, core
    agentapi stdlib, core, engine, detect, store
    ui       anything under ltm

Build spec: `docs/psm-transcript-tool-2026-09-09.md`.
"""

__all__ = ["__version__"]

__version__ = "0.1.0.dev0"
