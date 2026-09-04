"""Runtime settings (ARCHITECTURE.md §6.2, H16, Appendix C).

The application process may hold ONLY runtime credentials. If any bootstrap / migration /
operator credential is present in the runtime environment, startup is refused.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Final

from pydantic import Field, field_validator
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

RAZORPAY_TEST_PREFIX: Final[str] = "rzp_test_"


class RuntimeCredentialLeak(RuntimeError):
    """A non-runtime credential was found in the application's environment."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", frozen=True)

    baaki_app_dsn: str = Field(min_length=1)
    baaki_agent_dsn: str = Field(min_length=1)
    baaki_sim_dsn: str = Field(min_length=1)
    razorpay_key_id: str | None = None

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


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    env = dict(os.environ if environ is None else environ)
    assert_no_privileged_credentials(env)
    return Settings(
        baaki_app_dsn=env.get("BAAKI_APP_DSN", ""),
        baaki_agent_dsn=env.get("BAAKI_AGENT_DSN", ""),
        baaki_sim_dsn=env.get("BAAKI_SIM_DSN", ""),
        razorpay_key_id=env.get("RAZORPAY_KEY_ID"),
    )
