"""The layering gate. PSM section 3 — INV-15, INV-16, INV-18, and core's shape.

**Rung 1 (exhaustive enumeration).** The domain is finite: every module in the
tree, against a layer table, so the enumeration IS the proof and nothing above
adds truth. What it cannot do is judge a file's CONTENTS — that blind spot is
why the public-name check exists, and why an independent reader still reads the
code.

Both controls ship with every assertion, because a checker never shown a
violation is not known to detect one, and a checker never shown a legal file is
not known to be a checker rather than a `return False`:

    tests/fixtures/layering_bad.py_txt   every check must flag it
    tests/fixtures/prose_ok.md           no check may flag it

The two vocabularies are READ FROM `CLAUDE.md`, never restated here. A copy of a
ruling drifts from the ruling; this project has been blocked twice for exactly
that shape. If either parse comes back empty the test FAILS — a zero-length
vocabulary would make every scan below print "clean" for the wrong reason.
"""
import ast
import pathlib
import re
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO / "src"
RULINGS = REPO / "CLAUDE.md"
FIXTURES = pathlib.Path(__file__).parent / "fixtures"

STDLIB = set(sys.stdlib_module_names)

# The layer table of PSM section 3. `core`'s entry is an ALLOWLIST (INV-15): a
# ban on `advisor` is green forever in milestone 1, and a named ban silently
# permits whatever is not on it, so anything new lands on the forbidden side.
MAY_IMPORT = {
    "core": frozenset({"core"}),
    "engine": frozenset({"core"}),
    "detect": frozenset({"core"}),
    "store": frozenset({"core"}),
    "agentapi": frozenset({"core", "engine", "detect", "store"}),
    "ui": frozenset({"core", "engine", "detect", "store", "agentapi", "ui"}),
}

#: Third-party imports allowed in ONE named file, and the exemption is narrower
#: than the rule it bends. `engine/sidecar.py` is executed by the ASR interpreter
#: as a subprocess and is **never imported** -- that is precisely what makes
#: INV-18 checkable by an import graph, since the product's own graph then contains
#: no model runtime at all. A blanket "engine may import faster_whisper" would give
#: that away, so the allowance is per file and comes with the two assertions that
#: make it safe: nothing may import this file, and this file may import nothing
#: from `ltm` (it re-declares the wire vocabulary on purpose -- a child that cannot
#: start because of an import error cannot report why it did not start).
#:
#: `ctranslate2` joined it on 2026-09-09 (A-22) and the question it had to answer
#: was "is this a second hole or the same one". The same one: it is
#: `faster_whisper`'s own runtime, already resident in that interpreter because
#: the model cannot load without it, imported inside a function in the SAME file
#: the exemption already names, and both properties below still pay for it. It is
#: there to ask which compute types this machine supports -- the alternative was
#: matching the wording of an exception, and a failure code derived from a message
#: is a theory about what went wrong (INV-14).
FILE_ALLOWANCE = {
    "engine/sidecar.py": frozenset({"faster_whisper", "ctranslate2"}),
}


def file_key(path: pathlib.Path) -> str | None:
    try:
        return "/".join(path.relative_to(SRC / "ltm").parts)
    except ValueError:
        return None

# INV-18 / INV-11. Scope (PIM section 3, N-5): "a network request" means one that
# LEAVES this machine, so a loopback call would not violate the ruling behind it
# — but loopback still may not appear in this import graph, and in milestone 1
# the ban is total because the only thing that would ever call a local model is
# `Advisor`, which is absent (R12). Milestone 2 replaces this with TWO
# assertions, not a loosened one.
NETWORK = {
    "socket", "ssl", "http", "httplib", "urllib", "urllib2", "urllib3",
    "ftplib", "telnetlib", "smtplib", "poplib", "imaplib", "xmlrpc",
    "requests", "httpx", "aiohttp", "websockets", "grpc", "paramiko",
}


