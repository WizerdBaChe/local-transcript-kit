"""The store: INV-13 (all-or-nothing, serials never reused) and INV-20/INV-14.

Every test names the invariant or the PSM property it holds down. The store's
whole reason to exist is that a half-written round is worse than no round, so the
negative cases carry as much weight as the positive ones and each asserts that
**nothing landed**, not merely that an exception was raised.
"""
from __future__ import annotations

import json

import pytest

from ltm.store import layout, place, write

STEM = "talk"
DIGEST = "0123456789abcdef" * 4
RUN = layout.run_dir_name(STEM + ".m4a", DIGEST)

DECODE_ROUND = {"srt": "1\n00:00:00,000 --> 00:00:01,000\n對\n",
                "txt": "對\n",
                "finding": '{"findings": []}',
                "plan": '{"notes": []}'}
DECLINED_ROUND = {"finding": '{"findings": []}', "plan": '{"notes": []}'}


@pytest.fixture()
def root(tmp_path):
    r = tmp_path / "TranscriptDesk"
    write.write_origin(r, RUN, write.new_origin(RUN, STEM + ".m4a", DIGEST, 53_000_000))
    return r


def members_on_disk(root):
    """Members only. `_origin.json` is the run's record, not one of its members --
    it is the one file in the store that is rewritten rather than created once."""
    d = layout.run_dir(root, RUN)
    return sorted(str(p.relative_to(d)).replace("\\", "/")
                  for p in d.rglob("*")
                  if p.is_file() and p.name != layout.ORIGIN)


# --- INV-13, property 1: all or nothing ----------------------------------

def test_place_set_all_or_nothing(root):
    """INV-13. One existing name refuses the WHOLE set; nothing else lands."""
    rev, _ = write.place_round(root, RUN, STEM, DECODE_ROUND)
    before = members_on_disk(root)

    # The same serial again: `srt` already exists, `edit` does not.
    with pytest.raises(place.PlaceError) as e:
        place.place_set(root, RUN, rev, STEM, {"srt": "x", "edit": "{}"})
    assert "already exist" in str(e.value)
    assert layout.EDITS + "/" + rev + ".json" not in str(before)
    assert members_on_disk(root) == before, (
        "the member that did not collide must not have landed either")


def test_a_refused_set_leaves_no_partial_files(root):
    """INV-13. No `.partial` temporary survives a refusal."""
    with pytest.raises(place.PlaceError):
        place.place_set(root, RUN, "r000", STEM, {"srt": "x"}, max_path=10)
    assert not [p for p in root.rglob("*.partial")]
    assert members_on_disk(root) == []


def test_length_refusal_names_the_offending_member(root):
    """PSM section 7, property 2: refuse the set, NAME the member, never truncate."""
    with pytest.raises(place.PlaceError) as e:
        place.place_set(root, RUN, "r000", STEM, DECODE_ROUND, max_path=60)
    msg = str(e.value)
    assert any(k in msg for k in DECODE_ROUND), "the message must name a member kind"
    assert "60" in msg and "refused" in msg
    assert members_on_disk(root) == []


def test_unknown_member_kind_is_refused_not_guessed(root):
    with pytest.raises(ValueError, match="unknown member kinds"):
        place.place_set(root, RUN, "r000", STEM, {"transcript": "x"})


# --- INV-13, property 3: serials are never reused ------------------------

def test_serial_never_reused(root):
    """INV-13 / P-73. Deleting r001's files does not free r001.

    The scan alone would re-issue it -- **scanning cannot see subtraction** -- and
    then `_edits/r001.json` would describe different content, a mismatch no
    downstream consumer can see. The high-water mark is what sees it.
    """
    r0, _ = write.place_round(root, RUN, STEM, DECODE_ROUND)
    r1, paths = write.place_round(root, RUN, STEM, {"txt": "v2\n", "edit": "{}"},
                                  source_rev=r0)
    assert (r0, r1) == ("r000", "r001")

    for p in paths.values():
        p.unlink()
    assert place.serials_on_disk(root, RUN) == {"r000"}, "the scan has forgotten r001"
    assert write.next_serial(root, RUN) == "r002", "the record has not"


def test_a_serial_is_consumed_if_any_member_of_it_exists(root):
    """A leftover `_plans/r000.json` still blocks r000 from being re-issued."""
    place.place_set(root, RUN, "r000", STEM, {"plan": "{}"})
    assert place.serials_on_disk(root, RUN) == {"r000"}
    assert place.next_serial(-1, place.serials_on_disk(root, RUN)) == "r001"


