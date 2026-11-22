"""The agent face: INV-12, and the two faces that must be one set of functions.

PSM §9. Two claims live here and neither can be checked by reading the responses
that exist today:

* **No agent-facing response may carry a free-text field.** A walk over the real
  response types passes trivially if the walk is broken, so every check below runs
  a known-false control -- a type with a `str` field, a mapping keyed by a line of
  transcript, an `Identifier` built from one -- and asserts it is refused.
* **The CLI and the agent API dispatch to the SAME function objects.** Asserted by
  IDENTITY (`is`), not by behaviour: two functions with the same name that agree
  today is exactly the shape a CLI and an API drift apart in.
"""
from __future__ import annotations

import dataclasses
from typing import Mapping, Optional

import pytest

from ltm.agentapi import schema, verbs
from ltm.core.progress import IDENTIFIER


# --- INV-12 as a type ----------------------------------------------------

def test_every_shipping_response_passes_the_walk():
    """The known-TRUE side. Ten-plus types, every field, recursively."""
    assert len(schema.RESPONSES) == 11
    for cls in schema.RESPONSES:
        schema.check_response(cls)


#: The known-false controls, at MODULE scope on purpose: `get_type_hints` resolves
#: a postponed annotation in the defining module's namespace, so a control class
#: written inside a test function raises `NameError` instead of `SchemaError` -- and
#: a control that fails for the wrong reason is not a control.
@dataclasses.dataclass(frozen=True)
class _Bare(schema.Response):
    why: str


@dataclasses.dataclass(frozen=True)
class _InTuple(schema.Response):
    reasons: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class _InUnion(schema.Response):
    maybe: Optional[str]


@dataclasses.dataclass(frozen=True)
class _InMappingValue(schema.Response):
    said: Mapping[str, str]


@dataclasses.dataclass(frozen=True)
class _Inner:
    note: str


@dataclasses.dataclass(frozen=True)
class _Nested(schema.Response):
    detail: _Inner


@dataclasses.dataclass(frozen=True)
class _Fine(schema.Response):
    path: str
    paths: tuple[str, ...]
    run: str
    runs: tuple[str, ...]
    count: float
    label: schema.Identifier


def test_negative_control_a_free_text_field_is_refused():
    """...and the walk can fail, which is the half a passing suite cannot show.

    Five shapes, because the first version of this rule was a field-name allowlist
    and each of these is a way past one: a bare `str`, a `str` inside a container,
    a `str` inside a union, a `str` as a mapping VALUE, and a `str` nested one
    dataclass down.
    """
    for cls in (_Bare, _InTuple, _InUnion, _InMappingValue, _Nested):
        with pytest.raises(schema.SchemaError):
            schema.check_response(cls)

    # And the fields that ARE allowed stay allowed -- singular and plural, since the
    # plural is a rule rather than four more entries. Without this the controls
    # above would be passing because the walk rejects everything.
    schema.check_response(_Fine)
    assert schema._is_id_field("paths") and schema._is_id_field("runs")
    assert not schema._is_id_field("notes") and not schema._is_id_field("reason")


def test_an_identifier_refuses_transcript_text():
    """The type's value check, on real transcript-shaped strings.

    This is why the rule is a type and not a list of field names: `Identifier`
    refuses the VALUE, so a field somebody adds without reading §9 still cannot
    carry a line of the recording.
    """
    for good in ("engine-loop", "no-token-layer", "r001", "zh", "already-finished"):
        assert str(schema.ident(good)) == good
    for bad in ("對 對 對 對", "I don't know", "Nine years ago", "", "Engine-Loop",
                "x" * 65, "a line\nwith a newline"):
        with pytest.raises(schema.SchemaError):
            schema.ident(bad)
    # The pattern is the progress event's, imported and not restated.
    assert schema.IDENTIFIER is IDENTIFIER


