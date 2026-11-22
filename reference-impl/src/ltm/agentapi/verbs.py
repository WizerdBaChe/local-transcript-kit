"""The verbs. **One implementation each, reached by both faces** (BR-14).

PSM §9. `ui/cli.py` imports `IMPLEMENTATIONS` from this module and dispatches to
those exact objects; the agent face is the same table minus one entry. Two
functions with the same name in two modules is how a CLI and an API drift apart
while every test passes, so the identity is asserted rather than the behaviour:
`tests/test_agentapi_schema.py::test_both_faces_call_one_function`.

**`accept` was human-only and no longer is** (user ruling 2026-09-09, UAT A-5).
The reading V-10 left in place was the conservative one, and the user overturned
it in the user's own terms: a decision they cannot make from experience is not a
decision, and withholding the verb only moved the rubber stamp. Every verb is now
agent-callable.

`HUMAN_ONLY` stays, empty, and the mechanism keeps its own test. Deleting it
would mean the next verb that needs to be a person's act has to re-derive both
the split and the distinction between `human-only` and `unknown-verb` -- "not
implemented" and "deliberately a person's act" must never read the same to a
caller. What replaced the refusal as the guarantee is a RECORD: every revision
now says which face made it (`Context.face`), because "a person did this" stopped
being enforced and therefore has to be observable.

Every function here takes a `Context` and keyword arguments, returns a
`schema.Response`, and raises `schema.AgentError` with a code from a closed set.
Nothing here formats prose: the CLI does that, from the same objects.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import time
import dataclasses
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, Optional, Sequence

from ..core import editscript, ids, model
from ..core import progress as progress_module
from ..core.progress import Cancellation, ProgressEvent, ack, refusal_code
from ..detect import detectors, langmap, scriptform, thresholds
from ..detect import lexicon as lexicon_module
from ..engine import contract, runner
from ..store import came_from, layout, place, write
from . import schema
from .schema import AgentError, Identifier, ident, idents

#: What a verb needs to reach the disk and the engine. A frozen object rather than
#: module globals: two runs in one process (a test, or a future batch) must not be
#: able to see each other's root.
@dataclass(frozen=True)
class Context:
    #: Resolved when the Context is BUILT (PSM P-1), not when this module is
    #: imported: `LTM_ROOT` set after import must still be honoured.
    root: pathlib.Path = dataclasses.field(default_factory=layout.default_root)
    on_progress: Optional[Callable[[ProgressEvent], None]] = None
    cancellation: Optional[Cancellation] = None
    #: Which face made this call. `call()` sets it; nothing else should.
    #:
    #: It exists because adoption stopped being human-only (UAT A-5, 2026-09-09).
    #: While `accept` was refused to agents, "a person adopted this revision" was
    #: guaranteed by the refusal. Now it is guaranteed by nothing, so it is
    #: RECORDED instead -- otherwise the question "did I approve this edit or did
    #: an agent?" has no answer on disk the day it is first asked.
    face: Literal["human", "agent"] = "human"


ProgressSink = Callable[[ProgressEvent], None]


def _known_run(ctx: Context, run: str) -> dict:
    """The run's record, or `no-such-run`.

    The run id is NOT echoed into the refusal: it is the user's filename plus a
    hash, so it can be CJK or hold spaces, and `AgentError` only carries subjects
    that are identifiers. The caller asked about this run and does not need to be
    told which one it asked about.
    """
    try:
        return write.read_origin(ctx.root, run)
    except (write.StoreError, FileNotFoundError, OSError) as e:
        raise AgentError("no-such-run") from e


def _paths_of(paths: Mapping[str, pathlib.Path]) -> tuple[str, ...]:
    return tuple(str(p) for _k, p in sorted(paths.items()))


def _plan_path(ctx: Context, run: str, rev: str) -> pathlib.Path:
    return layout.run_dir(ctx.root, run) / layout.PLANS / f"{rev}.json"


def _read_plan(ctx: Context, run: str, rev: Optional[str]) -> tuple[str, dict]:
    """The round's own record, or `no-such-revision`. One reader, four callers."""
    record = _known_run(ctx, run)
    rev = rev or layout.serial(record["high_water"])
    path = _plan_path(ctx, run, rev)
    if not path.exists():
        raise AgentError("no-such-revision", rev)
    return rev, json.loads(path.read_text("utf-8"))


def _intake_source(ctx: Context, run: str) -> Optional[str]:
    """Where the media actually is, from the `_intake/` pointer.

    **Not from `_origin.json`.** That record's `source` is `{filename,
    content_sha256, bytes}` -- a description of the file, deliberately not a path,
    because a run record is the run's own history and a path is where somebody's
    disk happened to be on the day. `_intake/` is the pointer, which is what makes
    the no-copy decision (R10) work: the media is named in one place, and moving it
    breaks that one place loudly rather than half the store quietly.
    """
    marker = layout.intake_dir(ctx.root) / f"{run}.json"
    if not marker.is_file():
        return None
    return json.loads(marker.read_text("utf-8")).get("source")


def _detect_steps(ctx: Context) -> Optional[Callable[[str, int, int], None]]:
    """Turn `detectors.run`'s per-cue callback into INV-22's `detect` phase.

    The denominator is the cue count, which the caller already knows, and the
    detector's name rides in `ProgressEvent.name` -- an identifier, never a
    sentence. Built here rather than inside `detect` so that the emission shape
    lives beside the other verbs' and not inside one of them.
    """
    if ctx.on_progress is None:
        return None
    sink = ctx.on_progress
    t0 = time.monotonic()

    def step(name: str, done: int, total: int) -> None:
        sink(ProgressEvent(state="detecting", phase="detect",
                           elapsed=round(time.monotonic() - t0, 3),
                           done=float(done), total=float(total), unit="cues",
                           name=name))
    return step


# --- intake --------------------------------------------------------------

