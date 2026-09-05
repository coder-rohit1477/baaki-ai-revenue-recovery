"""Phase 2b-4 §4: the model credential is taken out of the environment, not merely 'not passed'."""

import pytest
from pydantic import SecretStr

from baaki.config import (
    MODEL_CREDENTIAL_KEYS,
    ModelCredentialLeak,
    assert_no_model_credential,
    take_model_credential,
)

KEY = "OPENAI_API_KEY"
VALUE = "sk-test-not-a-real-key"


def test_taking_the_credential_removes_it_from_the_environment():
    env = {KEY: VALUE, "BAAKI_APP_DSN": "postgresql://x"}
    taken = take_model_credential(env)
    assert isinstance(taken, SecretStr) and taken.get_secret_value() == VALUE
    assert KEY not in env  # the whole point: nothing downstream can read it back
    assert env["BAAKI_APP_DSN"] == "postgresql://x"


def test_taking_an_absent_credential_is_not_an_error():
    env: dict[str, str] = {}
    assert take_model_credential(env) is None  # degrades to NO_CREDENTIALS → L1, never a crash


def test_the_secret_does_not_print():
    taken = take_model_credential({KEY: VALUE})
    assert VALUE not in str(taken) and VALUE not in repr(taken)


def test_the_barrier_passes_once_the_credential_is_taken():
    env = {KEY: VALUE}
    take_model_credential(env)
    assert_no_model_credential(env)  # does not raise


def test_the_barrier_refuses_a_reachable_credential():
    with pytest.raises(ModelCredentialLeak):
        assert_no_model_credential({KEY: VALUE})


def test_an_empty_value_is_not_treated_as_a_credential():
    assert_no_model_credential({KEY: ""})


def test_the_key_set_is_exactly_the_model_credential():
    assert MODEL_CREDENTIAL_KEYS == frozenset({KEY})
