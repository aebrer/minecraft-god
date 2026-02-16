"""Tests for Herald trigger and cooldown logic.

The Herald only speaks when directly addressed by keyword ("herald", "bard")
and enforces a 60-second cooldown between messages. These tests verify
the decision logic without any LLM calls.
"""

import time as time_module
from unittest.mock import patch

from server.herald_god import HeraldGod, HERALD_COOLDOWN


# ---------------------------------------------------------------------------
# should_act — keyword detection
# ---------------------------------------------------------------------------


def test_responds_to_herald_keyword():
    god = HeraldGod()
    summary = 'CHAT:\n  [PLAYER CHAT] Steve: "hey herald, how do I find diamonds?"'
    assert god.should_act(summary) is True


def test_responds_to_bard_keyword():
    god = HeraldGod()
    summary = 'CHAT:\n  [PLAYER CHAT] Steve: "bard, tell me about the nether"'
    assert god.should_act(summary) is True


def test_ignores_without_keyword():
    god = HeraldGod()
    summary = 'CHAT:\n  [PLAYER CHAT] Steve: "hello everyone"'
    assert god.should_act(summary) is False


def test_ignores_keyword_without_chat():
    """The keyword must appear in a chat context, not just anywhere."""
    god = HeraldGod()
    # "herald" in a non-chat section shouldn't trigger
    summary = 'WEATHER: Changed to Rain\nMINING ACTIVITY:\n  herald_fan: 5 stone'
    assert god.should_act(summary) is False


def test_ignores_no_summary():
    god = HeraldGod()
    assert god.should_act(None) is False


def test_ignores_empty_summary():
    god = HeraldGod()
    assert god.should_act("") is False


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------


def test_cooldown_blocks_rapid_calls():
    god = HeraldGod()
    summary = 'CHAT:\n  [PLAYER CHAT] Steve: "herald help"'

    # Simulate having spoken just now
    god._last_spoke = time_module.time()
    assert god.should_act(summary) is False


def test_cooldown_expires():
    god = HeraldGod()
    summary = 'CHAT:\n  [PLAYER CHAT] Steve: "herald help"'

    # Simulate having spoken long ago
    god._last_spoke = time_module.time() - HERALD_COOLDOWN - 1
    assert god.should_act(summary) is True


def test_cooldown_exactly_at_boundary():
    """At exactly HERALD_COOLDOWN seconds, cooldown has expired (code uses <)."""
    god = HeraldGod()
    summary = 'CHAT:\n  [PLAYER CHAT] Steve: "herald help"'

    now = time_module.time()
    god._last_spoke = now - HERALD_COOLDOWN
    with patch.object(time_module, "time", return_value=now):
        # elapsed == HERALD_COOLDOWN, check is `< HERALD_COOLDOWN`, so cooldown is over
        assert god.should_act(summary) is True


def test_fresh_herald_has_no_cooldown():
    """A newly created Herald has _last_spoke=0, so no cooldown."""
    god = HeraldGod()
    summary = 'CHAT:\n  [PLAYER CHAT] Steve: "herald, what should I do?"'
    assert god.should_act(summary) is True
