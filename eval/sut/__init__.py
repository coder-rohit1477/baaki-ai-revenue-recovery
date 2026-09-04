"""SUT drivers — the ONLY eval modules permitted to import production decision code (D-2b2-G2-2, LOCKED).

Allowed: baaki.policy.{validate,arms,kernel,snapshot,ruleset,schemas}, baaki.rules_agent, baaki.agent.{context,mapping},
baaki.providers.llm.{base,fixtures}. Forbidden: baaki.db, baaki.pipeline, baaki.agent.runtime, writers, SDKs.
The oracle (eval/oracle.py) never imports this package.
"""
