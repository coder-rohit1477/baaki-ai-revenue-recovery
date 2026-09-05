"""The provider-facing schema must satisfy OpenAI strict Structured Outputs.

Found by the Phase 2b-3 live smoke: the provider rejected the previous schema with
"'required' is required to be supplied and to be an array including every key in properties".
The fixture provider never checked this, so the defect survived 2b-1, G2, G3 and G4.

These rules are asserted structurally so a future model change cannot reintroduce it without failing here.
Dropping the unsupported keywords loses no enforcement: the pydantic model still validates every reply.
"""

import pytest

from baaki.agent.context import _UNSUPPORTED_BY_STRICT, provider_json_schema
from baaki.policy.schemas.action_proposal_v1 import ActionProposalV1
from baaki.policy.schemas.interpretation_v1 import InterpretationV1

KINDS = ("interpretation", "action_proposal")
MODEL = {"interpretation": InterpretationV1, "action_proposal": ActionProposalV1}


def _objects(node, path="$"):
    """Every object schema in the tree, with its path."""
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            yield path, node
        for k, v in node.items():
            yield from _objects(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _objects(v, f"{path}[{i}]")


@pytest.mark.parametrize("kind", KINDS)
def test_required_names_every_property_of_every_object(kind):
    for path, obj in _objects(provider_json_schema(kind)):
        assert sorted(obj.get("required", [])) == sorted(obj["properties"]), (kind, path)


@pytest.mark.parametrize("kind", KINDS)
def test_every_object_stays_closed(kind):
    for path, obj in _objects(provider_json_schema(kind)):
        assert obj.get("additionalProperties") is False, (kind, path)


@pytest.mark.parametrize("kind", KINDS)
def test_no_keyword_strict_mode_rejects_survives(kind):
    def walk(node):
        if isinstance(node, dict):
            assert not (set(node) & _UNSUPPORTED_BY_STRICT), sorted(set(node) & _UNSUPPORTED_BY_STRICT)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(provider_json_schema(kind))


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("interpretation", {"promised_date_raw", "promised_amount_raw", "contact_correction"}),
        ("action_proposal", {"contact_id", "template_id", "followup_days"}),
    ],
)
def test_optional_fields_stay_optional_as_nullable_unions(kind, expected):
    """Optionality is expressed as `anyOf [T, null]`, never by omission from `required`."""
    props = provider_json_schema(kind)["properties"]
    nullable = {p for p, v in props.items() if any(x.get("type") == "null" for x in v.get("anyOf", []))}
    assert nullable == expected, (kind, sorted(nullable))
    for name in expected:
        assert MODEL[kind].model_fields[name].default is None, name


@pytest.mark.parametrize(
    "kind,collections", [("interpretation", {"invoice_refs", "evidence"}), ("action_proposal", set())]
)
def test_collection_fields_are_required_and_non_nullable(kind, collections):
    """A default of `[]` means "emit an empty list", not "omit the key" — semantics are unchanged."""
    schema = provider_json_schema(kind)
    for name in collections:
        assert name in schema["required"]
        assert schema["properties"][name].get("type") == "array"
        assert not schema["properties"][name].get("anyOf")
        assert MODEL[kind].model_fields[name].get_default(call_default_factory=True) == []


@pytest.mark.parametrize("kind", KINDS)
def test_the_model_still_enforces_what_the_provider_schema_no_longer_states(kind):
    """Enforcement moved nowhere: the schema is a hint, the model is the gate."""
    model = MODEL[kind]
    assert model.model_config["extra"] == "forbid"
    with pytest.raises(Exception):
        model.model_validate_json('{"unexpected_key": 1}')


def test_interpretation_keeps_all_nine_locked_intents():
    intents = provider_json_schema("interpretation")["$defs"]["Intent"]["enum"]
    assert set(intents) == {
        "WILL_PAY_ON_DATE",
        "REQUEST_INSTALLMENTS",
        "DISPUTE_AMOUNT",
        "DISPUTE_DELIVERY",
        "ALREADY_PAID_CLAIM",
        "WRONG_CONTACT",
        "NEEDS_DOCUMENT",
        "UNSUBSCRIBE",
        "NO_CLEAR_INTENT",
    }
    assert len(intents) == 9