def test_the_high_water_mark_may_not_fall(root):
    """P-73's guard lives in the writer, not in each caller."""
    write.place_round(root, RUN, STEM, DECODE_ROUND)
    record = write.read_origin(root, RUN)
    record["high_water"] = -1
    with pytest.raises(write.StoreError, match="may only rise"):
        write.write_origin(root, RUN, record)
    assert write.read_origin(root, RUN)["high_water"] == 0


def test_next_serial_reads_both_sources(root):
    """Either source alone is wrong: one cannot see a deletion, the other cannot
    see a file placed by hand."""
    assert place.next_serial(-1, []) == "r000"
    assert place.next_serial(4, []) == "r005"          # record ahead of disk
    assert place.next_serial(-1, ["r009"]) == "r010"   # disk ahead of record
    assert place.next_serial(2, ["r007", "junk"]) == "r008"


# --- INV-13: a declined round is still an event --------------------------

def test_declined_round_consumes_a_serial(root):
    """INV-13. "I looked and decided not to change it" is a conclusion on disk.

    A serial counts EVENTS in the run, not transcripts produced, so the declined
    round takes r001 and the next transcript is r002 -- never r001 again.
    """
    write.place_round(root, RUN, STEM, DECODE_ROUND)
    declined, paths = write.place_round(root, RUN, STEM, DECLINED_ROUND)
    assert declined == "r001"
    assert set(paths) == {"finding", "plan"}
    timed = layout.run_dir(root, RUN) / layout.TIMED
    assert not list(timed.glob(f"{STEM}.r001*.srt"))
    assert write.next_serial(root, RUN) == "r002"

    revs = write.read_origin(root, RUN)["revisions"]
    assert [r["serial"] for r in revs] == ["r000", "r001"]
    assert revs[1]["members"] == ["finding", "plan"]


# --- E-1: the decode record distinguishes absent from false -------------

def test_condition_on_previous_text_absent_is_not_false(root):
    """E-1. "never passed" is a defect, "passed False" is a decision."""
    empty = write.decode_record(write.read_origin(root, RUN), "r000")
    assert "condition_on_previous_text" not in empty
    write.record_decode(root, RUN, revision="r000",
                        condition_on_previous_text=False)
    decode = write.decode_record(write.read_origin(root, RUN), "r000")
    assert decode["condition_on_previous_text"] is False
    assert "condition_on_previous_text" in decode


def test_a_second_decode_does_not_overwrite_the_first_ones_settings(root):
    """The step-7 repair, with the defect it was found by as the positive.

    Decoding the same recording twice -- once on the shipping default, once with
    `--no-vad` to see whether VAD is what dropped a passage -- is the ordinary way
    to use this product. Before this, the second `record_decode` merged into ONE
    mapping and `r000`'s settings became `r001`'s, while both revisions sat on
    disk claiming to be different measurements. The assertion below is exactly the
    one that failed then.
    """
    write.record_decode(root, RUN, revision="r000", vad=True, cues=705)
    write.record_decode(root, RUN, revision="r001", vad=False, cues=936)
    record = write.read_origin(root, RUN)
    assert write.decode_record(record, "r000") == {"vad": True, "cues": 705}
    assert write.decode_record(record, "r001") == {"vad": False, "cues": 936}
    # A round that was not a decode has no provenance, and `{}` says so.
    assert write.decode_record(record, "r002") == {}


# --- INV-20 / R14: cite carries a human authorisation record ------------

def good_grant(rev="r000"):
    return {"grantedBy": "owner", "grantedAt": "2026-09-09T10:00:00Z",
            "scope": f"{RUN}/{rev}", "method": "ui-dialog"}


def test_cite_requires_authorisation(root):
    """INV-20 / R14, and ONLY the checkable half.

    Asserted: the record exists, its fields are complete, its scope names this
    revision. NOT asserted, and it must stay that way: whether `method` is
    honest. R6 leaves the product no way to tell a human's click from an agent
    writing `ui-dialog`, so this is an audit trail, not a gate -- a test that
    appeared to verify honesty would reintroduce the belief the card retracts.
    """
    rev, _ = write.place_round(root, RUN, STEM, DECODE_ROUND)

    with pytest.raises(write.StoreError):
        write.cite(root, RUN, rev, STEM, {})
    assert not layout.citable_dir(root, RUN, rev).exists(), (
        "an unauthorised cite must leave nothing behind: 'not authorised' and "
        "'not cited' must not look identical on disk")

    dest = write.cite(root, RUN, rev, STEM, good_grant(rev))
    grant = json.loads((dest / layout.GRANT).read_text(encoding="utf-8"))
    assert grant["scope"] == f"{RUN}/{rev}"
    assert grant["method"] == "ui-dialog"   # recorded, never believed


