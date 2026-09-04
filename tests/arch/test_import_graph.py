"""K. §5.3 import boundaries, KERNEL_TOKEN importers, raw_response opacity, no vendor SDKs."""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "baaki"

FORBIDDEN_EDGES = {
    "agent": ["baaki.ledger", "baaki.actions", "baaki.reconcile", "baaki.experiment", "baaki.providers.razorpay",
              "baaki.db.writers.decision", "baaki.db.writers.action_auto", "baaki.db.writers.ledger", "baaki.db.writers.payment",
              "baaki.db.writers.webhook", "baaki.db.writers.sweep", "baaki.db.writers.validation",
              "baaki.db.writers.optout_evidence", "baaki.db.writers.operator", "baaki.db.models", "baaki.db.engine", "baaki.pipeline",
              "baaki.policy.validate", "baaki.policy.kernel", "baaki.policy.arms", "baaki.policy.snapshot", "baaki.policy.optout",
              "baaki.policy.ruleset", "baaki.rules_agent"],
    "providers": ["baaki.agent", "baaki.policy", "baaki.pipeline", "baaki.db", "baaki.ledger", "baaki.actions", "baaki.reconcile",
                  "baaki.experiment", "baaki.rules_agent", "sqlalchemy", "psycopg"],
    "db": ["baaki.agent", "baaki.providers", "baaki.policy", "baaki.pipeline", "baaki.rules_agent"],
    "policy": ["baaki.providers", "baaki.agent", "baaki.actions", "baaki.db.writers.ledger", "baaki.db.writers.payment",
               "baaki.db.writers.action_auto", "baaki.db.writers.webhook", "baaki.db.writers.sweep", "baaki.db.writers.operator",
               "baaki.db.writers.proposal", "baaki.db.models", "baaki.db.engine"],
    "actions": ["baaki.agent", "baaki.db.writers.ledger", "baaki.db.writers.payment", "baaki.db.writers.decision"],
    "reconcile": ["baaki.agent", "baaki.policy.kernel"],
    "sim": ["baaki.experiment", "baaki.policy", "baaki.agent", "baaki.db.writers"],
    "experiment": ["baaki.agent", "baaki.sim"],
    "ledger": ["baaki.agent", "baaki.policy", "baaki.actions", "baaki.providers", "baaki.db.writers", "baaki.pipeline"],
    "rules_agent": ["baaki.providers", "baaki.agent", "baaki.actions", "baaki.db", "baaki.ledger", "sqlalchemy", "psycopg"],
    "pipeline": ["baaki.agent", "baaki.providers", "baaki.db.writers.ledger", "baaki.db.writers.payment", "baaki.db.writers.webhook",
                 "baaki.db.writers.sweep", "baaki.db.writers.proposal", "baaki.db.writers.operator"],
    "contracts": ["openai", "anthropic", "razorpay", "httpx", "requests", "fastapi", "baaki.agent", "baaki.providers"],
    "domain": ["openai", "anthropic", "razorpay", "httpx", "requests", "fastapi", "sqlalchemy", "psycopg", "baaki.agent",
               "baaki.providers"],
}
VENDOR = {"openai", "anthropic", "razorpay", "httpx", "requests", "fastapi", "langchain", "chromadb"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def _module_files(pkg: str):
    return list((SRC / pkg).rglob("*.py"))


def test_forbidden_edges_absent():
    for pkg, forbidden in FORBIDDEN_EDGES.items():
        for f in _module_files(pkg):
            imps = _imports(f)
            for bad in forbidden:
                assert not any(i == bad or i.startswith(bad + ".") for i in imps), (f, bad)


def test_no_vendor_sdk_anywhere_in_p1():
    for f in SRC.rglob("*.py"):
        assert not ({i.split(".")[0] for i in _imports(f)} & VENDOR), f


def test_kernel_token_importers():
    for f in SRC.rglob("*.py"):
        if f.name == "policy_decision.py":
            continue
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(a.name == "KERNEL_TOKEN" for a in node.names):
                assert "/policy/kernel/" in str(f), f"{f} imports KERNEL_TOKEN"
            if isinstance(node, ast.Attribute) and node.attr == "KERNEL_TOKEN":
                raise AssertionError(f"{f} accesses KERNEL_TOKEN")


def test_raw_response_unwrap_only_in_sanctioned_modules():
    for f in SRC.rglob("*.py"):
        if "unwrap_for_audit" in f.read_text() and f.name != "agent_proposal.py":
            # W07 wrapper serialises the opaque blob for storage; that is the write path, not a semantic read.
            assert f.name == "proposal.py" and f.parent.name == "writers", f


def test_no_converter_from_claimed_to_paise():
    money = (SRC / "domain" / "money.py").read_text()
    assert "def to_paise" not in money and "-> Paise" not in money.split("def claimed_paise")[1].split("def add_paise")[0]
