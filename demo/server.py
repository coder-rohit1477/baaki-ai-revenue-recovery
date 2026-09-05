"""Judge-facing demo server. Standard library only — no web framework, no new dependency.

Start with:  make demo      (or)  uv run python -m demo.server
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import UUID

from pydantic import SecretStr

from baaki.config import assert_no_model_credential, take_model_credential
from baaki.domain.ids import new_id
from demo import razorpay, scenarios, store
from demo.provision import DB_NAME, provision
from demo.seed import SeededAccount, seed

STATIC = Path(__file__).parent / "static"
HOST, PORT = "127.0.0.1", int(os.environ.get("BAAKI_DEMO_PORT", "8899"))

log = logging.getLogger("baaki.demo")


class State:
    """Engines, the seeded scenario accounts, and the credential — held once for the process."""

    def __init__(self, db: str | None = None) -> None:
        """`db` names the database to build. The pre-flight passes its own so it can never disturb a
        running demo: provisioning drops the target database with FORCE, closing every connection to it."""
        # Taken before anything else exists, exactly as the composition entrypoint does: from here on the
        # environment holds no model credential and only this SecretStr can reach the provider.
        self.credential: SecretStr | None = take_model_credential()
        assert_no_model_credential()
        self.db = db or DB_NAME
        d = provision(recreate=True, db=self.db)
        self.engine_app = d.engine("baaki_app")
        self.engine_agent = d.engine("baaki_agent")
        self.engine_owner = d.engine("baaki_migrate")
        self.engine_super = d.engine("super")  # reset needs TRUNCATE; no other path uses this engine
        self.engine_ops = d.engine("baaki_ops")   # operator authority: W15/W16 only
        self.accounts = self._seed()
        self.links: dict[str, dict[str, Any]] = {}  # invoice_id -> payment link, this run only

    def scenario_accounts(self) -> dict[str, Any]:
        return {
            k: {"account_id": str(a.account_id), "contact_id": str(a.contact_id),
                "invoice_id": str(a.invoice_id), "invoice_number": a.invoice_number,
                "name": a.name, "amount_paise": a.amount_paise, "days_overdue": a.days_overdue,
                **scenarios.SCENARIOS[k]}
            for k, a in self.accounts.items()
        }

    def _seed(self) -> dict[str, Any]:
        return seed(self.engine_owner, self.engine_app, today=datetime.now(UTC).date())

    def reseed(self) -> None:
        """Restore the original baseline. Clears first, then seeds once.

        The previous implementation seeded again without clearing, so every press added a second
        organisation and nine more accounts — revenue at risk doubled and duplicate account rows appeared.
        """
        store.truncate_demo_data(self.engine_super)
        self.accounts = self._seed()
        self.links.clear()
        self.links: dict[str, dict[str, Any]] = {}  # invoice_id -> payment link, this run only


STATE: State | None = None


def state() -> State:
    assert STATE is not None
    return STATE


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("%s %s", self.command, self.path)

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any, code: int = 200) -> None:
        self._send(code, json.dumps(payload, default=str).encode(), "application/json")

    def do_GET(self) -> None:  # noqa: N802
        route = urlparse(self.path)
        if route.path in ("/", "/index.html"):
            self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif route.path == "/api/state":
            s = state()
            self._json({
                "dashboard": store.dashboard(s.engine_app),
                "accounts": store.accounts(s.engine_app),
                "activity": store.recent_activity(s.engine_app),
                "scenarios": s.scenario_accounts(),
                "live_available": s.credential is not None,
                "model_id": scenarios.LOCKED_MODEL_ID,
                "razorpay": {"available": razorpay.available(), "mode": "test"},
                "links": s.links,
                "funnel": store.funnel(s.engine_app),
                "attention": store.attention(s.engine_app),
                "approvals": store.pending_approvals(s.engine_app),
                "decided": store.decided_approvals(s.engine_app),
                "timeline": store.activity_timeline(s.engine_app),
            })
        elif route.path == "/api/timeline":
            account_id = UUID(parse_qs(route.query)["account_id"][0])
            self._json(store.timeline(state().engine_app, account_id))
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        s = state()
        try:
            if route.path == "/api/run":
                key = body["scenario"]
                acct = s.accounts[key]
                report = scenarios.run(
                    engine_app=s.engine_app, engine_agent=s.engine_agent,
                    account_id=acct.account_id, contact_id=acct.contact_id,
                    scenario=key, credential=s.credential,
                )
                self._json(report.as_dict())
            elif route.path == "/api/pay":
                self._json(store.simulate_payment(
                    s.engine_app, invoice_id=UUID(body["invoice_id"]), amount_paise=int(body["amount_paise"])))
            elif route.path == "/api/razorpay/link":
                acct = s.accounts[body["scenario"]]
                self._json(_create_link(s, acct))
            elif route.path == "/api/razorpay/check":
                self._json(_check_payments(s, UUID(body["invoice_id"])))
            elif route.path in ("/api/approvals/approve", "/api/approvals/reject"):
                approve = route.path.endswith("approve")
                action_id = UUID(str(body["action_id"]))
                note = str(body.get("note") or "").strip()
                if not approve and not note:
                    self._json({"error": "NoteRequired", "detail": "A rejection needs a reason."}, 400)
                else:
                    self._json(store.decide_approval(
                        s.engine_ops, action_id=action_id, approve=approve,
                        note=note or "approved by operator"))
            elif route.path == "/api/reset":
                s.reseed()
                self._json({"ok": True})
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:  # the demo degrades visibly rather than dying on the judge
            log.exception("demo request failed")
            self._json({"error": type(exc).__name__, "detail": str(exc)[:500]}, 500)


def _create_link(s: State, acct: SeededAccount) -> dict[str, Any]:
    """One real Test Mode Payment Link for this invoice. `reference_id` is made run-safe: Razorpay rejects
    a duplicate, and the demo is reset and replayed many times."""
    existing = s.links.get(str(acct.invoice_id))
    if existing is not None:
        return existing
    link = razorpay.create_payment_link(
        amount_paise=acct.amount_paise,
        first_min_partial_paise=min(1000000, acct.amount_paise),
        reference_id=f"{acct.invoice_number}-{new_id().hex[:8]}",
        invoice_id=str(acct.invoice_id),
        description=f"{acct.name} — invoice {acct.invoice_number}",
    )
    out = {**link.as_dict(), "invoice_id": str(acct.invoice_id)}
    s.links[str(acct.invoice_id)] = out
    return out


def _check_payments(s: State, invoice_id: UUID) -> dict[str, Any]:
    """Ask the provider what it has, then reconcile through the committed writers. No webhook, no tunnel."""
    raw = razorpay.fetch_payments()
    items = razorpay.captured_for_invoice(raw, str(invoice_id))
    if not items:
        # Nothing applicable — but say WHY. A payment the customer has just made is `authorized` for a
        # moment before Razorpay captures it, and the hosted link page already shows it as paid. Reporting
        # that as "no payment" is what made a working reconciliation look broken.
        pending = razorpay.pending_for_invoice(raw, str(invoice_id))
        return {"matched": 0, "applied": [], "already_reconciled": [], "pending": pending,
                "scanned": len(razorpay.items_with_exact_spans(raw)),
                "outstanding_paise": store.outstanding(s.engine_app, invoice_id),
                "invoice_state": store.invoice_state(s.engine_app, invoice_id),
                "source": "razorpay_test_mode"}
    return store.reconcile_provider_payments(
        s.engine_app, invoice_id=invoice_id, raw_response=raw, items=items)


def main() -> None:
    global STATE
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    STATE = State()
    print(f"\n  Baaki demo ready →  http://{HOST}:{PORT}\n")
    print(f"  live model calls: {'ENABLED' if STATE.credential else 'DISABLED (no OPENAI_API_KEY)'}")
    print(f"  model: {scenarios.LOCKED_MODEL_ID}\n")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
