"""Types and pure functions. No I/O, no subprocesses, no network, no engine.

`core` may import the standard library and `core` itself, and nothing else.
That is an ALLOWLIST, not a list of banned names (INV-15, PSM section 3): a ban
on `advisor` is green forever in milestone 1, while an allowlist puts anything
new on the forbidden side by default.
"""
