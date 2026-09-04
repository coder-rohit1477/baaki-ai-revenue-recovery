"""K (Phase 2b-2 G1). eval/ import boundary: oracle independence, no production reach-through, no reverse edge."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "baaki"
EVAL = ROOT / "eval"
# Modules that must stay independent of every production interpretation/decision component (D-2b2-4).
ORACLE_MODULES = ("schema.py", "enr.py", "oracle.py", "profiles.py", "loader.py", "hashing.py", "records.py", "compare.py", "metrics.py", "stats.py", "report.py")
# D-2b2-G2-2 (LOCKED): only eval/sut/* may import production decision code, and only these modules.
SUT_ALLOWED = ("baaki.domain", "baaki.contracts", "baaki.policy.validate", "baaki.policy.arms", "baaki.policy.kernel", "baaki.policy.snapshot",
               "baaki.policy.ruleset", "baaki.policy.schemas", "baaki.rules_agent", "baaki.agent.context", "baaki.agent.mapping",
               "baaki.providers.llm.base", "baaki.providers.llm.fixtures", "eval")
SUT_FORBIDDEN = ("baaki.db", "baaki.pipeline", "baaki.agent.runtime", "baaki.providers.llm.openai", "eval.oracle")
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


def test_eval_imports_only_domain_contracts_and_itself_outside_sut():
    for f in EVAL.rglob("*.py"):
        if f.parent.name == "sut" or f.name == "run.py":
            continue  # run.py composes sut + oracle side; sut/* has its own allowlist below
        for imp in _imports(f):
            if imp.startswith("baaki.") or imp.startswith("eval"):
                assert imp.startswith(ALLOWED_FOR_EVAL), (f, imp)
            assert imp.split(".")[0] not in SDKS, (f, imp)


def test_sut_drivers_import_only_the_locked_production_allowlist():
    for f in (EVAL / "sut").glob("*.py"):
        for imp in _imports(f):
            if imp.startswith("baaki.") or imp.startswith("eval"):
                assert imp.startswith(SUT_ALLOWED), (f, imp)
                assert not imp.startswith(SUT_FORBIDDEN), (f, imp)
            assert imp.split(".")[0] not in (SDKS - {"sqlalchemy"}), (f, imp)  # snapshot.build_snapshot transitively imports sqlalchemy; no connection is ever opened
    run_imports = _imports(EVAL / "run.py")
    assert not any(i.startswith(("baaki.db", "baaki.pipeline", "baaki.agent.runtime", "baaki.providers.llm.openai")) for i in run_imports)


def test_oracle_side_never_imports_sut():
    for name in ORACLE_MODULES:
        for imp in _imports(EVAL / name):
            assert not imp.startswith("eval.sut"), (name, imp)


def test_nothing_in_src_imports_eval():
    for f in SRC.rglob("*.py"):
        for imp in _imports(f):
            assert not (imp == "eval" or imp.startswith("eval.")), (f, imp)


def test_eval_has_no_db_writer_or_pipeline_reach():
    for f in EVAL.rglob("*.py"):
        src = f.read_text()
        for tok in ("baaki_write.", "record_policy_decision", "create_recovery_action", "record_validation_result",
                    "run_decision_pipeline", "record_agent_proposal", "opt_out_contact_from_evidence", "opt_out_by_operator",
                    "OPENAI_API_KEY", "import openai"):
            assert tok not in src, (f, tok)


def test_eval_is_not_shipped_in_the_wheel_and_adds_no_dependency():
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert 'packages = ["src/baaki"]' in pyproject and "openai" not in pyproject
    assert not (EVAL / "openai.py").exists()
