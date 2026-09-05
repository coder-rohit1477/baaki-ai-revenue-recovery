"""I5a (mandatory, never skipped): the generator is a pure function of its seeds, bank and templates.

This runs in any clone. It uses a committed synthetic bank that contains no protected surface material, so
the determinism of newly generated material is gated everywhere — not only where the protected bank happens
to be present. I5b, reproducing the *protected* corpus byte-for-byte, remains an authoring-environment
check and is documented as such; it is never the determinism gate.
"""

import json
from pathlib import Path

import pytest
from eval.gen.channels import named_channel
from eval.gen.generate import ambiguous_items, generate_extension, generate_scored, load_templates, project

ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC = ROOT / "eval" / "gen" / "fixtures" / "synthetic_bank.json"
SEED_A = "deterministic-test-seed-a"
SEED_B = "deterministic-test-seed-b"


@pytest.fixture(scope="module")
def bank():
    return json.loads(SYNTHETIC.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def templates():
    return load_templates()


def test_the_synthetic_fixture_is_committed_and_carries_no_protected_material():
    assert SYNTHETIC.exists(), "I5a must not depend on protected material"
    body = SYNTHETIC.read_text(encoding="utf-8")
    assert "synthetic" in body and len(body) < 200_000


def test_scored_generation_is_byte_identical_for_the_same_seeds(bank, templates):
    ss = templates["structure_seed"]
    a_in, a_ans = project(generate_scored(ss, SEED_A, bank=bank, templates=templates))
    b_in, b_ans = project(generate_scored(ss, SEED_A, bank=bank, templates=templates))
    assert json.dumps(a_in, sort_keys=True) == json.dumps(b_in, sort_keys=True)
    assert json.dumps(a_ans, sort_keys=True) == json.dumps(b_ans, sort_keys=True)


def test_extension_generation_is_byte_identical_for_the_same_seeds(bank, templates):
    ss = templates["structure_seed"]
    a, _ = project(generate_extension(ss, SEED_A, bank=bank, templates=templates))
    b, _ = project(generate_extension(ss, SEED_A, bank=bank, templates=templates))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_a_different_surface_seed_produces_different_material(bank, templates):
    ss = templates["structure_seed"]
    a, _ = project(generate_scored(ss, SEED_A, bank=bank, templates=templates))
    b, _ = project(generate_scored(ss, SEED_B, bank=bank, templates=templates))
    assert json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True)


def test_replacement_generation_is_reproducible(bank, templates):
    """Newly generated replacement material is covered by the same non-skipping gate."""
    import random

    plan = templates["scored"]
    need = {"en": 3, "hi-Latn": 3, "mixed": 2, "hi-Deva": 0}
    from eval.gen.generate import _optout_items
    from eval.profiles import load_profiles

    profiles = load_profiles()
    a = _optout_items(random.Random("r"), bank, templates, plan, need, profiles, bank["bank_version"])
    b = _optout_items(random.Random("r"), bank, templates, plan, need, profiles, bank["bank_version"])
    assert a == b and len(a) == 8


def test_ambiguous_generation_is_reproducible_and_never_positive(bank, templates):
    import random

    plan = templates["scored"]
    per = {"en": 2, "hi-Latn": 1, "mixed": 1, "hi-Deva": 0}
    a = ambiguous_items(random.Random("z"), bank, templates, plan, per, bank["bank_version"])
    b = ambiguous_items(random.Random("z"), bank, templates, plan, per, bank["bank_version"])
    assert a == b and len(a) == 4
    for row in a:
        assert row["semantic"] == {"primary_intent": "NO_CLEAR_INTENT", "opt_out_scope": "AMBIGUOUS"}
        assert row["register"] in ("whatsapp", "transcript")


def test_a_channel_restricting_core_cannot_reach_the_general_pool(bank, templates):
    """The v1 defect is unconstructible, not merely absent: the generator refuses at runtime."""
    poisoned = json.loads(json.dumps(bank))
    # inserted at the head so it falls inside the scored half of the core slice
    poisoned["optout_general"]["en"].insert(0, "stop sending me these follow-up emails")
    assert named_channel("stop sending me these follow-up emails") == "EMAIL"
    with pytest.raises(ValueError, match="channel-restricting core reached the general pool"):
        generate_scored(templates["structure_seed"], SEED_A, bank=poisoned, templates=templates)


def test_a_mismatched_channel_pool_core_is_refused(bank, templates):
    poisoned = json.loads(json.dumps(bank))
    poisoned["optout_channel"]["sms"]["en"] = ["stop emailing this address"]
    with pytest.raises(ValueError, match="does not scope to its own channel"):
        generate_scored(templates["structure_seed"], SEED_A, bank=poisoned, templates=templates)