def intake(ctx: Context, *, path: str, from_url: Optional[str] = None,
           from_note: Optional[str] = None) -> schema.IntakeResponse:
    """Register a file, hash its CONTENT, and assign a run.

    The media is **not copied**. `_intake/` holds a pointer record naming where the
    file is, because a 53 MB copy per run is a cost nobody asked for and R10's
    「ordinary door」 is about the file being handed to a generic intake, not about
    the product taking custody of it. The hash is of the content (§7), so the same
    talk copied elsewhere resolves to the same run and two recordings that share a
    filename do not collide.

    **Three questions, three answers** (R17, PIM v1.0 §9.2). Identity is the
    content hash and only the hash; the lookup is by hash over the records that
    already exist; the folder keeps `<stem>-<hash8>` because a person opens it in
    Explorer and `45a0cad2/` means nothing to them.

    Until 2026-09-11 the implementation put the stem into the IDENTITY, and the
    spec had forbidden the consequence since v0.1 -- 「the same talk copied to
    another folder must resolve to the SAME run, or the user's work splits in
    two」. It split in two: dropping ` (1)` from a filename produced a second run
    under the same hash `45a0cad2`, with thirteen revisions of history stranded on
    the other side (I-19, measured 2026-09-10). A spec that is right and untested
    differs from no spec at all only in whose fault it was afterwards.
    """
    src = pathlib.Path(path)
    if not src.is_file():
        raise AgentError("bad-argument", "audio-missing")
    # PSM P-2: validate BEFORE anything is written, so a refusal leaves the
    # marker exactly as it was.
    try:
        url = came_from.clean_url(from_url)
        note = came_from.clean_note(from_note)
    except came_from.Refused as refused:
        raise AgentError("bad-argument", refused.subject) from None
    digest = ids.content_hash(src)
    size = float(src.stat().st_size)

    existing = _run_with_hash(ctx.root, digest)
    run = existing or layout.run_dir_name(src.name, digest)

    marker = layout.intake_dir(ctx.root) / f"{run}.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    previous: list = []
    if marker.exists():
        try:
            previous = json.loads(marker.read_text("utf-8")).get(came_from.KEY, [])
        except (OSError, ValueError, AttributeError):
            previous = []            # an unreadable marker is rewritten below
    rows = came_from.merged(previous, url, note)
    marker.write_text(json.dumps(
        {"schema": write.SCHEMA, "run": run, "source": str(src.resolve()),
         "sha256": digest, "bytes": size, came_from.KEY: rows},
        ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")

    if not write.origin_path(ctx.root, run).exists():
        write.write_origin(ctx.root, run,
                           write.new_origin(run, src.name, digest, int(size)))
    high_water = float(write.read_origin(ctx.root, run)["high_water"])
    return schema.IntakeResponse(
        verb=ident("intake"), run=run, path=str(marker), sha256=digest,
        bytes=size,
        # **Recognised, and it says so** -- not an error, not silence (§9.2). The
        # agent gets the fact as a number; the CLI turns it into the sentence.
        # `high_water` travels with it because 「this audio is already `<run>`」 is
        # only half an answer: the other half is how far that run already got.
        recognised=1.0 if existing else 0.0, high_water=high_water,
        # A count, never the text (INV-12): the URL lives on the marker.
        came_from_count=float(len(rows)))


def _run_with_hash(root: pathlib.Path, digest: str) -> Optional[str]:
    """The run this content already has, from the records that already exist.

    **No second index** (§9.2, the implementation-layer answer). Runs number in
    the tens; one linear pass over the markers on disk is cheaper than an index
    file, and -- the part that decides it -- an index can be out of step with the
    truth while a pass over the truth cannot.

    Ties are broken by the run that has got furthest, then by name. A tie can only
    exist because of the defect this function repairs, and when it does the answer
    that keeps the user's work is the run holding it.
    """
    store = root / layout.STORE
    if not store.is_dir():
        return None
    hits: list[tuple[int, str]] = []
    for d in sorted(store.iterdir()):
        marker = d / layout.ORIGIN
        if not marker.exists():
            continue
        try:
            record = json.loads(marker.read_text("utf-8"))
        except (OSError, ValueError):
            # A run folder whose marker cannot be read is not a match and is not
            # this verb's problem to fix; `runs` is where a broken store surfaces.
            continue
        if (record.get("source") or {}).get("content_sha256") == digest:
            hits.append((int(record.get("high_water", -1)), d.name))
    if not hits:
        return None
    return max(hits)[1]


# --- decode --------------------------------------------------------------

def _text_digest(cues: "Sequence[model.Cue]") -> str:
    """SHA-256 over the cue texts, stripped, NUL-separated.

    Separated rather than concatenated so that `["ab", "c"]` and `["a", "bc"]` are
    different fingerprints: where the boundaries fall is part of what changes
    between two decodes, and it is the part S3 counts.
    """
    h = hashlib.sha256()
    for cue in cues:
        h.update(cue.text.strip().encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


#: A language may be PINNED, and the pin is a code rather than a name. Free text
#: here would travel to the engine and into `_origin.json`, and "Chinese" and "zh"
#: recorded on different days are the same decode that cannot be compared. Two or
#: three lowercase letters covers every code faster-whisper knows (`yue` is the
#: only three-letter one); anything else is refused with the code form named.
LANGUAGE_CODE = re.compile(r"[a-z]{2,3}")


#: What the engine may be asked to compute in, and on what. Closed sets, because
#: both travel to another interpreter and a typo would come back as `crashed` --
#: a code that says the child died and nothing about why.
#:
#: The default is `float16` on `auto`, which is right when there is a GPU and is a
#: HARD FAILURE when there is not: CPU backends refuse float16 outright. That
#: happened on this machine mid-session (the CUDA device stopped being visible),
#: and the product had no way to be asked for anything else -- so a machine without
#: a working GPU could not run it at all. `int8` is the answer for that machine.
COMPUTE_TYPES = ("float16", "int8_float16", "int8", "float32")
DEVICES = ("auto", "cpu", "cuda")


def decode(ctx: Context, *, run: str, audio: Optional[str] = None,
           condition_on_previous_text: bool = False,
           vad: bool = True, language: Optional[str] = None,
           multilingual: bool = True,
           language_slots: bool = True,
           compute_type: str = "float16", device: str = "auto",
           duration_seconds: Optional[float] = None,
           language_set: Optional[Sequence[str]] = None,
           vad_threshold: Optional[float] = None,
           vad_min_silence_ms: Optional[int] = None,
           vad_speech_pad_ms: Optional[int] = None,
           python: Optional[str] = None,
           script: Optional[pathlib.Path] = None) -> schema.DecodeResponse:
    """Run the engine, build the four layers, place `r000`'s member set.

    The order here is the contract: the engine returns segments AND the per-slot
    audio side, the language map turns both into `Stretch`es, and only then can the
    `Cue`s be built -- a `Cue` names its stretch (INV-1), so the stretches cannot
    be an afterthought. `runner.build_recording` takes them as an argument for the
    same reason it always did: inventing one means inventing its `agreement`.

    **`language` and `vad` are arguments because the evidence was measured with
    them set (step 7).** §6.3's three cue counts came from decodes pinned to
    `language="zh"` with VAD off; the shipping default is auto-detect with VAD on,
    and it produces a different transcript -- fewer cues, and on the baseline
    recording no repetition for the detectors to find at all. A product whose own
    evidence cannot be reproduced through its own interface is a product that
    cannot be argued with, so both settings are reachable and both are recorded
    per revision. Which one is the RIGHT default is a user question, and it is
    `docs/uat-2026-09-09.md`'s first item rather than a decision taken here.

    **`multilingual` is on by default and excludes a pinned `language`** (PIM v1.3
    E-3, PSM section 17). On, the engine re-detects the language for every decode
    window -- the fix for I-20, where a Mandarin/English recording came out with
    zero Chinese characters. It cannot be combined with a pin, because the engine
    lets the per-window detection override the pin while reporting that the pin
    took; reproducing section 6.3 therefore means `language="zh",
    multilingual=False, vad=False`.
    """
    record = _known_run(ctx, run)
    source = audio or _intake_source(ctx, run)
    if not source:
        raise AgentError("bad-argument", "no-audio")
    if language is not None and not LANGUAGE_CODE.fullmatch(language):
        raise AgentError("bad-argument", "language")
    if not isinstance(multilingual, bool):
        raise AgentError("bad-argument", "multilingual")
    if multilingual and language is not None:
        # E-3(f), and HERE -- before the interpreter is spawned, let alone the
        # model loaded (A-19's shape). Letting the flag win would silently disable
        # A-15 (`--language` exists so section 6.3's numbers can be re-run through
        # the product); letting the pin win would switch the flag off because of a
        # runtime condition, which E-3(e) forbids. So a person picks one.
        raise AgentError("bad-argument", "language-with-multilingual")
    if compute_type not in COMPUTE_TYPES:
        raise AgentError("bad-argument", "compute-type")
    if device not in DEVICES:
        raise AgentError("bad-argument", "device")
    if language_set is not None:
        # I-28. Same code form as a pin, and refused in the same places a pin is:
        # a set narrows per-window detection, so it needs that detection on and
        # nothing pinned.
        language_set = tuple(dict.fromkeys(language_set))
        if not language_set or not all(LANGUAGE_CODE.fullmatch(c)
                                        for c in language_set):
            raise AgentError("bad-argument", "language-set")
        if language is not None or not multilingual:
            raise AgentError("bad-argument", "language-set-with-pin")
    vad_knobs = (vad_threshold, vad_min_silence_ms, vad_speech_pad_ms)
    if any(v is not None for v in vad_knobs) and not vad:
        raise AgentError("bad-argument", "vad-knobs-without-vad")
    if vad_threshold is not None and not 0.0 < vad_threshold < 1.0:
        raise AgentError("bad-argument", "vad-threshold")
    if any(v is not None and (not isinstance(v, int) or v < 0)
           for v in (vad_min_silence_ms, vad_speech_pad_ms)):
        raise AgentError("bad-argument", "vad-milliseconds")

    request = contract.EngineRequest(
        audio=str(source), vad=vad, language=language,
        condition_on_previous_text=condition_on_previous_text,
        multilingual=multilingual,
        language_slots=language_slots,
        compute_type=compute_type, device=device,
        duration_seconds=duration_seconds,
        language_set=language_set,
        vad_threshold=vad_threshold, vad_min_silence_ms=vad_min_silence_ms,
        vad_speech_pad_ms=vad_speech_pad_ms)
    try:
        result = runner.run(request, cancellation=ctx.cancellation,
                            on_progress=ctx.on_progress, python=python,
                            script=script,
                            stderr_path=layout.engine_log(ctx.root, run))
    except runner.EngineError as e:
        if e.failure.code == "cancelled":
            # R13: a cancel is answered, and the answer says `cancelled` -- not
            # `engine-failed`, which is what this verb said until 2026-09-16 and
            # which reads to a person as "your run crashed". `detect` has always
            # answered this way; `decode` did not, because its cancel travels
            # back through the engine channel rather than as an exception.
            raise AgentError("cancelled", e.failure.phase or "decoding") from e
        raise AgentError("engine-failed", e.failure.code) from e

    # `default` because a decode with no segments is a legal answer (I-22). The
    # runner has always measured the duration by then, so the default is a guard
    # for a result built by hand, not a path a real decode takes.
    duration = result.duration or max((s.end for s in result.segments),
                                      default=0.0)
    if language_slots and result.slots:
        stretches = langmap.stretches(
            result.segments, duration,
            [s.language for s in sorted(result.slots, key=lambda x: x.index)])
    else:
        # No second signal, so there is no `agreement` to report and the product
        # will not invent one: one stretch, `undetermined`, 0.0 -- which BR-4 reads
        # as 「這段分不出來」 and is exactly true here. The alternative is a language
        # label backed by one signal, which is the claim this product exists to
        # avoid making.
        stretches = (model.Stretch(id="st000", start=0.0, end=duration,
                                   language="undetermined", agreement=0.0),)

    rec = runner.build_recording(result, id=run, path=str(source),
                                 sha256=record["source"]["content_sha256"],
                                 stretches=stretches, duration=duration)
    # Detection is part of this verb, so it is part of this verb's PROGRESS and of
    # its cancel window. Without `on_step` the run goes quiet between the last
    # language slot and the finished plan -- 936 cues of silence on the real
    # recording, which is 「單純反灰然後等待很久」 exactly (R13). And without `cancel`
    # the phase R13 named cancellable would be the one phase that ignores a request.
    try:
        plan_obj, _verdicts = detectors.run(
            rec, on_step=_detect_steps(ctx), cancel=ctx.cancellation, run_id=run)
    except progress_module.Cancelled as e:
        raise AgentError("cancelled", "detecting") from e
    payloads = {
        "srt": write.render_srt(rec.cues),
        "txt": write.render_txt(rec.cues),
        "finding": _findings_json(plan_obj),
        "plan": _plan_json(plan_obj, rec),
    }
    rev, paths = write.place_round(ctx.root, run, layout.run_stem_of(run),
                                   payloads, face=ctx.face,
                                   # What THIS decode measured, not a default
                                   # (I-18 / §9.1). `undetermined` or a mix and
                                   # the filename says nothing about language.
                                   lang=layout.filename_language(
                                       s.language for s in rec.stretches))
    write.record_decode(
        ctx.root, run, revision=rev,
        condition_on_previous_text=request.condition_on_previous_text,
        vad=request.vad,
        # E-3(c): what the CHILD reported, and `null` if it said nothing -- no
        # fallback to the request here, unlike `language_slots` below, because
        # "asked True, ran at the library default False" is exactly the decode
        # this field exists to make visible. When it is true, `language_detected`
        # below is the FIRST 30-second window only (E-3(d)).
        multilingual=result.multilingual,
        # Both faces of the language question, and they are different facts. `asked`
        # is `null` when nothing was pinned -- which is the shipping default and the
        # reason this run's cue count cannot be compared with §6.3's. `detected` is
        # what the engine settled on, and a pinned decode records it too: if they
        # ever disagree, the pin did not take.
        language_asked=request.language,
        language_detected=result.language,
        # I-28 / I-27: the child's word on what it narrowed to and which VAD
        # overrides it used; `null` = none, or a child that did not say.
        language_set=(list(result.language_set)
                      if result.language_set else None),
        vad_parameters=(dict(result.vad_parameters)
                        if result.vad_parameters else None),
        # Part of the settings, and the reason is I-1: the numeric precision is one
        # of the things that decides whether two decodes of one file agree. A record
        # that says which language was pinned but not what the arithmetic was is
        # missing half of "what was this measured with".
        compute_type=request.compute_type,
        device=request.device,
        # A fingerprint of the TEXT, because a decode is not reproducible and the
        # store is where that becomes checkable. Measured on this recording: SEVEN
        # decodes with identical arguments produced SEVEN distinct transcripts
        # (661..728 cues), two of which proposed deletions while five proposed none
        # (`docs/evidence/positive_control_2026-09-09.txt`). A cue COUNT cannot
        # answer 「是不是同一份逐字稿」 -- two decodes can agree on a count and
        # differ everywhere -- so the count is not the thing to record.
        text_sha256=_text_digest(rec.cues),
        # Usually 0.0. When it is not, the engine's own timestamps ran past the
        # duration `ffprobe` measured, and the last cue's `end` is therefore past
        # the end of the file -- which matters to anything that seeks with it
        # (A-23). It is recorded rather than reported once and lost, because it is
        # a property of THIS decode and the next one may not have it.
        timestamps_past_end=result.timestamps_past_end,
        # What the CHILD reported, falling back to what was asked. E-1's shape: the
        # request is not evidence the child honoured it, so the record prefers the
        # child's word and says so by preferring it.
        language_slots=(result.language_slots if result.language_slots is not None
                        else request.language_slots),
        model=pathlib.Path(request.model_path).name,
        cues=len(rec.cues), windows=len(rec.windows),
        stretches=len(rec.stretches), slots=len(result.slots),
        duration=duration,
        # The stretch table is the answer to 「哪裡該懷疑」, so it belongs in the run's
        # record and not only inside the plan: `show` reads it back, and a record
        # that held only the COUNT of stretches could say 18 without being able to
        # say where any of them were.
        stretch_table=[{"id": s.id, "start": s.start, "end": s.end,
                        "language": s.language, "agreement": s.agreement}
                       for s in rec.stretches])
    s = plan_obj.summary
    return schema.DecodeResponse(
        verb=ident("decode"), run=run, rev=rev,
        counts={"cues": float(len(rec.cues)), "windows": float(len(rec.windows)),
                "stretches": float(len(rec.stretches)),
                "slots": float(len(result.slots))},
        duration=float(duration), paths=_paths_of(paths),
        health={"vad_enabled": _as_number(rec.report.vad_enabled),
                "multilingual": _as_number(rec.report.multilingual),
                "degenerated": _as_number(rec.report.degenerated),
                # Seconds VAD kept as speech, and -1.0 whenever E-2(a) says the
                # number means nothing (VAD off, or the engine never said). I-22:
                # beside `counts["cues"] == 0` this is what separates "VAD found no
                # speech here" (0.0) from "the engine heard audio and wrote
                # nothing" -- two answers a caller should not have to guess between.
                "vad_kept": (float(rec.report.vad_kept)
                             if rec.report.vad_kept_is_trustworthy() else -1.0)},
        # The detection this verb already ran (v1.0 §7.1 ④). Same five fields as
        # `detect`, from the same summary object, so one round cannot be described
        # two ways by the two verbs that describe it.
        findings={k: float(v) for k, v in s.findings.items()},
        proposals={k: float(v) for k, v in s.proposals.items()},
        notes={k: float(v) for k, v in s.notes.items()},
        ops=float(len(plan_obj.script)),
        detectors_skipped=idents(s.detectors_skipped),
        detectors_failed=tuple((ident(a), ident(b))
                               for a, b in s.detectors_failed),
        degenerated_spans=tuple((float(w.start), float(w.end))
                                for w in rec.windows if w.temperature >= 1.0))


def _as_number(value: Optional[bool]) -> float:
    """`None` is -1.0, and that is not a cute encoding.

    E-2 needs three branches: reported true, reported false, and NOT REPORTED. A
    response field typed `float` cannot carry `None` without becoming
    `Optional[float]`, and an `Optional` here would let a consumer read "absent" as
    "false" -- the exact collapse E-1 and E-2 are both about. -1.0 is outside the
    0/1 domain, so a consumer that ignores it produces nonsense loudly.
    """
    return -1.0 if value is None else float(bool(value))


# --- detect --------------------------------------------------------------

def detect(ctx: Context, *, run: str, rev: Optional[str] = None,
           enabled: tuple[str, ...] = detectors.DEFAULT,
           terms: Optional[Mapping[str, tuple[str, ...]]] = None,
           word_level: bool = False,
           lexicons: tuple[str, ...] = (),
           script: Optional[str] = None) -> schema.DetectResponse:
    """Re-run detection over a revision's measured layers and place a new round.

    It reads the recording back from the plan record rather than keeping one in
    memory between verbs: the two faces are separate processes in the agent case,
    and a verb that only works when `decode` ran in the same process is not a verb.

    **The plan record carries all four layers, not just the text**, and that is
    what makes this verb honest rather than a text-only imitation of itself. S4
    measures decoder state, which lives on `Window`; a re-detect that had only the
    cues could offer INV-6 one textual signal and nothing else, so every proposal
    it produced would be a note. The four layers are 79 windows and 18 stretches on
    the baseline -- engineering numbers, cheap to keep, and they are the state the
    verdicts were measured against (§7 case 2).
    """
    # A value outside the closed set is refused before any store is read.
    if script is not None and script not in scriptform.SCRIPTS:
        raise AgentError("bad-argument", "script")
    rev, data = _read_plan(ctx, run, rev)
    rec = _recording_of(data)
    # `lexicons` turns `glossary` on: naming a lexicon and not wanting it read
    # is not a request anyone makes. The lexicon is every file in `_詞表/`
    # (the user's standing lists), then the named ones (a talk's own), merged.
    enabled = tuple(enabled)
    if lexicons and "glossary" not in enabled:
        enabled += ("glossary",)
    # Same rule for `script`: naming the expected script turns `script-switch` on.
    if script is not None and "script-switch" not in enabled:
        enabled += ("script-switch",)
    lex = freq = None
    if "glossary" in enabled:
        try:
            lex = lexicon_module.load_all(
                [*layout.lexicon_files(ctx.root), *lexicons])
            # Character frequency is optional: no `_詞表/_字頻/` -> no `prior`.
            freq = lexicon_module.load_freq(layout.char_freq_files(ctx.root)) or None
        except (lexicon_module.LexiconError, OSError, UnicodeDecodeError) as e:
            raise AgentError("bad-argument", "lexicon") from e
    try:
        plan_obj, _verdicts = detectors.run(
            rec, enabled=enabled, terms=terms, lex=lex, freq=freq,
            expect_script=script or scriptform.DEFAULT_SCRIPT,
            word_level=word_level,
            on_step=_detect_steps(ctx), cancel=ctx.cancellation, run_id=rev)
    except progress_module.Cancelled as e:
        raise AgentError("cancelled", "detecting") from e

    new_rev, paths = write.place_round(
        ctx.root, run, layout.run_stem_of(run),
        # No transcript in the member set: this round measured the same text and
        # changed none of it. §7 lets the ROUND decide its members, and placing an
        # identical SRT would put a second copy of the text on disk whose only
        # difference from the first is its serial.
        {"finding": _findings_json(plan_obj), "plan": _plan_json(plan_obj, rec)},
        face=ctx.face,
        source_rev=rev)
    s = plan_obj.summary
    return schema.DetectResponse(
        verb=ident("detect"), run=run, rev=new_rev,
        findings={k: float(v) for k, v in s.findings.items()},
        proposals={k: float(v) for k, v in s.proposals.items()},
        notes={k: float(v) for k, v in s.notes.items()},
        unverified={k: float(v) for k, v in s.unverified.items()},
        detectors_skipped=idents(s.detectors_skipped),
        detectors_failed=tuple((ident(a), ident(b))
                               for a, b in s.detectors_failed),
        paths=_paths_of(paths),
        passed_over={k: float(v) for k, v in s.passed_over.items()},
        script_cues={k: float(v) for k, v in s.script_cues.items()})


# --- plan / runs / show / open ------------------------------------------

def plan(ctx: Context, *, run: str, rev: Optional[str] = None
         ) -> schema.PlanResponse:
    rev, data = _read_plan(ctx, run, rev)
    return schema.PlanResponse(
        verb=ident("plan"), run=run, rev=rev,
        findings=tuple(_span_ref(row) for row in data["findings"]),
        notes=tuple(_span_ref(row) for row in data["notes"]),
        # The list, in the order `accept --op N` indexes (v1.0 §5.5). `data["ops"]`
        # -- the count -- stays on disk and is no longer what travels: a caller
        # that wants the number takes `len()` of the thing it is the length of,
        # which is one fact instead of two that can disagree.
        ops=tuple(_op_ref(row) for row in data["script"]),
        unverified={k: float(v) for k, v in data["unverified"].items()},
        candidates=tuple(_op_ref(row) for row in data.get("candidates", ())))


def runs(ctx: Context) -> schema.RunsResponse:
    """Every run under this root, from the markers on disk.

    A directory without `_origin.json` is not a run (`write.read_origin` says so),
    so this lists markers rather than directories -- a half-created run folder is
    not offered as something a caller can ask about.

    A run id is `<stem>-<hash8>` and the stem is the user's filename, so it can be
    CJK or hold a space: it travels as an id (bare `str`), never as an `Identifier`
    and never as a mapping KEY -- see `schema.RunRef`.
    """
    store = ctx.root / layout.STORE
    found: list[schema.RunRef] = []
    if store.is_dir():
        for d in sorted(store.iterdir()):
            marker = d / layout.ORIGIN
            if marker.exists():
                record = json.loads(marker.read_text("utf-8"))
                found.append(schema.RunRef(
                    run=d.name, high_water=float(record["high_water"])))
    return schema.RunsResponse(verb=ident("runs"), runs=tuple(found))


def show(ctx: Context, *, run: str, rev: Optional[str] = None
         ) -> schema.ShowResponse:
    record = _known_run(ctx, run)
    rev = rev or layout.serial(record["high_water"])
    # **This revision's provenance, not the run's.** The record used to hold one
    # decode mapping per run, so `show rev=r00001` printed whatever the LAST decode
    # had written -- and comparing two decodes of one recording is the ordinary way
    # to use this product (§7 case 2). A revision's numbers must be the numbers that
    # revision was measured with, or `show` is a confident liar.
    provenance = write.decode_record(record, rev)
    stretches = tuple(
        {"start": float(s["start"]), "end": float(s["end"]),
         "agreement": float(s["agreement"])}
        for s in provenance.get("stretch_table", ()))
    languages = idents(s["language"] for s in provenance.get("stretch_table", ()))
    members = layout.member_paths(
        ctx.root, run, rev, layout.run_stem_of(run), ("srt", "txt"))
    return schema.ShowResponse(
        verb=ident("show"), run=run, rev=rev,
        counts={k: float(v) for k, v in provenance.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)},
        stretches=stretches, languages=languages,
        paths=tuple(str(p) for p in members.values() if p.exists()))


def open_path(ctx: Context, *, path: str) -> schema.OpenResponse:
    """Hand a path back. **It does not launch anything from the agent face.**

    `open` is classed as its own verb in §9 because handing a path to the OS is a
    side effect on the user's desktop, and an agent asking for a window is a
    different act from a person clicking one. The agent face answers with the path
    and whether it exists; `ui/cli.py` is what actually opens it.
    """
    p = pathlib.Path(path)
    return schema.OpenResponse(verb=ident("open"), path=str(p),
                               exists=float(p.exists()))


# --- cite / cancel / accept ---------------------------------------------

def cite(ctx: Context, *, run: str, rev: str, granted_by: str, scope: str,
         method: str, granted_at: Optional[str] = None) -> schema.CiteResponse:
    """R14: agent-drivable, and every entry carries a human authorisation record.

    The grant is built here from arguments the caller supplied and checked by
    `store.write.check_grant`, which verifies the four fields and the closed
    `method` domain. What no code can verify is whether the human named actually
    said yes -- R6 leaves no way to ask them -- so the record is an audit trail and
    the check is the checkable half of INV-20, deliberately not more.
    """
    _known_run(ctx, run)
    grant = {"grantedBy": granted_by, "grantedAt": granted_at or write._now(),
             "scope": scope, "method": method}
    try:
        dest = write.cite(ctx.root, run, rev, layout.run_stem_of(run), grant)
    except write.StoreError as e:
        # The SENTENCE stays in the exception for the CLI to print; the agent gets
        # `grant-missing` and nothing else. Slicing the message into a `subject`
        # here would put prose in an agent-facing field through the back door
        # (INV-12), which is the same hole an allowlist of field names leaves open.
        raise AgentError("grant-missing", "authorisation") from e
    return schema.CiteResponse(verb=ident("cite"), run=run, rev=rev,
                               paths=(str(dest),))


def cancel(ctx: Context, *, state: str) -> schema.CancelResponse:
    """Answer within one event interval, always, and never with silence.

    The reply is computed from §8's edge list, so a state that gains or loses a
    cancel edge changes this answer in the same commit. The agent gets the refusal
    CODE; the sentence goes to the CLI, because INV-12 keeps prose out of here.
    """
    if ctx.cancellation is not None:
        reply = ctx.cancellation.request(state)
    else:
        reply = ack(state)
    code = refusal_code(state)
    return schema.CancelResponse(
        verb=ident("cancel"), state=schema.state(state),
        accepted=float(reply.accepted),
        reason_code=ident(code) if code else None,
        eta_seconds=reply.eta_seconds)


def accept(ctx: Context, *, run: str, rev: Optional[str] = None,
           ops: tuple[int, ...] = (), candidates: tuple[int, ...] = (),
           picked_by: Optional[str] = None) -> schema.AcceptResponse:
    """Apply selected ops, prove the result reverts, then write (INV-3/INV-4).

    **Agent-callable since 2026-09-09** (UAT A-5). What did not change: INV-3 (no
    write without a script), INV-4 (the revert is proven in memory first and
    nothing is written when the proof fails), and the declined round still
    consuming a serial. An agent gets exactly the same guarantees a person did,
    plus a `face` on the revision saying it was an agent.

    `candidates` names dormant ops from the plan's `candidates[]` by index; they
    are applied only when named, and naming any with no `ops` means candidates
    ONLY (a selection is a selection -- it does not also sweep in every script
    op). `picked_by` records who chose them (`human`, or `advisor:<model>@<prompt
    version>` when an LLM the user consented to picked them) in the edit record.
    A selection whose ops overlap is refused (`bad-argument`/`overlap`).
    """
    rev, data = _read_plan(ctx, run, rev)
    pool = data.get("candidates", [])
    if any(not 0 <= i < len(pool) for i in candidates):
        raise AgentError("bad-argument", "candidate")
    if picked_by is not None:
        try:
            got = schema.provenance(picked_by)
        except schema.SchemaError as e:
            raise AgentError("bad-argument", "picked-by") from e
        if not (got.text == "human" or got.text.startswith("advisor:")):
            raise AgentError("bad-argument", "picked-by")
    if candidates and not ops:
        ops = (-1,)
    chosen = [row for i, row in enumerate(data["script"]) if not ops or i in ops]
    extra = [dict(pool[i], picked_by=picked_by or "human")
             for i in sorted(set(candidates))]
    if extra:
        try:
            editscript.merge(tuple(_edit_op(r) for r in chosen),
                             tuple(_edit_op(r) for r in extra))
            for a in range(len(extra)):
                for b in range(a + 1, len(extra)):
                    editscript.merge((_edit_op(extra[a]),), (_edit_op(extra[b]),))
        except editscript.ScriptError as e:
            raise AgentError("bad-argument", "overlap") from e
        chosen = chosen + extra
    script = tuple(_edit_op(row) for row in chosen)
    rows = data["recording"]["cues"]
    source = {row["id"]: row["text"] for row in rows}

    if not script:
        # PSM §7 case (3): a declined round is a conclusion and **consumes a
        # serial**. "I looked and decided not to change it" and "I never ran" must
        # not look the same on disk, so the round is placed with the finding and
        # the plan it declined and no transcript at all.
        new_rev, paths = write.place_round(
            ctx.root, run, layout.run_stem_of(run),
            {"finding": _findings_json_raw(data),
             "plan": _plan_path(ctx, run, rev).read_text("utf-8")},
            source_rev=rev, face=ctx.face)
        return schema.AcceptResponse(
            verb=ident("accept"), run=run, rev=new_rev, source_rev=ident(rev),
            ops=0.0, declined=1.0, paths=_paths_of(paths))

    if not editscript.prove(source, script):
        # INV-4 runs in memory and NOTHING is written when it fails. The state
        # machine's `applying -> failed(proof)` edge exists for the case where the
        # proof is reached at write time; catching it here means that edge is not
        # taken for a script that could never have passed.
        raise AgentError("bad-argument", "proof-failed")
    applied, journal = editscript.apply(source, script)
    cues = [_CueLike(row["id"], row["start"], row["end"], applied[row["id"]])
            for row in rows]
    new_rev, paths = write.place_round(
        ctx.root, run, layout.run_stem_of(run),
        {"srt": write.render_srt(cues), "txt": write.render_txt(cues),
         "edit": _edit_json(rev, chosen, journal)},
        source_rev=rev, face=ctx.face,
        # An edit round changes text, never the language determination -- so it
        # inherits the source revision's answer rather than inventing one, and
        # inherits `None` just as faithfully (I-18).
        lang=_plan_language(data))
    return schema.AcceptResponse(
        verb=ident("accept"), run=run, rev=new_rev, source_rev=ident(rev),
        ops=float(len(script)), declined=0.0, paths=_paths_of(paths))


def _edit_op(row: Mapping[str, Any]) -> editscript.EditOp:
    return editscript.EditOp(op=row["op"],
                             span=model.Span(row["cue_id"], row["char_start"],
                                             row["char_end"]),
                             text=row.get("text"), code=row["code"],
                             origin=row["origin"])


def _edit_json(source_rev: str, ops: list[Mapping[str, Any]],
               journal: editscript.Journal) -> str:
    """The script AND what it removed. `revert` is total because of this file.

    The journal is not derivable from the ops: a delete's span says WHERE, and only
    the journal says what was there. Reverting by re-deriving would mean running
    the detector again against text it has already changed, which is not a revert
    -- it is a second opinion wearing the word.
    """
    return json.dumps(
        {"schema": write.SCHEMA, "source": source_rev, "ops": list(ops),
         "journal": [{"cue_id": j.cue_id, "char_start": j.char_start,
                      "char_end": j.char_end, "removed": j.removed,
                      "inserted": j.inserted} for j in journal]},
        ensure_ascii=False, indent=2) + "\n"


@dataclass(frozen=True)
class _CueLike:
    """The three fields a renderer needs. See `langmap.Timed` for the same trick.

    `accept` re-renders from the plan's own text table rather than from a
    `Recording`: rebuilding the four layers would mean re-deriving windows and
    stretches that this revision never measured, and a revision's verdicts hang on
    the version they were measured on (§7 case 2).
    """

    id: str
    start: float
    end: float
    text: str


# --- the records a round leaves behind -----------------------------------

def _findings_json(plan_obj: model.Plan) -> str:
    rows = [{"id": f.id, "code": f.code, "unit": f.unit,
             "severity": f.severity, "cue_id": f.span.cue_id,
             "char_start": f.span.char_start, "char_end": f.span.char_end,
             "evidence": dict(f.evidence.values), "actions": list(f.actions)}
            for f in plan_obj.findings]
    return json.dumps({"schema": write.SCHEMA, "findings": rows},
                      ensure_ascii=False, indent=2) + "\n"


def _findings_json_raw(plan_data: Mapping[str, Any]) -> str:
    return json.dumps({"schema": write.SCHEMA,
                       "findings": plan_data["findings"]},
                      ensure_ascii=False, indent=2) + "\n"


def _recording_json(rec: model.Recording) -> dict:
    """All four layers, as the round measured them.

    Not just the cues, and the reason is INV-6: S4 reads **decoder state** off
    `Window`, so a record holding only text lets a later `detect` offer one textual
    signal and nothing else -- every proposal it could make would be a note, and
    nothing in the output would say why. 79 windows and 18 stretches on the
    baseline: the cheapest half of the record and the half that carries the
    evidence.
    """
    return {
        "id": rec.id, "path": rec.path, "sha256": rec.sha256,
        "duration": rec.duration,
        "report": {"vad_kept": rec.report.vad_kept,
                   "vad_enabled": rec.report.vad_enabled,
                   "condition_on_previous_text":
                       rec.report.condition_on_previous_text,
                   "multilingual": rec.report.multilingual,
                   "degenerated": rec.report.degenerated},
        "windows": [{"id": w.id, "start": w.start, "end": w.end,
                     "avg_logprob": w.avg_logprob, "temperature": w.temperature,
                     "compression_ratio": w.compression_ratio,
                     "no_speech_prob": w.no_speech_prob} for w in rec.windows],
        "stretches": [{"id": s.id, "start": s.start, "end": s.end,
                       "language": s.language, "agreement": s.agreement}
                      for s in rec.stretches],
        "cues": [{"id": c.id, "start": c.start, "end": c.end, "text": c.text,
                  "window_id": c.window_id, "stretch_id": c.stretch_id}
                 for c in rec.cues],
    }


def _recording_of(data: Mapping[str, Any]) -> model.Recording:
    """The other direction. Every field is re-validated by the types themselves.

    Nothing here re-derives anything: windows are not recomputed from the cues and
    stretches are not re-cut. A revision's verdicts hang on the version they were
    measured against (§7 case 2), so a reader that recomputed a layer would be
    judging different data from the one the findings name.
    """
    try:
        raw = data["recording"]
    except KeyError as e:
        raise AgentError("no-such-revision", "no-recording") from e
    r = raw["report"]
    return model.Recording(
        id=raw["id"], path=raw["path"], sha256=raw["sha256"],
        duration=float(raw["duration"]),
        report=model.EngineReport(
            vad_kept=r["vad_kept"], vad_enabled=r["vad_enabled"],
            condition_on_previous_text=r["condition_on_previous_text"],
            # `.get`, not `[...]`: every plan written before E-3 shipped lacks the
            # key, and those rounds must keep loading. `None` is what they are --
            # not reported -- and not a back-filled `False` (PSM 17.0 (3)).
            multilingual=r.get("multilingual"),
            degenerated=bool(r["degenerated"])),
        windows=tuple(model.Window(**w) for w in raw["windows"]),
        stretches=tuple(model.Stretch(**s) for s in raw["stretches"]),
        cues=tuple(model.Cue(**c) for c in raw["cues"]))


def _plan_json(plan_obj: model.Plan, rec: model.Recording) -> str:
    """Everything a later `plan`/`detect`/`accept` needs, and the state it measured.

    The recording is here on purpose: `accept` must apply an `EditScript` to the
    EXACT text the findings were measured against (INV-5's source coordinates), and
    reading the SRT back would mean parsing a rendering -- one lossy step away from
    the thing the spans point at. `detect` needs the same record for the same
    reason, plus the windows S4 is measured on.
    """
    def ref(item, kind):
        row = {"id": item.id, "code": item.code,
               "unit": getattr(item, "unit", "cue"),
               "cue_id": item.span.cue_id,
               "char_start": item.span.char_start,
               "char_end": item.span.char_end,
               "evidence": dict(item.evidence.values) if kind == "finding" else {}}
        if kind == "note":
            # WHY this is a note, not just that it is one (A-24). The three
            # reasons are different instructions to the reader -- `near-threshold`
            # is the one A-20 created and README calls 「最值得你自己去看的」 --
            # and until this line they died here: the summary counted them by
            # reason, the rows did not carry it, so `plan` (the verb that says
            # WHERE to look) could not say which note that was.
            #
            # SCHEMA is not bumped, for A-21's reason: an unknown key is
            # unambiguous, and an ABSENT one is a third value meaning "this round
            # was written before the field existed" -- never read as
            # `single-signal`.
            row["reason"] = item.reason
        return row

    return json.dumps({
        "schema": write.SCHEMA,
        "findings": [ref(f, "finding") for f in plan_obj.findings],
        "notes": [ref(n, "note") for n in plan_obj.notes],
        "ops": len(plan_obj.script),
        "script": [{"op": o.op, "cue_id": o.span.cue_id,
                    "char_start": o.span.char_start,
                    "char_end": o.span.char_end, "text": o.text,
                    "code": o.code, "origin": o.origin}
                   for o in plan_obj.script],
        # Dormant: `accept` applies one only when it is named (`--candidate N`).
        # An absent key (a plan older than this field) reads as none.
        "candidates": [{"op": o.op, "cue_id": o.span.cue_id,
                        "char_start": o.span.char_start,
                        "char_end": o.span.char_end, "text": o.text,
                        "code": o.code, "origin": o.origin}
                       for o in plan_obj.candidates],
        "unverified": dict(plan_obj.summary.unverified),
        "detectors_skipped": list(plan_obj.summary.detectors_skipped),
        "detectors_failed": [list(x) for x in plan_obj.summary.detectors_failed],
        # Counts only (`script-variant`: lexicon terms written in simplified
        # characters, recognised and not reported). Absent key = not run.
        "passed_over": dict(plan_obj.summary.passed_over),
        # Cues by Chinese script, from `script-switch`; absent = it did not run.
        **({"script_cues": dict(plan_obj.summary.script_cues)}
           if plan_obj.summary.script_cues else {}),
        "recording": _recording_json(rec),
    }, ensure_ascii=False, indent=2) + "\n"


def _span_ref(row: Mapping[str, Any]) -> schema.SpanRef:
    # `reason` is absent on findings and on note rows written before A-24. Both
    # stay `None`: a missing reason is not `single-signal`, and guessing one here
    # would put a claim about the round into a reader that only transcribes.
    return schema.SpanRef(
        id=row["id"], cue_id=ident(row["cue_id"]),
        code=ident(row["code"]), unit=ident(row["unit"]),
        char_start=float(row["char_start"]), char_end=float(row["char_end"]),
        evidence={k: float(v) for k, v in (row.get("evidence") or {}).items()},
        reason=ident(row["reason"]) if row.get("reason") else None)


def _plan_language(data: Mapping[str, Any]) -> Optional[str]:
    """The language segment a round derived from THIS round's own stretches.

    A round that does not decode still writes files, and those files must make
    the same claim -- or the same silence -- as the decode they descend from. The
    stretches are in the plan record because `accept` needs the measured layers
    anyway (INV-5); reading them here adds no second source of truth.
    """
    stretches = (data.get("recording") or {}).get("stretches") or ()
    return layout.filename_language(s["language"] for s in stretches)


def _op_ref(row: Mapping[str, Any]) -> schema.OpRef:
    """One row of the on-disk `script[]`, minus the one field INV-12 forbids.

    `row["text"]` is deliberately not read here. It is `None` for every op
    milestone 1 produces (`delete` replaces nothing), so copying it would look
    harmless today and would be the route transcript text takes out on the day
    `replace` exists -- the shape INV-12 exists to refuse.
    """
    return schema.OpRef(
        op=ident(row["op"]), cue_id=ident(row["cue_id"]),
        char_start=float(row["char_start"]), char_end=float(row["char_end"]),
        code=ident(row["code"]), origin=schema.provenance(row["origin"]))


# --- the two faces -------------------------------------------------------

#: Every verb, once. `ui/cli.py` dispatches to THESE objects.
IMPLEMENTATIONS: Mapping[str, Callable[..., schema.Response]] = {
    "intake": intake,
    "decode": decode,
    "detect": detect,
    "plan": plan,
    "accept": accept,
    "runs": runs,
    "show": show,
    "open": open_path,
    "cite": cite,
    "cancel": cancel,
}

#: Verbs a person must perform. **Empty in milestone 1**, and kept as a
#: mechanism rather than deleted: see the module docstring. It is read at call
#: time, not folded into `AGENT_VERBS` at import time, so a verb put back into it
#: is refused immediately -- and so the mechanism's own test can prove it works
#: while nothing is in it (`test_agentapi_verbs.py`).
HUMAN_ONLY: tuple[str, ...] = ()
#: The agent surface. It is now the whole table (UAT A-5).
AGENT_VERBS: Mapping[str, Callable[..., schema.Response]] = dict(IMPLEMENTATIONS)


def call(ctx: Context, verb: str, **args: Any) -> schema.Response:
    """The agent entry point. Every failure is a code, never a message.

    `human-only` and `unknown-verb` are separate codes because the difference
    matters to the caller: one says "ask a person", the other says "you asked for
    something that does not exist".

    **This is also where the face is stamped.** Anything reaching a verb through
    here is an agent call, whatever the caller passed in its `Context`; the CLI
    keeps the default. One line here rather than a parameter on `accept` -- a
    per-verb flag is a flag the next verb forgets.
    """
    if verb in HUMAN_ONLY:
        raise AgentError("human-only", verb)
    if verb not in AGENT_VERBS:
        raise AgentError("unknown-verb", verb if IDENT_OK(verb) else None)
    ctx = dataclasses.replace(ctx, face="agent")
    try:
        return AGENT_VERBS[verb](ctx, **args)
    except TypeError as e:
        raise AgentError("bad-argument", verb) from e
    except detectors.DetectError as e:
        code = ("token-layer-absent" if detectors.TOKEN_LAYER in str(e)
                else "bad-argument")
        raise AgentError(code, verb) from e
    except (place.PlaceError, write.StoreError) as e:
        raise AgentError("bad-argument", verb) from e


def IDENT_OK(text: str) -> bool:
    """Is this safe to echo back as an `Identifier`?

    An unknown verb name came from the CALLER, and echoing an arbitrary string
    into a response is the one place this API could be talked into carrying text.
    So it is echoed only when it is already an identifier.
    """
    try:
        Identifier(text)
        return True
    except schema.SchemaError:
        return False
