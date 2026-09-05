"""How a rejected proposal is presented. Deterministic behaviour is unchanged; only the rendering is.

The adversarial scenario legitimately produces `interpretation: null` — the model's output carried forged
money fields, failed A3/A4, and was never parsed. Rendering that as a table of em-dashes read as "the AI is
broken" rather than "the AI was stopped". These tests pin the honest empty state, and pin the rule that no
field is ever invented to fill it.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
PAGE = (ROOT / "demo" / "static" / "index.html").read_text(encoding="utf-8")
STORE = (ROOT / "demo" / "store.py").read_text(encoding="utf-8")
FLAT = " ".join(PAGE.split())


# ── the rejected empty state ─────────────────────────────────────────────────────────────


def test_a_rejected_proposal_explains_itself_instead_of_showing_dashes():
    assert "AI proposal rejected before execution" in FLAT
    assert "out-of-policy financial proposal" in FLAT
    assert "before any financial or communication action could occur" in FLAT


def test_the_empty_state_says_why_no_interpretation_is_shown():
    assert "no structured interpretation" in FLAT
    assert "nothing the model returned was accepted" in FLAT


def test_the_interpretation_fields_render_only_when_an_intent_actually_came_back():
    """The guard that stops the dash-table: no intent means no field table at all."""
    assert "${i.intent ? `<div class=\"pb\">" in PAGE


def test_a_missing_interpretation_is_not_backfilled_with_invented_values():
    """The empty state must not manufacture an intent, confidence or evidence."""
    block = PAGE.split("function noInterpretation(r){")[1].split("\nfunction ")[0]
    for invented in ("confidence:", "WILL_PAY_ON_DATE", "promised_amount", "promised_date", "evidence:"):
        assert invented not in block, invented
    # it may only echo values the response actually carried
    assert "r.parse_status" in block and "r.rejection_reasons" in block


def test_a_non_schema_failure_gets_its_own_truthful_wording():
    """A provider timeout is not an out-of-policy proposal and must not be described as one."""
    block = PAGE.split("function noInterpretation(r){")[1].split("\nfunction ")[0]
    assert "No interpretation available" in block
    assert "continued on the deterministic rules path" in block


# ── provenance: a fallback action must not read as an accepted model proposal ─────────────


def test_the_ui_labels_where_the_action_came_from():
    assert "function provenance(level)" in PAGE
    assert "Deterministic fallback" in FLAT
    assert "Model-assisted" in FLAT


def test_the_decision_pane_shows_provenance_next_to_the_action():
    pane = PAGE.split('<div class="pane pol">')[1].split("</div></div>")[0]
    assert "provenance(r.degradation_level)" in pane


def test_the_activity_feed_shows_provenance_for_a_decision():
    feed = PAGE.split("function renderFeed(){")[1].split("\nfunction ")[0]
    assert "provenance(x.level)" in feed


def test_the_audit_timeline_says_which_path_chose_the_action():
    assert "chosen by the deterministic rules path" in STORE
    assert "from the model proposal" in STORE


def test_the_timeline_does_not_call_a_rejected_output_an_interpretation():
    assert "AI response was not usable" in STORE
    assert "AI proposal was not usable" in STORE
    # the accepted wording is still there for the cases that really were accepted
    assert "AI interpreted response" in STORE


def test_no_historical_event_is_filtered_out_of_the_timeline():
    """Provenance is added to the label; the event set itself is untouched."""
    timeline = STORE.split("def activity_timeline(")[1]
    assert "WHERE" not in timeline.split("FROM baaki.policy_decision")[1].split("UNION ALL")[0]


# ── the deterministic story itself is unchanged ──────────────────────────────────────────


def test_the_rejection_claims_are_still_present_and_unchanged():
    assert "Financial state: UNCHANGED" in FLAT
    assert "External delivery: NOT SENT" in FLAT
    assert "deterministic policy tree" in FLAT
    assert re.search(r"rejected the whole proposal as", FLAT)
