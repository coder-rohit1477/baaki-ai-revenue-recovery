"""ValidationInput — proposal + the source text it was produced from + account facts (P2-D4)."""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict

from baaki.contracts.agent_proposal import AgentProposal
from baaki.contracts.candidate import AccountFacts


class ValidationInput(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    proposal: AgentProposal
    source_text: str
    facts: AccountFacts

    def source_hash_matches(self) -> bool:
        return hashlib.sha256(self.source_text.encode("utf-8")).hexdigest() == self.proposal.input_hash
