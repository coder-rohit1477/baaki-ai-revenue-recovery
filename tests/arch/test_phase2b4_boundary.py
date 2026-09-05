"""Phase 2b-4 boundary: the composition entrypoint must sequence, never decide.

`scripts/` is the one package allowed to import `agent/` (§2.1 reverse rule). That privilege is what these
tests fence: it must not become a place where money moves, a credential lingers, or a socket opens.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "baaki"
ENTRYPOINT = SRC / "scripts" / "run_treatment_day.py"
SDKS = {"openai", "anthropic", "langchain", "litellm", "httpx", "requests", "aiohttp"}
NETWORK_MODULES = {"urllib", "socket", "http", "ssl", "ftplib", "smtplib", "telnetlib"}
# W08-W12 and the money writers: the entrypoint reaches these only through the pipeline, never directly.
AUTHORITY_WRITERS = {
    "baaki.db.writers.decision", "baaki.db.writers.action_auto", "baaki.db.writers.ledger",
    "baaki.db.writers.payment", "baaki.db.writers.webhook", "baaki.db.writers.sweep",
    "baaki.db.writers.operator", "baaki.db.writers.optout_evidence", "baaki.db.writers.validation",
    "baaki.db.writers.proposal",
}
PROTECTED_TOKENS = ("heldout", "calibration", "bank.v1", "bank.v2", "surface_seed", "heldout_touches")


def _imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def test_exactly_one_package_imports_the_agent(): 
    importers = set()
    for f in SRC.rglob("*.py"):
        if f.is_relative_to(SRC / "agent"):
            continue
        if any(i == "baaki.agent" or i.startswith("baaki.agent.") for i in _imports(f)):
            importers.add(f.relative_to(SRC).parts[0])
    assert importers == {"scripts"}, importers


def test_the_entrypoint_holds_no_authority_writer():
    assert not (_imports(ENTRYPOINT) & AUTHORITY_WRITERS)


def test_the_entrypoint_opens_no_socket_and_imports_no_sdk():
    imps = {i.split(".")[0] for i in _imports(ENTRYPOINT)}
    assert not (imps & SDKS)
    assert not (imps & NETWORK_MODULES)


def test_the_entrypoint_runs_the_barrier_before_the_pipeline():
    body = ENTRYPOINT.read_text(encoding="utf-8")
    assert body.index("assert_no_model_credential()") < body.index("run_decision_pipeline(\n")


def test_phase_2b4_code_never_references_protected_g4_material():
    for f in (ENTRYPOINT, SRC / "agent" / "observability.py", SRC / "config.py"):
        body = f.read_text(encoding="utf-8").lower()
        for token in PROTECTED_TOKENS:
            assert token not in body, (f, token)


def test_no_api_key_material_is_committed_by_this_phase():
    for f in (ENTRYPOINT, SRC / "agent" / "observability.py", SRC / "config.py", ROOT / ".env.example"):
        body = f.read_text(encoding="utf-8")
        assert "sk-" not in body.replace("sk-...", ""), f
