"""K (Phase 2b-1). Forward + reverse import rules for agent/ and providers/llm/, single port, no SDK, no writer reach."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "baaki"
AGENT_ALLOWED = ("baaki.domain", "baaki.contracts", "baaki.policy.schemas", "baaki.providers.llm", "baaki.db.writers.proposal", "baaki.agent")
PROVIDER_ALLOWED = ("baaki.domain", "baaki.contracts", "baaki.providers.llm")
LOWER_LAYERS = ("domain", "contracts", "policy", "pipeline", "db", "ledger", "actions", "reconcile", "experiment", "sim", "rules_agent")
SDKS = {"openai", "anthropic", "langchain", "litellm", "httpx", "requests", "aiohttp"}


def _imports(f: Path) -> set[str]:
    out = set()
    for n in ast.walk(ast.parse(f.read_text())):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module)
    return out


def test_agent_imports_only_its_allowlist():
    for f in (SRC / "agent").rglob("*.py"):
        for imp in _imports(f):
            if imp.startswith("baaki."):
                assert imp.startswith(AGENT_ALLOWED), (f, imp)


def test_providers_llm_imports_only_domain_and_contracts():
    for f in (SRC / "providers").rglob("*.py"):
        for imp in _imports(f):
            if imp.startswith("baaki."):
                assert imp.startswith(PROVIDER_ALLOWED), (f, imp)
            assert imp.split(".")[0] not in {"sqlalchemy", "psycopg"}, (f, imp)  # the provider layer has no DB reach


def test_no_lower_layer_imports_agent_or_providers():
    for layer in LOWER_LAYERS:
        for f in (SRC / layer).rglob("*.py"):
            for imp in _imports(f):
                assert not imp.startswith(("baaki.agent", "baaki.providers")), (f, imp)


def test_agent_cannot_reach_w08_w09_w10_or_any_writer_but_w07():
    for f in (SRC / "agent").rglob("*.py"):
        for imp in _imports(f):
            if imp.startswith("baaki.db"):
                assert imp == "baaki.db.writers.proposal", (f, imp)
        src = f.read_text()
        for tok in ("record_validation_result", "record_policy_decision", "create_recovery_action", "opt_out_", "KERNEL_TOKEN"):
            assert tok not in src, (f, tok)


def test_exactly_one_provider_port_and_no_sdk_importer():
    ports = [f for f in SRC.rglob("*.py") if re.search(r"^class AiProviderPort\(Protocol\)", f.read_text(), re.M)]
    assert [p.relative_to(SRC).as_posix() for p in ports] == ["providers/llm/base.py"]
    for f in SRC.rglob("*.py"):
        assert not ({i.split(".")[0] for i in _imports(f)} & SDKS), f
    # Phase 2b-3 landed the live adapter as openai_provider.py, so the module name cannot shadow a
    # same-named package on sys.path. The guard that matters is asserted above: no SDK is imported
    # anywhere in src/. D-2b3-1 chose stdlib, so the manifest below stays clean too.
    assert not (SRC / "providers" / "llm" / "openai.py").exists()
    assert (SRC / "providers" / "llm" / "openai_provider.py").exists()  # Phase 2b-3 implemented
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert "openai" not in pyproject and "httpx" not in pyproject


def test_prompt_templates_are_exactly_the_approved_files():
    """interp.v1 is retained, not deleted: a proposal stamped interp.v1 must stay reconstructible (§11.2)."""
    names = sorted(p.name for p in (SRC / "agent" / "prompts").iterdir())
    assert names == ["interp.v1.txt", "interp.v2.txt", "propose.v1.txt"]


def test_no_migration_added_by_phase_2b():
    versions = sorted(p.name for p in (ROOT / "migrations" / "versions").glob("*.py"))
    assert versions == ["0001_schema.py", "0002_write_functions.py", "0003_grants.py", "0004_opt_out_metadata.py",
                        "0005_opt_out_writers.py", "0006_seed_templates.py"]
