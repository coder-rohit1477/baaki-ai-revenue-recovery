"""Protected held-out generation (Phase 2b-2 G4).

Oracle-side only: this package may import eval.schema/oracle/profiles/enr and baaki.domain/contracts.
It must never import the system under test (rules_agent, policy, agent, eval.sut) — labels come from the
declarative oracle, never from the thing being measured (D-2b2-4, D-G4-6).
"""
