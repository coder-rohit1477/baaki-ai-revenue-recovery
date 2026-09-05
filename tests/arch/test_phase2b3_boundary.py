"""Phase 2b-3 architecture and security guards.

The live adapter is the single egress point. Everything else in the tree stays provider-neutral, and the
protected G4 material stays untouched by this phase.
"""

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "baaki"
LLM = SRC / "providers" / "llm"
EGRESS = LLM / "transport.py"
ADAPTER = LLM / "openai_provider.py"
SDKS = {"openai", "anthropic", "langchain", "litellm", "httpx", "requests", "aiohttp"}
NETWORK_MODULES = {"urllib", "socket", "http", "ssl", "ftplib", "smtplib", "telnetlib"}
CORE_LAYERS = ("contracts", "domain", "policy", "pipeline", "ledger", "db", "actions", "reconcile", "experiment")


def _imports(f: Path) -> set[str]:
    out: set[str] = set()
    for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module)
    return out


def test_no_vendor_sdk_is_imported_anywhere_in_src():
    """D-2b3-1 = stdlib: no SDK was added, so every SDK stays forbidden tree-wide."""
    for f in SRC.rglob("*.py"):
        assert not ({i.split(".")[0] for i in _imports(f)} & SDKS), f


def test_no_sdk_entered_the_dependency_manifest():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for sdk in SDKS:
        assert sdk not in pyproject, sdk


def test_exactly_one_module_may_open_a_socket():
    """Network egress is confined to the transport seam so it can be reasoned about in one place."""
    offenders = []
    for f in SRC.rglob("*.py"):
        if f == EGRESS:
            continue
        if {i.split(".")[0] for i in _imports(f)} & NETWORK_MODULES:
            offenders.append(f.relative_to(SRC).as_posix())
    assert offenders == [], offenders
    assert "urllib.request" in EGRESS.read_text(encoding="utf-8")


def test_openai_wire_format_does_not_leak_out_of_the_provider_package():
    """Endpoint, auth header and request/response shape live only in the adapter and its seam.

    `config.py` may *name* the credential — reading an environment variable is not a vendor coupling —
    but no other module may know how the request is framed.
    """
    wire = re.compile(r"api\.openai\.com|Bearer |chat/completions|response_format|json_schema\"", re.IGNORECASE)
    vendor = re.compile(r"\bopenai\b", re.IGNORECASE)
    credential_naming_allowed = {"config.py"}
    for f in SRC.rglob("*.py"):
        if f.parent == LLM:
            continue
        body = f.read_text(encoding="utf-8")
        rel = f.relative_to(SRC).as_posix()
        assert not wire.search(body), rel
        if f.name not in credential_naming_allowed:
            assert not vendor.search(body), rel


def test_core_layers_never_import_the_provider_package():
    for layer in CORE_LAYERS:
        for f in (SRC / layer).rglob("*.py"):
            for imp in _imports(f):
                assert not imp.startswith("baaki.providers.llm"), (f, imp)


def test_the_adapter_never_reaches_the_database_or_the_pipeline():
    for f in (ADAPTER, EGRESS):
        for imp in _imports(f):
            assert not imp.startswith(("baaki.db", "baaki.pipeline", "baaki.policy", "baaki.ledger", "eval")), (f, imp)


def test_the_adapter_holds_no_authority_verbs():
    """The model proposes; it must not be able to name an executor action from inside the adapter."""
    body = ADAPTER.read_text(encoding="utf-8")
    for token in ("baaki_write.", "record_policy_decision", "create_recovery_action", "INSERT ", "UPDATE "):
        assert token not in body, token


def test_no_api_key_material_is_committed():
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    key_like = re.compile(r"sk-[A-Za-z0-9_-]{20,}")
    for rel in tracked:
        p = ROOT / rel
        if not p.is_file() or p.suffix in {".png", ".jpg", ".pdf"}:
            continue
        body = p.read_text(encoding="utf-8", errors="ignore")
        assert not key_like.search(body), rel


def test_the_key_is_named_only_where_it_is_legitimately_read():
    """`OPENAI_API_KEY` may appear in config, tests and docs — never in a log line or a payload builder."""
    allowed = {"src/baaki/config.py"}
    for f in SRC.rglob("*.py"):
        rel = f.relative_to(ROOT).as_posix()
        if rel in allowed:
            continue
        assert "OPENAI_API_KEY" not in f.read_text(encoding="utf-8"), rel


def test_the_observability_record_cannot_carry_a_secret_or_a_message_body():
    from baaki.agent.observability import REDACTED_FIELDS, ProviderCallRecord

    fields = set(ProviderCallRecord.model_fields)
    assert not (fields & REDACTED_FIELDS), sorted(fields & REDACTED_FIELDS)
    assert "raw_json" not in fields and "raw_text" not in fields


# ── G4 protected-data isolation ────────────────────────────────────────────────────────────
PROTECTED_TOKENS = ("heldout", "calibration", "bank.v1", "bank.v2", "surface_seed", "heldout_touches")


def test_phase_2b3_code_never_references_protected_g4_material():
    for f in list(SRC.rglob("*.py")) + [ADAPTER, EGRESS]:
        body = f.read_text(encoding="utf-8").lower()
        for token in PROTECTED_TOKENS:
            assert token not in body, (f.relative_to(SRC).as_posix(), token)


def test_phase_2b3_tests_never_read_protected_g4_material():
    for name in ("test_openai_adapter.py", "test_openai_live_smoke.py"):
        for base in (ROOT / "tests" / "providers", ROOT / "tests" / "agent"):
            f = base / name
            if not f.exists():
                continue
            body = f.read_text(encoding="utf-8").lower()
            for token in PROTECTED_TOKENS:
                assert token not in body, (name, token)


def test_g4_calibration_state_is_untouched_by_this_phase():
    import json

    v3 = json.loads((ROOT / "eval" / "calibration.v3.json").read_text(encoding="utf-8"))
    assert v3["result"]["performed"] is False
    assert v3["provisional_comparison"]["agreement"] == 0.85
    assert v3["provisional_comparison"]["carries_calibration_credit"] is False
