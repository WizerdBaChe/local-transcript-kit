"""INV-3, INV-4, INV-5. PSM section 5.

The product may only propose REMOVAL, and it may only write after proving the
removal reverts. These tests are the proof that the proof can fail.
"""
import pytest

from ltm.core.editscript import (
    EditOp,
    JournalEntry,
    ScriptError,
    apply,
    merge,
    prove,
    revert,
)
from ltm.core.model import Span

SOURCE = {
    "c0": "我學會了,我學會了,我學會了",
    "c1": "This is fine.",
    "c2": "對對對對",
}


def delete(cue, a, b, code="engine-loop", origin="detector:engine-loop"):
    return EditOp("delete", Span(cue, a, b), None, code, origin)


# --- INV-3: no write path without a script --------------------------------

def test_no_write_path_without_script():
    """Every text-changing entry point takes a script or a journal.

    Checked over the module's own surface rather than asserted in a comment: a
    helper added later that edits a string directly would fail this test, which
    is the only way the rule survives someone in a hurry.
    """
    import inspect
    import ltm.core.editscript as es
    mine = {name: obj for name, obj in vars(es).items()
            if not name.startswith("_") and inspect.isfunction(obj)
            and obj.__module__ == es.__name__}
    assert set(mine) >= {"apply", "revert", "merge", "prove"}
    for name, fn in mine.items():
        params = set(inspect.signature(fn).parameters)
        assert params & {"script", "journal", "a", "b", "ops"}, name


def test_delete_only_in_milestone_one_origins():
    with pytest.raises(ScriptError):
        EditOp("delete", Span("c0", 0, 3), None, "engine-loop", "guess")
    assert EditOp("delete", Span("c0", 0, 3), None, "x", "human")
    # accepted by the value domain, never produced by milestone 1
    assert EditOp("delete", Span("c0", 0, 3), None, "x",
                  "advisor:gemma4:e2b@v1")


def test_op_shapes_are_enforced():
    with pytest.raises(ScriptError):
        EditOp("delete", Span("c0", 0, 3), "text", "x", "human")
    with pytest.raises(ScriptError):
        EditOp("replace", Span("c0", 0, 3), None, "x", "human")
    with pytest.raises(ScriptError):
        EditOp("insert", Span("c0", 0, 3), "t", "x", "human")   # not zero width
    with pytest.raises(ScriptError):
        EditOp("delete", Span("c0", 3, 3), None, "x", "human")  # removes nothing


# --- INV-4: prove before write --------------------------------------------

def test_prove_before_write():
    script = (delete("c0", 5, 10),)      # the second "我學會了," of three
    assert prove(SOURCE, script) is True
    applied, journal = apply(SOURCE, script)
    assert applied["c0"] == "我學會了,我學會了"
    assert revert(applied, journal) == SOURCE


def test_failed_proof_writes_nothing():
    """A journal that does not describe the text makes revert fail loudly.

    `prove` returns False rather than raising because "this script does not
    revert" is a legitimate outcome with its own state (`failed(proof)`), not a
    bug. What must never happen is a silent partial write, so the negative
    control is a journal deliberately made inconsistent.
    """
    applied, journal = apply(SOURCE, (delete("c0", 0, 4),))
    tampered = (JournalEntry("c0", 0, 0, "NOT WHAT WAS REMOVED", "x"),)
    with pytest.raises(ScriptError):
        revert(applied, tampered)
    # and the source object was never touched by any of this
    assert SOURCE["c0"] == "我學會了,我學會了,我學會了"


def test_prove_is_false_when_the_script_cannot_apply():
    assert prove(SOURCE, (delete("c9", 0, 1),)) is False       # no such cue
    assert prove(SOURCE, (delete("c1", 0, 999),)) is False      # past the end


def test_replace_and_insert_also_revert():
    script = (EditOp("replace", Span("c1", 0, 4), "That", "x", "human"),)
    applied, journal = apply(SOURCE, script)
    assert applied["c1"] == "That is fine."
    assert revert(applied, journal) == SOURCE
    script = (EditOp("insert", Span("c1", 4, 4), " really", "x", "human"),)
    applied, journal = apply(SOURCE, script)
    assert applied["c1"] == "This really is fine."
    assert revert(applied, journal) == SOURCE


# --- INV-5: source coordinates, before and after merge ---------------------

def test_source_coordinates_after_merge():
    """Two deletions in one cue, and the second one's offsets are UNSHIFTED.

    If `apply` worked left to right, the second span would have to be rebased
    by the first deletion's length. It does not: application runs right to
    left, so both ops keep the offsets the reader can see in the source.
    """
    script = merge((delete("c0", 0, 5),), (delete("c0", 10, 14),))
    assert [(o.span.char_start, o.span.char_end) for o in script] == [(0, 5), (10, 14)]
    applied, journal = apply(SOURCE, script)
    # the first and third repetitions are gone; the second one and its comma
    # stay, and the third op's offsets were NEVER shifted by the first delete
    assert applied["c0"] == "我學會了,"
    assert revert(applied, journal) == SOURCE
    assert prove(SOURCE, script) is True


def test_merge_refuses_overlap_rather_than_picking_a_winner():
    with pytest.raises(ScriptError):
        merge((delete("c0", 0, 6),), (delete("c0", 4, 10),))
    # touching, not overlapping, is legal
    assert len(merge((delete("c0", 0, 5),), (delete("c0", 5, 10),))) == 2


def test_merge_refuses_two_inserts_at_one_point():
    """Their text does not overlap; their ORDER is undefined, which is worse.

    An undefined order produces a different transcript on a different day from
    the same script -- the failure would show up as an unreproducible diff, not
    as an error.
    """
    a = (EditOp("insert", Span("c1", 4, 4), " really", "x", "human"),)
    b = (EditOp("insert", Span("c1", 4, 4), " truly", "x", "human"),)
    with pytest.raises(ScriptError):
        merge(a, b)


def test_merge_is_not_composition():
    """The merged script applies to the SOURCE, not to `a`'s output."""
    a = (delete("c2", 0, 2),)
    b = (delete("c2", 2, 4),)
    merged = merge(a, b)
    applied, _ = apply(SOURCE, merged)
    assert applied["c2"] == ""
    # composition would have rebased b onto "對對", deleting nothing at [2,4)
    applied_a, _ = apply(SOURCE, a)
    assert applied_a["c2"] == "對對"


def test_ops_across_cues_are_independent():
    script = merge((delete("c0", 0, 5),), (delete("c1", 0, 5),))
    applied, journal = apply(SOURCE, script)
    assert applied["c0"] == "我學會了,我學會了"
    assert applied["c1"] == "is fine."
    assert revert(applied, journal) == SOURCE