def test_cite_failure_names_missing_field(root):
    """INV-14. The failure says which field was missing, not just that it failed."""
    rev, _ = write.place_round(root, RUN, STEM, DECODE_ROUND)
    for field in write.GRANT_FIELDS:
        grant = good_grant(rev)
        del grant[field]
        with pytest.raises(write.StoreError) as e:
            write.cite(root, RUN, rev, STEM, grant)
        assert field in str(e.value), f"the message must name {field}"
        assert not layout.citable_dir(root, RUN, rev).exists()


def test_cite_refuses_a_scope_that_is_not_this_revision(root):
    """R14: a grant covers a single revision -- never a run, never standing."""
    rev, _ = write.place_round(root, RUN, STEM, DECODE_ROUND)
    for bad in (RUN, f"{RUN}/r001", "*"):
        grant = good_grant(rev) | {"scope": bad}
        with pytest.raises(write.StoreError, match="scope"):
            write.cite(root, RUN, rev, STEM, grant)
        assert not layout.citable_dir(root, RUN, rev).exists()


def test_cite_refuses_an_unknown_method(root):
    rev, _ = write.place_round(root, RUN, STEM, DECODE_ROUND)
    grant = good_grant(rev) | {"method": "trust-me"}
    with pytest.raises(write.StoreError, match="method"):
        write.cite(root, RUN, rev, STEM, grant)


def test_cite_copies_and_the_original_stays(root):
    """PSM section 7, point 2: copies, not links; and citing is not a move.

    A link back into the run folder changes with the next edit, which is not a
    citation. The originals stay, so the serial chain is not broken in two places.
    """
    rev, paths = write.place_round(root, RUN, STEM, DECODE_ROUND)
    dest = write.cite(root, RUN, rev, STEM, good_grant(rev))

    # No `lang` was passed, so the round claimed no language and neither does the
    # citation (I-18). `cite` FINDS the members rather than rebuilding their
    # names, which is what lets it copy a `.zh.` revision and a bare one alike --
    # `test_cite_finds_a_member_whatever_its_language_segment` is that control.
    copy = dest / layout.TIMED / f"{STEM}.{rev}.srt"
    assert copy.is_file() and not copy.is_symlink()
    assert copy.read_text(encoding="utf-8") == DECODE_ROUND["srt"]
    assert paths["srt"].is_file(), "the original must stay in its run folder"
    assert (dest / layout.GRANT).is_file(), (
        "the grant sits WITH the content it authorises (point 3)")
    assert not (dest / layout.FINDINGS).exists(), "a citation cites text"


def test_a_filename_carries_a_language_only_when_one_was_measured(root):
    """I-18 / PIM §9.1, and §9.1 demands BOTH sides of this control.

    A test that only checked the determinate recording would pass on the broken
    implementation too -- the old default was `"zh"`, so the Chinese case was
    right by accident, which is exactly why the defect survived to a real run.
    """
    named, _ = write.place_round(root, RUN, STEM, DECODE_ROUND, lang="zh")
    silent, _ = write.place_round(root, RUN, STEM, DECODE_ROUND, lang=None)
    timed = layout.run_dir(root, RUN) / layout.TIMED
    assert (timed / f"{STEM}.{named}.zh.srt").is_file()
    assert (timed / f"{STEM}.{silent}.srt").is_file()
    # And the separator went with the segment: no `..srt`, no placeholder code.
    assert not list(timed.glob(f"{STEM}.{silent}.*.srt"))


def test_the_language_a_filename_may_claim_is_the_one_every_stretch_agrees_on():
    """The reduction rule, over its whole domain. Four cases, four answers.

    `undetermined` is a MEASUREMENT (BR-4: no label had majority support from two
    independent signals) and is the one value that must never reach a filename --
    the grating recording measured 0.311 and was still written `.zh.`.
    """
    assert layout.filename_language(["zh", "zh", "zh"]) == "zh"
    assert layout.filename_language(["en"]) == "en"
    assert layout.filename_language(["zh", "en"]) is None       # a mix
    assert layout.filename_language(["zh", "undetermined"]) is None
    assert layout.filename_language(["undetermined"]) is None
    assert layout.filename_language([]) is None                 # nothing measured


