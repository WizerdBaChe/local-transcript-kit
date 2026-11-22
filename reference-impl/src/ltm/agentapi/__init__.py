"""The agent face. One verb set, two faces (BR-14).

`ui/cli.py` and `agentapi/verbs.py` dispatch to the SAME function objects -- not to
two implementations that agree today. `tests/test_agentapi_schema.py` asserts the
identity, because two functions with the same name is exactly how two faces drift
apart.

INV-12 lives here: no response may carry a free-text field able to hold transcript
content. `schema.py` enforces it by walking the response types, with both controls.
"""
