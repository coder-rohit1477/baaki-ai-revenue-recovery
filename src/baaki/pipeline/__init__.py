"""T2 orchestrator: validate → decide → create action (ARCHITECTURE.md §5.8).

Lives outside policy/ and actions/ because §5.3 forbids policy/ from importing the action writer and actions/ from
importing the decision writer; this package may import both plus policy and ledger reads, and nothing from agent/
or providers/. Phase 2 creates authoritative action records only — it executes nothing.
"""
