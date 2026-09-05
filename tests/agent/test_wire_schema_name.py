"""Regression: the wire schema name and the stored schema version are different things.

Root cause this pins down (confirmed live, 2026-09-05): the production request sent
`response_format.json_schema.name = "interpretation.v1"`, and the provider answered
400 invalid_request_error / invalid_value on `response_format.json_schema.name` — the dot is not a legal
character there. The old live smoke never caught it because it hand-built a request with the name
"interpretation" instead of using the production builder.

Two invariants must hold together, and breaking either is silent:
  * the WIRE name must be provider-legal, or every live call 400s;
  * the STORED schema_version must stay the dotted domain constant, or the validator rejects every
    proposal with UNKNOWN_SCHEMA_VERSION (check 04) and every decision degrades to L1.
"""

import re
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from baaki.agent.context import WIRE_SCHEMA_NAME, build_action_request, build_interpretation_request
from baaki.agent.mapping import SCHEMA_VERSION_FOR_KIND, map_response
from baaki.domain.enums import ParseStatus, ProposalKind
from baaki.policy.schemas import action_proposal_v1, interpretation_v1
from baaki.providers.llm.base import ProviderResponse, ProviderStatus
from baaki.providers.llm.openai_provider import OpenAIProvider
from tests.phase2_helpers import AS_OF, cand, facts

# The provider's documented character class for response_format.json_schema.name.
PROVIDER_LEGAL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MSG_ARGS = dict(correlation_id=uuid4(), trace_id=uuid4(), seed=None)


def interp_request():
    from baaki.agent.context import InboundMessage

    return build_interpretation_request(
        facts(candidates=[cand()]),
        InboundMessage(text="We will pay on Friday", received_at=AS_OF),
        **MSG_ARGS,
    )[0]


def action_request():
    return build_action_request(facts(candidates=[cand()]), interpretation=None, **MSG_ARGS)[0]


# ── item 6: the actual production request sends a provider-legal wire name ───────────────


def test_the_production_interpretation_request_sends_the_wire_name():
    assert interp_request().schema_name == "interpretation"


def test_the_production_action_request_sends_the_wire_name():
    assert action_request().schema_name == "action_proposal"


@pytest.mark.parametrize("request_", [interp_request(), action_request()])
def test_every_production_wire_name_is_provider_legal(request_):
    """A dot here is what produced the live 400. Anything outside [A-Za-z0-9_-] must fail this test."""
    assert PROVIDER_LEGAL_NAME.match(request_.schema_name), request_.schema_name


def test_the_wire_name_reaches_the_payload_that_goes_on_the_wire():
    """Asserted at the payload boundary, not just on the request object — this is what the provider sees."""
    provider = OpenAIProvider(None)
    payload = provider._payload(interp_request())
    assert payload["response_format"]["json_schema"]["name"] == "interpretation"
    assert PROVIDER_LEGAL_NAME.match(payload["response_format"]["json_schema"]["name"])


def test_no_wire_name_is_a_dotted_domain_version():
    for domain_version, wire in WIRE_SCHEMA_NAME.items():
        assert "." in domain_version and "." not in wire, (domain_version, wire)


# ── item 7: the internal/domain schema version is unchanged ──────────────────────────────


def test_the_internal_schema_versions_are_unchanged():
    assert interpretation_v1.SCHEMA_VERSION == "interpretation.v1"
    assert action_proposal_v1.SCHEMA_VERSION == "action_proposal.v1"


def test_the_stored_schema_version_is_the_domain_constant_not_the_wire_name():
    """The column the validator authorises against must keep the dotted version."""
    response = ProviderResponse(
        status=ProviderStatus.OK,
        raw_json={"intent": "NO_CLEAR_INTENT", "sentiment": "NEUTRAL", "confidence": 0.5, "evidence": []},
        provider="openai", model_id="gpt-4o-mini-2024-07-18", latency_ms=10, attempts=1,
    )
    proposal = map_response(
        response, interp_request(), kind=ProposalKind.INTERPRETATION, source_text="src",
        account_id=uuid4(), business_date=AS_OF.date(), invoice_hint=None,
        created_at=datetime.now(UTC),
    )
    assert proposal.schema_version == "interpretation.v1"
    assert proposal.schema_version != proposal_wire_name()
    assert proposal.parse_status is ParseStatus.OK


def proposal_wire_name() -> str:
    return interp_request().schema_name


def test_the_stored_version_agrees_with_the_validator_authority_table():
    """mapping.SCHEMA_VERSION_FOR_KIND must equal the validator's SCHEMA_FOR_KIND, or check 04 rejects all."""
    from baaki.policy.validate.ladder import SCHEMA_FOR_KIND

    assert SCHEMA_VERSION_FOR_KIND == SCHEMA_FOR_KIND
