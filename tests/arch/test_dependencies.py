"""K. Direct dependencies equal the approved plan; forbidden deps absent; lockfile pins uuid6; config refuses privileged credentials."""
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APPROVED_RUNTIME = {"pydantic", "pydantic-settings", "sqlalchemy", "alembic", "psycopg", "uuid6"}
APPROVED_DEV = {"pytest", "hypothesis", "mypy", "ruff"}
FORBIDDEN = {"openai", "anthropic", "razorpay", "fastapi", "uvicorn", "httpx", "requests", "langchain", "chromadb", "testcontainers"}


def _name(spec: str) -> str:
    return spec.split("[")[0].split(">")[0].split("<")[0].split("=")[0].strip().lower()


def test_direct_dependencies_exact():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    runtime = {_name(s) for s in data["project"]["dependencies"]}
    dev = {_name(s) for s in data["dependency-groups"]["dev"]}
    assert runtime == APPROVED_RUNTIME and dev == APPROVED_DEV
    assert not ((runtime | dev) & FORBIDDEN)


def test_lockfile_pins_uuid6_with_hash():
    lock = (ROOT / "uv.lock").read_text()
    assert 'name = "uuid6"' in lock and "sha256:" in lock
    for bad in FORBIDDEN:
        assert f'name = "{bad}"' not in lock


def test_lock_is_current():
    subprocess.run(["uv", "lock", "--check"], cwd=ROOT, check=True, capture_output=True)


def test_settings_refuse_privileged_credentials():
    from baaki.config import RuntimeCredentialLeak, load_settings
    good = {"BAAKI_APP_DSN": "x", "BAAKI_AGENT_DSN": "y", "BAAKI_SIM_DSN": "z", "RAZORPAY_KEY_ID": "rzp_test_abc"}
    load_settings(good)
    for key in ("BAAKI_OWNER_DSN", "BAAKI_MIGRATE_DSN", "BAAKI_OPS_DSN", "BAAKI_SUPERUSER_DSN", "BAAKI_WEBHOOK_SECRET"):
        with pytest.raises(RuntimeCredentialLeak):
            load_settings({**good, key: "leak"})
    with pytest.raises(Exception):
        load_settings({**good, "RAZORPAY_KEY_ID": "rzp_live_abc"})


def test_env_example_runtime_section_has_placeholders_only():
    text = (ROOT / ".env.example").read_text()
    runtime = text.split("NON-RUNTIME")[0]
    for line in runtime.splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            assert v.startswith("<") and v.endswith(">"), line
            assert k not in ("BAAKI_MIGRATE_DSN", "BAAKI_OWNER_DSN", "BAAKI_OPS_DSN", "BAAKI_WEBHOOK_SECRET", "BAAKI_SUPERUSER_DSN")
    assert not (ROOT / ".env").exists()
    assert ".env" in (ROOT / ".gitignore").read_text()


def test_no_known_credential_formats_in_tree():
    import re
    pat = re.compile(r"rzp_(test|live)_[A-Za-z0-9]{6,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN|ghp_[A-Za-z0-9]{20,}")
    for f in list(ROOT.rglob("*.py")) + list(ROOT.rglob("*.toml")) + list(ROOT.rglob("*.sql")) + list(ROOT.rglob("*.md")) + [ROOT / ".env.example"]:
        if ".venv" in f.parts or f.name == "test_dependencies.py":
            continue
        assert not pat.search(f.read_text(errors="ignore")), f
