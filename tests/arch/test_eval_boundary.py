"""K (Phase 2b-2 G1). eval/ import boundary: oracle independence, no production reach-through, no reverse edge."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "baaki"
EVAL = ROOT / "eval"
# Modules that must stay independent of every production interpretation/decision component (D-2b2-4).
ORACLE_MODULES = ("schema.py", "enr.py", "oracle.py", "profiles.py", "loader.py", "hashing.py")
FORBIDDEN_FOR_ORACLE = (
    "baaki.rules_agent", "baaki.policy.validate", "baaki.policy.kernel", "baaki.policy.arms", "baaki.policy.snapshot",
    "baaki.policy.optout", "baaki.policy.ruleset", "baaki.agent", "baaki.providers", "baaki.pipeline", "baaki.db",
    "baaki.ledger", "baaki.actions", "baaki.reconcile", "baaki.experiment", "baaki.sim",
)
ALLOWED_FOR_EVAL = ("baaki.domain", "baaki.contracts", "eval")
SDKS = {"openai", "anthropic", "langchain", "litellm", "httpx", "requests", "aiohttp", "sqlalchemy", "psycopg"}


def _imports(f: Path) -> set[str]:
    out = set()
    for n in ast.walk(ast.parse(f.read_text())):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module)
    return out


def test_oracle_modules_never_import_production_interpretation_or_decision_code():
    for name in ORACLE_MODULES:
        for imp in _imports(EVAL / name):
            assert not imp.startswith(FORBIDDEN_FOR_ORACLE), (name, imp)


def test_eval_imports_only_domain_contracts_and_itself_in_g1():
    for f in EVAL.rglob("*.py"):
        for imp in _imports(f):
            if imp.startswith("baaki.") or imp.startswith("eval"):
                assert imp.startswith(ALLOWED_FOR_EVAL), (f, imp)
            assert imp.split(".")[0] not in SDKS, (f, imp)


def test_nothing_in_src_imports_eval():
    for f in SRC.rglob("*.py"):
        for imp in _imports(f):
            assert not (imp == "eval" or imp.startswith("eval.")), (f, imp)


def test_eval_has_no_db_writer_or_pipeline_reach():
    for f in EVAL.rglob("*.py"):
        src = f.read_text()
        for tok in ("baaki_write.", "record_policy_decision", "create_recovery_action", "record_validation_result",
                    "run_decision_pipeline", "record_agent_proposal", "opt_out_contact_from_evidence", "opt_out_by_operator"):
            assert tok not in src, (f, tok)


def test_eval_is_not_shipped_in_the_wheel_and_adds_no_dependency():
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert 'packages = ["src/baaki"]' in pyproject and "openai" not in pyproject
    assert not (EVAL / "openai.py").exists()
