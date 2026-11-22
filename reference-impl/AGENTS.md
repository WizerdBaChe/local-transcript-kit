# AGENTS.md — a pointer, not a copy

The machine-facing contract for this product lives at:

    src/ltm/AGENTS.md

It moved there on 2026-09-16 (PSM card CLI-1) and this file holds no contract
text of its own. The reason is the whole of the card: an INSTALLED `ltm` has no
repository to read, so a guide that lives only at the repository root cannot be
printed by the installed command — and a second copy made at build time is the
defect this project has been blocked for twice. One copy, shipped with the
package, reachable from both sides:

```
ltm agent guide              # prints the packaged file
ltm agent guide --path       # prints where it is
python -m ltm agent guide    # the same, from a checkout
```

`tests/test_agents_contract.py` reads the PACKAGED file through
`importlib.resources` and asserts every member of `EngineFailure.CODES`,
`ErrorResponse.CODES`, `progress.PHASE_UNITS` and `AGENT_VERBS` is named in it —
so what an installed `ltm` prints is checked against the constants that ship
beside it, which is the half a repo-root check could never cover.

`README.md` is the human's entry point and is deliberately not a substitute for
the contract.