# --------------------------------------------------------------------------
# the two vocabularies, read from the rulings file
# --------------------------------------------------------------------------
def _bullet(text: str, opening: str) -> str:
    """One bullet of `CLAUDE.md`, including its continuation lines."""
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith(opening))
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].lstrip().startswith("- ") or not lines[i].strip()),
               len(lines))
    return "\n".join(lines[start:end])


def foreign_vocabulary() -> frozenset[str]:
    """mfp's on-disk names (R8). Read from the ruling, not restated."""
    text = RULINGS.read_text(encoding="utf-8")
    bullet = _bullet(text, "- No shipping file may contain mfp's on-disk")
    return frozenset(t.strip("/\\") for t in re.findall(r"`([^`]+)`", bullet))


def _store_table(column: int) -> frozenset[str]:
    """One column of `CLAUDE.md`'s store table: 1 = ours, 2 = the contrast."""
    text = RULINGS.read_text(encoding="utf-8")
    rows = re.findall(
        r"^\|[^|\n]+\|\s*`([^`]+)`\s*\|\s*(?:[^|`\n]*`([^`]+)`)?[^|\n]*\|",
        text, re.M)
    return frozenset(r[column - 1].strip("/\\") for r in rows if r[column - 1])


def own_disk_vocabulary() -> frozenset[str]:
    """This product's own store names, from `CLAUDE.md`'s store table."""
    return _store_table(1)


VOCAB_FOREIGN = foreign_vocabulary()
VOCAB_OWN = own_disk_vocabulary()


def test_the_vocabularies_parsed():
    """A zero-length vocabulary prints the same 'clean' a compliant tree does.

    So the populations are asserted before anything is scanned with them -- the
    same rule the design-time instrument applies to its own extractors.

    **No forbidden token is spelled out here**, deliberately: this file is
    itself a shipping file, so a literal in an assertion would make the scan
    below flag its own test -- the exact mis-scoping R8's first draft died of.
    Instead the ruling is parsed TWICE by different routes (the R8 bullet list
    and the store table's contrast column) and the two must agree on their
    overlap. Two independent extractions of one ruling agreeing is better
    evidence than a hardcoded example anyway.
    """
    contrast = _store_table(2)
    assert len(VOCAB_FOREIGN) >= 9, sorted(VOCAB_FOREIGN)
    assert len(VOCAB_OWN) >= 6, sorted(VOCAB_OWN)
    assert len(contrast) >= 6, sorted(contrast)
    assert len(VOCAB_FOREIGN & contrast) >= 5, (
        "the two parses of one ruling disagree, so one of them read the wrong "
        f"lines: bullet={sorted(VOCAB_FOREIGN)} table={sorted(contrast)}")
    # and ours must not intersect theirs -- that is the whole content of R8
    assert not (VOCAB_FOREIGN & VOCAB_OWN)
    assert not (contrast & VOCAB_OWN)


# --------------------------------------------------------------------------
# extractors
# --------------------------------------------------------------------------
def shipping_python() -> list[pathlib.Path]:
    """Files that EXECUTE or SHIP. `docs/` is exempt and must be (R8).

    The bad fixture is named `.py_txt` so that neither pytest nor this list
    picks it up: a deliberately illegal file inside the scanned tree would make
    the suite red for the wrong reason.
    """
    return sorted(SRC.rglob("*.py")) + sorted(
        p for p in (REPO / "tests").rglob("*.py")) + [REPO / "conftest.py"]


def executing_evidence() -> list[pathlib.Path]:
    """`docs/evidence/*.py` -- files that EXECUTE but do not ship (user ruling).

    R8's scan used to stop at `src/`, `tests/` and `conftest.py`, because R8's
    asset list is "source, tests, configuration, packaging, fixtures" and `docs/`
    is exempt. These scripts fall between: they are under `docs/`, they are not
    shipped, and they RUN -- so one of them can hardcode a path under the other
    product's output root and no gate would say a word. Measured before asking:
    nothing here violated R8, and the single grep hit was a Chinese sentence
    using the ordinary word for "transcript" -- the false positive R8's own text
    warns about.

    **Only the vocabulary check is applied to this population**, and only through
    `path_components`. Layering, the core allowlist and the network ban are rules
    about the product's own module graph; an evidence script is not in it, and
    running those here would be a different ruling than the one that was given.
    """
    return sorted((REPO / "docs" / "evidence").glob("*.py"))