def test_a_member_is_found_by_reading_the_directory_not_by_rebuilding_its_name(
        root):
    """I-18's second half: readers stop guessing.

    Positive control: a revision written before this change (`.zh.srt`) is still
    found. Negative control: a revision written after it (bare `.srt`) is found by
    the same call. A reader that rebuilt one name would find exactly one of these
    two, and which one it missed would depend on when the file was written.

    The third assertion is the reason this is a regex and not a glob: at r1000 the
    serial widens to four digits, and `r100*` matches `r1000`.
    """
    old, _ = write.place_round(root, RUN, STEM, DECODE_ROUND, lang="zh")
    new, _ = write.place_round(root, RUN, STEM, DECODE_ROUND, lang=None)

    found_old = layout.members_on_disk(root, RUN, old, STEM, ("srt", "txt"))
    found_new = layout.members_on_disk(root, RUN, new, STEM, ("srt", "txt"))
    assert found_old["srt"].name == f"{STEM}.{old}.zh.srt"
    assert found_new["srt"].name == f"{STEM}.{new}.srt"

    # A neighbouring serial whose text starts with this one's must not be picked up.
    timed = layout.run_dir(root, RUN) / layout.TIMED
    (timed / f"{STEM}.{old}0.srt").write_text("decoy\n", encoding="utf-8")
    again = layout.members_on_disk(root, RUN, old, STEM, ("srt",))
    assert again["srt"].name == f"{STEM}.{old}.zh.srt"

    # A kind that never carried a language is returned unchanged, not searched for.
    assert layout.members_on_disk(root, RUN, old, STEM, ("plan",))["plan"].name \
        == f"{old}.json"


def test_cite_finds_a_member_whatever_its_language_segment(root):
    """`cite` used to rebuild `<stem>.<rev>.zh.<ext>` and would now miss its own
    output. Both namings, one call, no `lang` argument."""
    old, _ = write.place_round(root, RUN, STEM, DECODE_ROUND, lang="zh")
    new, _ = write.place_round(root, RUN, STEM, DECODE_ROUND, lang=None)
    d_old = write.cite(root, RUN, old, STEM, good_grant(old))
    d_new = write.cite(root, RUN, new, STEM, good_grant(new))
    assert (d_old / layout.TIMED / f"{STEM}.{old}.zh.srt").is_file()
    assert (d_new / layout.TIMED / f"{STEM}.{new}.srt").is_file()


def test_cite_is_one_entry_per_run_and_revision(root):
    """R14: r001 and r002 of one recording are two separate grants."""
    r0, _ = write.place_round(root, RUN, STEM, DECODE_ROUND)
    r1, _ = write.place_round(root, RUN, STEM, {"txt": "v2\n", "edit": "{}"},
                              source_rev=r0)
    d0 = write.cite(root, RUN, r0, STEM, good_grant(r0))
    d1 = write.cite(root, RUN, r1, STEM, good_grant(r1))
    assert d0 != d1 and d0.name.endswith(".r000") and d1.name.endswith(".r001")

    with pytest.raises(write.StoreError, match="already exists"):
        write.cite(root, RUN, r0, STEM, good_grant(r0))
    assert len(write.read_origin(root, RUN)["citations"]) == 2


def test_cite_refuses_a_declined_round(root):
    """A declined round produced a conclusion, not a transcript."""
    rev, _ = write.place_round(root, RUN, STEM, DECLINED_ROUND)
    with pytest.raises(write.StoreError, match="no text to cite"):
        write.cite(root, RUN, rev, STEM, good_grant(rev))


# --- the record itself ---------------------------------------------------

def test_a_run_directory_without_a_record_is_not_a_run(root):
    (layout.run_dir(root, RUN) / layout.ORIGIN).unlink()
    with pytest.raises(write.StoreError, match="no record"):
        write.read_origin(root, RUN)


def test_the_record_names_the_content_hash_not_the_filename(root):
    """The run id is content-addressed, so the same talk copied elsewhere
    resolves to the same run and two recordings sharing a filename do not."""
    record = write.read_origin(root, RUN)
    assert record["source"]["content_sha256"] == DIGEST
    assert record["run"].endswith("-" + DIGEST[:layout.HASH_CHARS])
    assert layout.run_dir_name("elsewhere/" + STEM + ".m4a", DIGEST) == RUN
