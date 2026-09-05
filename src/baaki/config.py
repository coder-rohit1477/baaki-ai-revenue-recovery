"""Runtime settings (ARCHITECTURE.md §6.2, H16, Appendix C).

The application process may hold ONLY runtime credentials. If any bootstrap / migration /
operator credential is present in the runtime environment, startup is refused.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping
from typing import Final

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

FORBIDDEN_RUNTIME_KEYS: Final[frozenset[str]] = frozenset(
    {
        "BAAKI_OWNER_DSN",
        "BAAKI_MIGRATE_DSN",
        "BAAKI_OPS_DSN",
        "BAAKI_SUPERUSER_DSN",
        "BAAKI_WEBHOOK_SECRET",
    }
)

MODEL_CREDENTIAL_KEYS: Final[frozenset[str]] = frozenset({"OPENAI_API_KEY"})
"""Credentials the agent leg may hold and the pipeline leg may not (PHASE2B_PLAN §12).

Not in FORBIDDEN_RUNTIME_KEYS: those are never legitimate in any runtime process, whereas the model
credential is legitimate in exactly one leg. The separation is enforced by taking the key out of the
environment (`take_model_credential`) and asserting its absence (`assert_no_model_credential`).
"""

RAZORPAY_TEST_PREFIX: Final[str] = "rzp_test_"


class RuntimeCredentialLeak(RuntimeError):
    """A non-runtime credential was found in the application's environment."""


class ModelCredentialLeak(RuntimeError):
    """A model-provider credential is reachable in a leg that must not be able to use one."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", frozen=True)

    baaki_app_dsn: str = Field(min_length=1)
    baaki_agent_dsn: str = Field(min_length=1)
    baaki_sim_dsn: str = Field(min_length=1)
    razorpay_key_id: str | None = None
    # Phase 2b-3: a legitimate runtime credential, unlike the operator DSNs above. SecretStr so no
    # repr, log line or traceback can print it. Absence is not an error: the workflow degrades to
    # the deterministic rules path (ProviderStatus.NO_CREDENTIALS).
    openai_api_key: SecretStr | None = None

    @field_validator("razorpay_key_id")
    @classmethod
    def _test_mode_only(cls, v: str | None) -> str | None:
        # §9.2: live mode is an abort, not a configuration flag.
        if v is not None and not v.startswith(RAZORPAY_TEST_PREFIX):
            raise ValueError("RAZORPAY_KEY_ID must be a test-mode key (rzp_test_...)")
        return v


def assert_no_privileged_credentials(environ: Mapping[str, str]) -> None:
    leaked = sorted(k for k in FORBIDDEN_RUNTIME_KEYS if k in environ)
    if leaked:
        raise RuntimeCredentialLeak(
            "privileged credentials present in runtime environment: " + ", ".join(leaked)
        )


def take_model_credential(environ: MutableMapping[str, str] | None = None) -> SecretStr | None:
    """Read the model credential into a SecretStr and REMOVE it from the environment.

    After this call the process can no longer hand the key to a child process, a library that reads the
    environment, or the pipeline leg — the only remaining reference is the returned SecretStr, which the
    caller gives to the provider and drops. Absence returns None: the workflow degrades to the
    deterministic rules path (NO_CREDENTIALS), it does not fail.
    """
    env = os.environ if environ is None else environ
    taken: SecretStr | None = None
    for key in sorted(MODEL_CREDENTIAL_KEYS):
        raw = env.pop(key, None)
        if raw:
            taken = SecretStr(raw)
    return taken


def assert_no_model_credential(environ: Mapping[str, str] | None = None) -> None:
    """Refuse to run a pipeline leg while a model credential is still reachable from the environment."""
    env = os.environ if environ is None else environ
    leaked = sorted(k for k in MODEL_CREDENTIAL_KEYS if env.get(k))
    if leaked:
        raise ModelCredentialLeak("model credential reachable in the pipeline leg: " + ", ".join(leaked))


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    env = dict(os.environ if environ is None else environ)
    assert_no_privileged_credentials(env)
    return Settings(
        baaki_app_dsn=env.get("BAAKI_APP_DSN", ""),
        baaki_agent_dsn=env.get("BAAKI_AGENT_DSN", ""),
        baaki_sim_dsn=env.get("BAAKI_SIM_DSN", ""),
        razorpay_key_id=env.get("RAZORPAY_KEY_ID"),
        openai_api_key=SecretStr(env["OPENAI_API_KEY"]) if env.get("OPENAI_API_KEY") else None,
    )
