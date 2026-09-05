"""The one-command launcher must not become a way to smuggle credentials or relax the config boundary.

Design constraint worth stating: the launcher loads credentials in the SHELL and exports them into the
child process. No Python reads a dotenv file, so `Settings` keeps `env_file=None` and the runtime still
sees only `os.environ` — the launcher changes how the environment is populated, never who may read it.
"""

import os
import pathlib
import re
import stat
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "start-demo.sh"
BODY = SCRIPT.read_text(encoding="utf-8")


def test_the_launcher_exists_and_is_executable():
    assert SCRIPT.is_file()
    assert SCRIPT.stat().st_mode & stat.S_IXUSR, "start-demo.sh must be chmod +x"


def test_the_launcher_carries_no_credential_material():
    assert not re.search(r"sk-[A-Za-z0-9_-]{20,}|rzp_(test|live)_[A-Za-z0-9]{6,}", BODY)


def test_the_launcher_reads_env_local_not_a_root_dotenv():
    """A committed architecture test asserts no `.env` exists in the repository root."""
    assert ".env.local" in BODY
    assert 'ENV_FILE="${BAAKI_ENV_FILE:-.env.local}"' in BODY


def test_the_launcher_does_not_source_the_env_file():
    """`source`-ing a credentials file would execute whatever is in it."""
    assert not re.search(r"^\s*(source|\.)\s+[\"']?\$?\{?ENV_FILE", BODY, re.M)


def test_secrets_are_masked_before_being_printed():
    assert "mask()" in BODY
    assert 'sed -E \'s#://[^@]*@#://***@#\'' in BODY  # the DSN password never reaches the terminal


def test_the_launcher_refuses_a_live_razorpay_key():
    assert "rzp_test_*)" in BODY and "Live mode is refused" in BODY


def test_env_local_is_gitignored():
    ignored = subprocess.run(["git", "check-ignore", ".env.local"], cwd=ROOT,
                             capture_output=True, text=True).returncode
    assert ignored == 0, ".env.local must be gitignored"


def test_no_env_file_is_tracked_except_the_example():
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    assert [t for t in tracked if t.startswith(".env")] == [".env.example"]


def test_the_application_still_reads_only_the_environment():
    """The security boundary the launcher must not weaken."""
    from baaki.config import Settings

    assert Settings.model_config.get("env_file") is None


def test_the_launcher_runs_and_validates_without_credentials(tmp_path):
    """No credentials is a warning, not a failure: the demo still runs on the deterministic path."""
    stub = tmp_path / "sd.sh"
    stub.write_text(re.sub(r"^exec uv run.*$", "echo LAUNCH_OK", BODY, flags=re.M), encoding="utf-8")
    stub.chmod(0o755)
    env = {k: v for k, v in os.environ.items()
           if k not in ("OPENAI_API_KEY", "RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET")}
    env["BAAKI_ENV_FILE"] = str(tmp_path / "absent")
    r = subprocess.run([str(stub)], cwd=ROOT, capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0 and "LAUNCH_OK" in r.stdout


def test_the_launcher_rejects_a_live_key_at_startup(tmp_path):
    stub = tmp_path / "sd.sh"
    stub.write_text(re.sub(r"^exec uv run.*$", "echo LAUNCH_OK", BODY, flags=re.M), encoding="utf-8")
    stub.chmod(0o755)
    envfile = tmp_path / "live.env"
    envfile.write_text("RAZORPAY_KEY_ID=rzp_live_x\nRAZORPAY_KEY_SECRET=y\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("RAZORPAY_")}
    env["BAAKI_ENV_FILE"] = str(envfile)
    r = subprocess.run([str(stub)], cwd=ROOT, capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode != 0 and "LAUNCH_OK" not in r.stdout
