"""P1: the channel classifier that gates the G4 v2 bank. Its two failure modes are pinned explicitly."""

import pytest
from eval.gen.channels import is_general_safe, named_channel, resolve_scope


@pytest.mark.parametrize(
    "core,expected",
    [
        # medium named directly
        ("stop sending me these follow-up emails", "EMAIL"),
        ("stop emailing this address", "EMAIL"),
        ("ye sab mails mat bhejo", "EMAIL"),
        ("इस पते पर मेल मत भेजिए", "EMAIL"),
        ("इस ईमेल पर संदेश बंद कीजिए", "EMAIL"),
        ("stop sending me any more texts", "SMS"),
        ("ye SMS band karo", "SMS"),
        ("इस नंबर पर एसएमएस मत भेजिए", "SMS"),
        ("no more WhatsApp messages", "WHATSAPP"),
        ("व्हाट्सएप पर मत भेजिए", "WHATSAPP"),
        ("do not call this number again", "VOICE"),
        # medium beats a number mentioned in the same phrase
        ("do not send SMS to this number", "SMS"),
        ("is number pe SMS mat bhejo", "SMS"),
        ("text mat karo is number pe", "SMS"),
        # a bare number restricts, but names no medium
        ("Please stop, is number pe mat likhna", "NUMBER"),
        ("is number pe message mat karna", "NUMBER"),
        ("इस नंबर पर संदेश बंद कीजिए", "NUMBER"),
        ("Cease all follow-up messages to this number from today.", "NUMBER"),
        ("do not reach me on this number any more", "NUMBER"),
        ("I do not want anything on this number any more", "NUMBER"),
        # a number or mailing as the object of removal restricts nothing
        ("mera number list se nikal dijiye", None),
        ("take this number off whatever list you are using", None),
        ("remove me from all future mailings", None),
        ("stop sending me these payment reminders", None),
        ("मुझे सूची से हटा दीजिए", None),
    ],
)
def test_named_channel(core, expected):
    assert named_channel(core) == expected


def test_the_two_defects_that_broke_the_first_classifier():
    """Latin-only matching and number-over-medium precedence: both are now pinned."""
    assert named_channel("stop emailing this address") == "EMAIL"  # verb form, not "email"/"mails"
    assert named_channel("इस नंबर पर एसएमएस मत भेजिए") == "SMS"  # Devanagari medium beats Devanagari number
    assert named_channel("do not send SMS to this number") == "SMS"  # medium beats number


@pytest.mark.parametrize(
    "named,register,expected",
    [
        ("EMAIL", "email", "CHANNEL_INBOUND"),
        ("EMAIL", "sms", "CHANNEL_OTHER"),
        ("SMS", "sms", "CHANNEL_INBOUND"),
        ("SMS", "whatsapp", "CHANNEL_OTHER"),
        ("WHATSAPP", "whatsapp", "CHANNEL_INBOUND"),
        ("NUMBER", "sms", "CHANNEL_INBOUND"),
        ("NUMBER", "email", "CHANNEL_OTHER"),
        ("NUMBER", "whatsapp", "AMBIGUOUS"),
        ("NUMBER", "transcript", "AMBIGUOUS"),
    ],
)
def test_resolve_scope_matches_the_adjudicated_table(named, register, expected):
    assert resolve_scope(named, register) == expected


def test_is_general_safe_is_the_negation_of_naming_a_channel():
    assert is_general_safe("stop sending me these payment reminders")
    assert not is_general_safe("ye SMS band karo")
