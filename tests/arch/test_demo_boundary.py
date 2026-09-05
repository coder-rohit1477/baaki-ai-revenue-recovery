"""The demo is a shell, not a second brain. These guards are AST-only, so they cost nothing to run.

If someone later 'fixes' a demo number by computing it in Python, or reaches past the pipeline to write a
decision, these fail. That matters more than usual here: the demo is what a judge actually sees.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "demo"
# Writers the demo may call directly: provider confirmation (simulated or real) and its ledger application.
# `_call` is not a writer — it exposes the writer exception types (pipeline/run.py imports it the same way)
# and grants no authority. The AUTHORITY_WRITERS assertion below is unchanged and is what actually fences
# the demo out of decisions, validations and proposals.
PAYMENT_WRITERS = {
    "baaki.db.writers.sweep", "baaki.db.writers.payment", "baaki.db.writers.ledger", "baaki.db.writers._call",
}
# Writers that represent policy authority — only the pipeline may call these.
AUTHORITY_WRITERS = {
    "baaki.db.writers.decision", "baaki.db.writers.action_auto", "baaki.db.writers.validation",
    "baaki.db.writers.proposal", "baaki.db.writers.operator", "baaki.db.writers.optout_evidence",
}
VENDOR = {"openai", "anthropic", "razorpay", "httpx", "requests", "fastapi", "flask", "django", "langchain"}


def _imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def _files() -> list[Path]:
    return sorted(DEMO.rglob("*.py"))


def test_the_demo_never_writes_a_decision_a_validation_or_a_proposal_itself():
    for f in _files():
        assert not (_imports(f) & AUTHORITY_WRITERS), f


def test_the_demo_pulls_in_no_vendor_sdk_or_web_framework():
    for f in _files():
        assert not ({i.split(".")[0] for i in _imports(f)} & VENDOR), f


def test_the_scenarios_go_through_the_committed_composition_entrypoint():
    body = (DEMO / "scenarios.py").read_text(encoding="utf-8")
    assert "from baaki.scripts.run_treatment_day import" in body
    assert "run_treatment_day(" in body
    # no hand-rolled validate/decide inside the demo
    assert "from baaki.policy.validate" not in body and "from baaki.policy.kernel" not in body


def test_the_demo_only_touches_the_sanctioned_payment_writers():
    used = set()
    for f in _files():
        used |= {i for i in _imports(f) if i.startswith("baaki.db.writers")}
    assert used <= PAYMENT_WRITERS, used


def test_demo_data_is_labelled_synthetic():
    assert "SYNTHETIC" in (DEMO / "seed.py").read_text(encoding="utf-8")
    page = (DEMO / "static" / "index.html").read_text(encoding="utf-8")
    assert "DEMO · SYNTHETIC DATA" in page and "SIMULATED" in page
    # Razorpay Test Mode is now genuinely wired, so the old "no live Razorpay integration" wording became
    # false and was removed. The guard is not dropped — it is tightened onto the claims that still matter:
    # name the sandbox, deny real money, and never assert a production integration.
    flat = " ".join(page.split())  # copy wraps across lines; assert on the rendered sentence, not the source
    assert "Razorpay Test Mode" in flat
    assert "no real money moves" in flat
    assert "no production Razorpay integration is claimed" in flat
    for false_claim in ("real money recovered", "live payments", "production payment"):
        assert false_claim not in flat.lower(), false_claim
