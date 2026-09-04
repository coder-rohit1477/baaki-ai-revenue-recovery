"""Baaki offline evaluation harness (PHASE2B2_PLAN, top-level package; D-2b2-9).

Dependency direction (arch-tested): eval/ → baaki.domain (vocabulary), baaki.contracts (facts types). The oracle
modules (`schema`, `enr`, `oracle`, `profiles`, `loader`, `hashing`) never import the production interpreter,
restriction detector, grammar, decision tree, agent, validator, or kernel, so the evaluation can detect their defects.
Nothing under src/baaki imports eval. G1 = infrastructure/bootstrap only; no number produced here is evidence.
"""
