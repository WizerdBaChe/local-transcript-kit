"""`python -m ltm` (PSM §2). One line, and it delegates.

The entry point is `ui.cli:main` and `pyproject.toml` names the same function for
the `ltm` command, so the two ways in are one function rather than two that agree.
"""
from __future__ import annotations

import sys

from .ui.cli import main

if __name__ == "__main__":
    sys.exit(main())