def module_layer(path: pathlib.Path) -> str | None:
    try:
        rel = path.relative_to(SRC / "ltm")
    except ValueError:
        return None
    return rel.parts[0] if len(rel.parts) > 1 else None


def imports_of(source: str) -> set[str]:
    """Top-level module names, plus `ltm.<layer>` for intra-package imports."""
    out: set[str] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:                      # relative: `from .model import`
                out.add("." * node.level + (node.module or ""))
            elif node.module:
                out.add(node.module)
    return out


def string_literals(source: str) -> list[str]:
    return [n.value for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def public_names(source: str) -> set[str]:
    tree = ast.parse(source)
    out = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return {n for n in out if not n.startswith("_")}


def path_components(literal: str) -> set[str]:
    """Split on path separators only.

    NOT free substrings, and this is the load-bearing choice (CLAUDE.md R8):
    `分析` and `字幕檔` are ordinary Chinese words, so a substring scan fires on
    any prose that DISCUSSES the boundary — it did, on the PIM's own section 9.4,
    the first time this check was run. A gate that cries wolf on correct text is
    one readers learn to skip.
    """
    return {c for c in re.split(r"[\\/]+", literal) if c}


# --------------------------------------------------------------------------
# the checks, each a pure function so a fixture can be run through it
# --------------------------------------------------------------------------
def check_layer(layer: str | None, imports: set[str],
                third_party: frozenset[str] = frozenset()) -> list[str]:
    if layer is None or layer not in MAY_IMPORT:
        return []
    allowed = MAY_IMPORT[layer]
    bad = []
    for name in imports:
        head = name.split(".")[0]
        if name.startswith("."):
            continue                            # relative: inside its own layer
        if head == "ltm":
            parts = name.split(".")
            target = parts[1] if len(parts) > 1 else ""
            if target and target not in allowed:
                bad.append(f"{layer} imports ltm.{target}")
        elif head not in STDLIB and head not in third_party:
            bad.append(f"{layer} imports third-party {name!r}")
    return bad


def check_network(imports: set[str]) -> list[str]:
    return [f"network import {n!r}" for n in imports
            if n.split(".")[0] in NETWORK]


#: The other product's package and config-home name, assembled rather than
#: written: a literal here would be a token this very file must not contain, and
#: the check would flag its own source. R8's exemption covers prose, not code.
_FOREIGN_PKG = "m" + "fp"
_FOREIGN_HOME = "media-" + "fetch-" + "pipeline"


def check_foreign_vocabulary(imports: set[str], literals: list[str]) -> list[str]:
    bad = [f"imports the other product ({n!r})" for n in imports
           if n.split(".")[0] == _FOREIGN_PKG]
    for lit in literals:
        for token in VOCAB_FOREIGN | {_FOREIGN_HOME}:
            if lit == token or token in path_components(lit):
                bad.append(f"names {token!r} as a path component or literal")
    return bad


def check_core_stays_core(layer: str | None, names: set[str],
                          literals: list[str]) -> list[str]:
    """core must not become store-shaped.

    An import graph cannot see the SHAPE of a file's contents: in the ancestor
    product a module classified as core was transcript-shaped top to bottom and
    the gate said nothing. So core is also forbidden the store's own disk
    vocabulary, as a public name or as a literal. Note the comparison is
    equality against the disk names -- `_findings` is a directory, `findings` is
    a legitimate field of `Plan`, and a scan that conflated them would fire on
    correct code.
    """
    if layer != "core":
        return []
    bad = []
    for token in VOCAB_OWN:
        for n in names:
            if n.lower() == token.lower() or n.lower() == token.lower().lstrip("_"):
                bad.append(f"core exposes store vocabulary {n!r}")
        for lit in literals:
            if lit == token or token in path_components(lit):
                bad.append(f"core names the store's {token!r}")
    return bad


# --------------------------------------------------------------------------
# the tree itself
# --------------------------------------------------------------------------
@pytest.mark.parametrize("path", shipping_python(), ids=lambda p: p.name)
def test_every_shipping_file(path: pathlib.Path):
    source = path.read_text(encoding="utf-8")
    layer = module_layer(path)
    imports = imports_of(source)
    literals = string_literals(source)
    allowance = FILE_ALLOWANCE.get(file_key(path) or "", frozenset())
    assert check_layer(layer, imports, allowance) == []
    assert check_network(imports) == [] or layer == "ui"
    assert check_foreign_vocabulary(imports, literals) == []
    assert check_core_stays_core(layer, public_names(source), literals) == []


def test_core_allowlist():
    """Anything new lands on the forbidden side by default (INV-15)."""
    for path in SRC.rglob("core/*.py"):
        assert check_layer("core", imports_of(path.read_text(encoding="utf-8"))) == []
    # the allowlist really is a list of one
    assert MAY_IMPORT["core"] == frozenset({"core"})


def test_the_sidecar_exemption_is_narrower_than_the_rule():
    """The two properties that make `FILE_ALLOWANCE` safe rather than a hole.

    The card's INV-18 argument is "the product's own import graph contains no
    model runtime at all". That is true only while **nothing imports the one file
    that has one**, so the exemption is paid for here:

    1. no shipping file imports `engine.sidecar` (it is spawned, never imported);
    2. `sidecar.py` imports nothing from `ltm` -- it re-declares the wire
       vocabulary, because it runs under an interpreter that does not have this
       package on its path.

    Without (1) a single `from .sidecar import ...` would quietly put
    `faster_whisper` back into the product's graph and this file would still say
    "clean", because the allowance is keyed on the importee, not the importer.
    """
    sidecar = SRC / "ltm" / "engine" / "sidecar.py"
    assert sidecar.is_file(), "the exemption names a file that must exist"

    importers = []
    for path in shipping_python():
        if path == sidecar:
            continue
        imports = imports_of(path.read_text(encoding="utf-8"))
        if any(name.endswith("sidecar") or name.split(".")[-1] == "sidecar"
               for name in imports):
            importers.append(path.name)
    assert importers == [], (
        f"these files import the sidecar, which puts a model runtime back into "
        f"the product's graph: {importers}")

    own = imports_of(sidecar.read_text(encoding="utf-8"))
    assert not [n for n in own if n.startswith("ltm") or n.startswith(".")], (
        f"the sidecar must not import from this package: {sorted(own)}")
    assert FILE_ALLOWANCE == {
        "engine/sidecar.py": frozenset({"faster_whisper", "ctranslate2"})}, (
        "a second exempt FILE is a design change, not an implementation detail. "
        "Both names here are one model runtime in one file the product never "
        "imports; a second key in this mapping is a different claim entirely")


def test_negative_control_an_import_of_the_sidecar_is_caught():
    """The known-false control for the check above.

    The real tree has no importer, so the check passes trivially and would pass
    just as well if the detection were broken. The predicate is therefore run
    against a source that DOES import it.
    """
    bad = "from ltm.engine.sidecar import main\n"
    imports = imports_of(bad)
    assert any(n.split(".")[-1] == "sidecar" for n in imports)
    # and a file that merely mentions the word in prose must not be caught
    innocent = imports_of('"""spawns the sidecar as a subprocess"""\nimport json\n')
    assert not any(n.split(".")[-1] == "sidecar" for n in innocent)


def test_no_network_imports():
    """INV-11 and INV-18 over the whole tree except `ui`."""
    for path in shipping_python():
        layer = module_layer(path)
        if layer == "ui":
            continue
        assert check_network(imports_of(path.read_text(encoding="utf-8"))) == []


def test_no_foreign_vocabulary():
    for path in shipping_python():
        source = path.read_text(encoding="utf-8")
        assert check_foreign_vocabulary(imports_of(source),
                                       string_literals(source)) == []


# --------------------------------------------------------------------------
# the controls -- one known-true positive, one known-false
# --------------------------------------------------------------------------
BAD = FIXTURES / "layering_bad.py_txt"
GOOD_PROSE = FIXTURES / "prose_ok.md"


def test_positive_control_every_check_flags_the_bad_fixture():
    source = BAD.read_text(encoding="utf-8")
    imports = imports_of(source)
    literals = string_literals(source)
    names = public_names(source)
    assert check_layer("core", imports), "the layer check missed a forbidden edge"
    assert check_network(imports), "the network check missed urllib"
    foreign = check_foreign_vocabulary(imports, literals)
    assert any(f.startswith("imports the other product") for f in foreign), \
        "the vocabulary check missed the foreign IMPORT"
    assert any(f.startswith("names") for f in foreign), \
        "the vocabulary check missed a foreign directory name in a literal"
    assert check_core_stays_core("core", names, literals), \
        "the core-shape check missed a store name in core"


def test_negative_control_prose_discussing_the_boundary_is_clean():
    """Prose that names both sides must not be flagged.

    The first draft of R8 said "no file in this repository", which `CLAUDE.md`
    itself violated in the commit that introduced it. A rule whose own
    repository fails it on day one is not strict, it is mis-scoped -- and the
    reader who hits the false positive learns to ignore the check.

    The fixture is fed to the check as ONE literal, because that is exactly how
    a docstring discussing the boundary reaches the scan: a shipping module's
    docstring is a string constant like any other, and it must survive.
    """
    prose = GOOD_PROSE.read_text(encoding="utf-8")
    # the control is worthless unless the fixture really does name the words
    for token in sorted(VOCAB_FOREIGN):
        assert token in prose, f"the fixture must MENTION {token!r}"
    assert check_foreign_vocabulary(set(), [prose]) == []


def test_docs_and_rulings_are_not_scanned_at_all():
    """`docs/` and `CLAUDE.md` are exempt, and the exemption is structural.

    Not "they happen to pass": they are not in the scanned population. R8's own
    text names both products' vocabularies on purpose, because a boundary
    nobody may describe cannot be reviewed.
    """
    scanned = set(shipping_python())
    assert RULINGS not in scanned
    assert not any(p for p in scanned if "docs" in p.parts)
    # and the fixture that is deliberately illegal is not scanned either
    assert BAD not in scanned


# --------------------------------------------------------------------------
# R8 over the scripts that execute but do not ship (user ruling 2026-09-09)
# --------------------------------------------------------------------------
@pytest.mark.parametrize("path", executing_evidence(), ids=lambda p: p.name)
def test_no_evidence_script_names_the_other_products_disk(path: pathlib.Path):
    source = path.read_text(encoding="utf-8")
    assert check_foreign_vocabulary(imports_of(source),
                                    string_literals(source)) == []


def test_the_evidence_scan_has_something_to_scan_and_both_controls_hold():
    """Widening a gate ships with the case it must catch AND the one it must not.

    A population of zero passes this rule the way a reject-everything gate scores
    100% on a one-sided calibration, so the size is asserted first.

    The known-TRUE positive is the illegal fixture, fed through the same check:
    it names the other product's output root as a path and its marker file as a
    literal, and both must be flagged. The known-FALSE control is the real
    script whose Chinese prose uses the ordinary word for "transcript" -- the
    exact hit that made this scan worth measuring before widening. It must stay
    silent, because a gate that cries wolf on correct text is one readers learn
    to skip.
    """
    scripts = executing_evidence()
    assert len(scripts) >= 10, [p.name for p in scripts]

    bad = BAD.read_text(encoding="utf-8")
    flagged = check_foreign_vocabulary(imports_of(bad), string_literals(bad))
    assert len(flagged) >= 3, flagged

    prose = next(p for p in scripts if p.name.startswith("positive_control"))
    text = prose.read_text(encoding="utf-8")
    assert any(t in text for t in VOCAB_FOREIGN), (
        "the known-false control must actually CONTAIN one of the words, or it "
        "is not controlling for anything")
    assert check_foreign_vocabulary(imports_of(text), string_literals(text)) == []
