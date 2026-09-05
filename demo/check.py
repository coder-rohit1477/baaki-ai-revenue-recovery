"""Pre-flight: one live call against the locked model, then a full scenario A run. No judge present.

    export OPENAI_API_KEY=sk-...   &&   make demo-check

Runs against its OWN database (`baaki_demo_check`), never the one `make demo` is serving. Building a demo
database drops it with FORCE, which closes every open connection — pointing that at a live demo would take
the presentation down. Safe to run at any time, including while the demo is up.
"""

from __future__ import annotations

import sys

from demo import scenarios
from demo.provision import CHECK_DB_NAME
from demo.server import State


def main() -> int:
    s = State(db=CHECK_DB_NAME)
    if s.credential is None:
        print("✗ no OPENAI_API_KEY in the environment — export it first, then re-run")
        return 1
    a = s.accounts["A"]
    r = scenarios.run(
        engine_app=s.engine_app, engine_agent=s.engine_agent,
        account_id=a.account_id, contact_id=a.contact_id, scenario="A", credential=s.credential,
    )
    print(f"database       : {s.db} (isolated from the running demo)")
    print(f"live call      : {r.live}")
    print(f"model          : {r.model_id}")
    print(f"parse status   : {r.parse_status}")
    print(f"interpretation : {r.interpretation}")
    print(f"validator      : {r.validation_outcome}  reasons={r.rejection_reasons}")
    print(f"decision       : {r.verdict} / {r.action_type} (tier {r.tier}, {r.degradation_level})")
    ok = r.live and r.parse_status == "OK"
    print("\n" + ("✓ live path healthy" if ok else "✗ live path did NOT produce a parsed interpretation"))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
