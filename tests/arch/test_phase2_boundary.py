"""K (Phase 2). Policy/rules_agent money hygiene, no model/provider/network in Phase 2 packages, opt-out monotonicity in SQL,
template seed integrity, and the §5.3 pipeline placement rule."""
import ast
import hashlib
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "baaki"
P2_PKGS = ["policy", "rules_agent", "pipeline", "ledger"]
MONEY_CTORS = {"Paise", "paise", "ClaimedPaise", "claimed_paise"}


def _files(*pkgs):
    for p in pkgs:
        yield from (SRC / p).rglob("*.py")


def test_money_constructors_never_receive_float_expressions():
    for f in _files(*P2_PKGS):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in MONEY_CTORS:
                for sub in ast.walk(node):
                    assert not (isinstance(sub, ast.Constant) and isinstance(sub.value, float)), (f, ast.unparse(node))
                    assert not (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "float"), (f, ast.unparse(node))
                    assert not (isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.Div)), (f, ast.unparse(node))


def test_no_paise_field_annotated_float_anywhere():
    for f in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id.endswith("_paise"):
                assert "float" not in ast.unparse(node.annotation), (f, node.target.id)


def test_phase2_packages_have_no_model_provider_or_network_imports():
    banned = {"openai", "anthropic", "razorpay", "httpx", "requests", "urllib", "socket", "aiohttp", "smtplib", "twilio"}
    for f in _files(*P2_PKGS):
        for node in ast.walk(ast.parse(f.read_text())):
            names = {a.name.split(".")[0] for a in node.names} if isinstance(node, ast.Import) else ({node.module.split(".")[0]} if isinstance(node, ast.ImportFrom) and node.module else set())
            assert not names & banned, (f, names)


def test_no_scheduler_loop_or_executor_in_pipeline():
    src = (SRC / "pipeline" / "run.py").read_text()
    for tok in ("while True", "sleep(", "import schedule", "cron", "transition_recovery_action", "razorpay", "send(", "httpx", "requests"):
        assert tok not in src, tok


def test_opt_out_is_monotonic_in_sql_no_clearing_path():
    sql = "\n".join(p.read_text() for p in (ROOT / "migrations" / "versions").glob("*.py"))
    assert not re.search(r"opted_out\s*=\s*false", sql, re.I)
    assert not re.search(r"opt_out\s*=\s*false", sql, re.I)
    assert "opt_out_contact_from_evidence" in sql and "opt_out_by_operator" in sql
    assert "session_user <> 'baaki_ops'" in sql  # H17 first-statement check in W12


def test_w12_requires_actor_note_and_has_no_role_parameter():
    src = (ROOT / "migrations" / "versions" / "0005_opt_out_writers.py").read_text()
    assert "p_actor_note text" in src and "p_role" not in src and "p_actor_id" not in src and "current_setting" not in src


def test_policy_toml_is_the_only_constant_source_and_matches_docs_hash_rule():
    data = (ROOT / "config" / "policy.v1.toml").read_bytes()
    parsed = tomllib.loads(data.decode())
    assert parsed["policy_version"] == "policy.v1"
    from baaki.policy.ruleset import DEFAULT_RULESET_PATH, load_ruleset
    assert load_ruleset(DEFAULT_RULESET_PATH).policy_hash == hashlib.sha256(data).hexdigest()
    # no hard-coded duplicates of locked constants in policy code (caps/quiet/TTL must flow from the ruleset)
    for f in _files("policy", "rules_agent"):
        if f.name == "ruleset.py":
            continue
        src = f.read_text()
        for needle in ("contact_cap_account_7d = 3", "= 72", "timedelta(hours=72)", "timedelta(hours=24)", "[3, 7, 15]", "time(9", "time(19"):
            assert needle not in src, (f, needle)


def test_template_seed_files_exist_for_every_seeded_id():
    spec = (ROOT / "migrations" / "versions" / "0006_seed_templates.py").read_text()
    ids = re.findall(r'\("(tpl\.[a-z.]+\.v1)"', spec)
    assert len(ids) == 6
    for tid in ids:
        assert (ROOT / "config" / "templates" / f"{tid}.txt").is_file(), tid


def test_pipeline_is_the_only_module_importing_both_decision_and_action_writers():
    both = []
    for f in SRC.rglob("*.py"):
        imps = {n.module for n in ast.walk(ast.parse(f.read_text())) if isinstance(n, ast.ImportFrom) and n.module}
        if "baaki.db.writers.decision" in imps and "baaki.db.writers.action_auto" in imps:
            both.append(f.relative_to(SRC).as_posix())
    assert both == ["pipeline/run.py"]


def test_validator_never_unwraps_raw_response_and_kernel_never_sees_proposals():
    v = (SRC / "policy" / "validate" / "ladder.py").read_text()
    assert "unwrap_for_audit" not in v and "raw_response" not in v
    for f in (SRC / "policy" / "kernel").glob("*.py"):
        src = f.read_text()
        assert "AgentProposal" not in src and "raw_response" not in src and "rationale" not in src