def test_a_mapping_keyed_by_transcript_is_refused_at_construction():
    """The hole the static walk cannot see, and it is checked recursively.

    `Mapping[str, float]` keyed by a line of transcript satisfies every type check
    in the module. So keys are validated when the response is BUILT, at any depth:
    `ShowResponse.stretches` is a tuple OF mappings, and a check that looked only
    at top-level fields would pass a bad key one level down.
    """
    ok = schema.DetectResponse(
        verb=schema.ident("detect"), run="talk-0badc0de", rev="r001",
        findings={"engine-loop": 1.0}, proposals={}, notes={"single-signal": 2.0},
        unverified={"no-token-layer": 0.0}, detectors_skipped=(),
        detectors_failed=(), paths=())
    assert ok.findings["engine-loop"] == 1.0

    with pytest.raises(schema.SchemaError):
        schema.DetectResponse(
            verb=schema.ident("detect"), run="talk-0badc0de", rev="r001",
            findings={"對對對對對對": 1.0},        # a cue, as a key
            proposals={}, notes={}, unverified={}, detectors_skipped=(),
            detectors_failed=(), paths=())

    # one level down, inside a tuple of mappings
    with pytest.raises(schema.SchemaError):
        schema.ShowResponse(
            verb=schema.ident("show"), run="talk-0badc0de", rev="r001",
            counts={"cues": 3.0},
            stretches=({"start": 0.0, "I don't know": 1.0},),
            languages=(schema.ident("zh"),), paths=())


def test_an_error_subject_is_a_kind_not_a_value():
    """A refusal may not echo the caller's string back.

    The one place this API could be talked into carrying text is the error it
    returns for input it did not understand. So a subject is validated where it is
    raised, and the codes are a closed set -- `human-only` and `unknown-verb` are
    separate entries because "ask a person" and "that does not exist" are different
    answers.
    """
    err = schema.AgentError("bad-argument", "audio-missing")
    assert err.response("intake").code.text == "bad-argument"
    with pytest.raises(schema.SchemaError):
        schema.AgentError("no-such-run", "我的訪談 2026.m4a")
    with pytest.raises(schema.SchemaError):
        schema.ErrorResponse(verb=schema.ident("intake"),
                             code=schema.ident("not-a-real-code"))
    assert set(schema.ErrorResponse.CODES) >= {"human-only", "unknown-verb"}
    assert len(set(schema.ErrorResponse.CODES)) == len(schema.ErrorResponse.CODES)


# --- BR-14: one verb set, two faces --------------------------------------

def test_both_faces_call_one_function():
    """Identity, not equivalence. `is`, on every shared verb.

    A CLI and an API that each define `decode` will agree on the day they are
    written and diverge on the first bug fix that only one of them receives. So the
    agent table is built FROM the implementation table, and this asserts the two
    hold the same objects rather than equivalent ones.
    """
    from ltm.ui import cli

    for name, fn in verbs.AGENT_VERBS.items():
        assert fn is verbs.IMPLEMENTATIONS[name], name
    for name, fn in cli.VERBS.items():
        assert fn is verbs.IMPLEMENTATIONS[name], name
    assert set(cli.VERBS) == set(verbs.IMPLEMENTATIONS)


def test_every_verb_is_agent_callable_and_the_gate_still_works(monkeypatch):
    """A-5 opened the last verb; the gate stays, and is proven live anyway.

    Two claims, and the second is why this test is not just `assert HUMAN_ONLY ==
    ()`. **A gate that has never refused anything is not known to refuse.** So the
    positive control puts a verb back into `HUMAN_ONLY` and requires the refusal,
    with the right code: "deliberately a person's" and "does not exist" must not
    look the same to a caller -- an agent told `unknown-verb` would reasonably
    conclude the product cannot do it at all.
    """
    ctx = verbs.Context()
    assert verbs.HUMAN_ONLY == ()
    assert set(verbs.AGENT_VERBS) == set(verbs.IMPLEMENTATIONS)

    with pytest.raises(schema.AgentError) as unknown:
        verbs.call(ctx, "transcribe")
    assert unknown.value.code == "unknown-verb"

    monkeypatch.setattr(verbs, "HUMAN_ONLY", ("accept",))
    with pytest.raises(schema.AgentError) as human:
        verbs.call(ctx, "accept", run="x", rev="r001")
    assert human.value.code == "human-only"
    assert human.value.code != unknown.value.code

    # An unknown verb name came from the CALLER, so it is echoed only when it is
    # already an identifier -- the same rule as every other subject.
    with pytest.raises(schema.AgentError) as noisy:
        verbs.call(ctx, "請幫我把逐字稿做出來")
    assert noisy.value.subject is None


def test_every_verb_returns_a_registered_response_type():
    """The 'forgot to register it' hole an allowlist walk cannot see by itself.

    `RESPONSES` is what the INV-12 walk is run over, so a response class nobody put
    in it is an unchecked type. This asserts the two lists agree in both
    directions: every registered class is a `Response`, and every `Response`
    subclass defined in the module is registered.
    """
    defined = {v for v in vars(schema).values()
               if isinstance(v, type) and issubclass(v, schema.Response)
               and v is not schema.Response}
    assert defined == set(schema.RESPONSES)
