"""The engine: a subprocess boundary, on purpose.

`faster_whisper` is never imported by product code. It is reached by running
`sidecar.py` under the ASR interpreter (§1, R9), and that is what makes INV-18
checkable by an import graph rather than by a promise: the product's own graph
contains no HTTP client because it contains no model runtime at all.

The boundary buys three other things, each of which was a real problem upstream:
a model load that cannot be cancelled cannot hang the product; a crash in native
code (ctranslate2, CUDA) takes down a child and not the CLI; and the interpreter
that owns the weights can be replaced without touching the product's own
environment.
"""
