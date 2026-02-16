"""Tests for EventBuffer, focusing on player status staleness logic.

The staleness check prevents LLM calls to an empty server: the Java plugin
sends status beacons every ~30s while players are online and stops when the
server is empty.  Without the staleness timeout, cached status persists
indefinitely and the gods keep calling the LLM with stale "player online" data.
"""

import time
from unittest.mock import patch

from server.events import EventBuffer


# ---------------------------------------------------------------------------
# Player status staleness
# ---------------------------------------------------------------------------


def test_no_status_returns_none():
    """Before any status beacon arrives, get_player_status returns None."""
    buf = EventBuffer()
    assert buf.get_player_status() is None


def test_fresh_status_returns_data():
    """A recently received status beacon is returned as-is."""
    buf = EventBuffer()
    status = {"type": "player_status", "players": [{"name": "aeBRER"}]}
    buf.add(status)
    assert buf.get_player_status() == status


def test_stale_status_returns_none():
    """Status older than _STATUS_STALE_SECONDS is treated as 'no one online'."""
    buf = EventBuffer()
    status = {"type": "player_status", "players": [{"name": "aeBRER"}]}

    with patch.object(time, "time", return_value=1000.0):
        buf.add(status)

    # Jump forward past the staleness threshold
    stale_time = 1000.0 + EventBuffer._STATUS_STALE_SECONDS + 1
    with patch.object(time, "time", return_value=stale_time):
        assert buf.get_player_status() is None


def test_status_refreshed_after_stale():
    """A new beacon after staleness makes get_player_status return data again."""
    buf = EventBuffer()
    old_status = {"type": "player_status", "players": [{"name": "aeBRER"}]}

    with patch.object(time, "time", return_value=1000.0):
        buf.add(old_status)

    # Go stale
    stale_time = 1000.0 + EventBuffer._STATUS_STALE_SECONDS + 1
    with patch.object(time, "time", return_value=stale_time):
        assert buf.get_player_status() is None

    # New beacon arrives
    new_status = {"type": "player_status", "players": [{"name": "aeBRER"}, {"name": "Steve"}]}
    with patch.object(time, "time", return_value=stale_time + 1):
        buf.add(new_status)

    with patch.object(time, "time", return_value=stale_time + 2):
        result = buf.get_player_status()
        assert result == new_status
        assert len(result["players"]) == 2


def test_status_exactly_at_threshold_is_not_stale():
    """At exactly _STATUS_STALE_SECONDS, status is still valid (uses >)."""
    buf = EventBuffer()
    status = {"type": "player_status", "players": [{"name": "aeBRER"}]}

    with patch.object(time, "time", return_value=1000.0):
        buf.add(status)

    at_threshold = 1000.0 + EventBuffer._STATUS_STALE_SECONDS
    with patch.object(time, "time", return_value=at_threshold):
        assert buf.get_player_status() is not None


# ---------------------------------------------------------------------------
# Event buffering basics
# ---------------------------------------------------------------------------


def test_status_not_buffered_as_event():
    """player_status events update the cached status but don't go into the event list."""
    buf = EventBuffer()
    buf.add({"type": "player_status", "players": []})
    buf.add({"type": "weather_change", "newWeather": "Rain"})

    summary = buf.drain_and_summarize()
    assert summary is not None
    assert "Rain" in summary
    # player_status should not appear as a regular event
    assert "player_status" not in summary


def test_drain_returns_none_when_empty():
    """drain_and_summarize returns None when no events have been buffered."""
    buf = EventBuffer()
    assert buf.drain_and_summarize() is None


def test_drain_clears_events():
    """After draining, the buffer is empty."""
    buf = EventBuffer()
    buf.add({"type": "weather_change", "newWeather": "Clear"})
    buf.drain_and_summarize()
    assert buf.drain_and_summarize() is None


def test_drain_preserves_player_status():
    """Draining events does NOT clear the cached player status."""
    buf = EventBuffer()
    status = {"type": "player_status", "players": [{"name": "aeBRER"}]}
    buf.add(status)
    buf.add({"type": "weather_change", "newWeather": "Rain"})

    buf.drain_and_summarize()

    # Status should still be available (it's not an event, it's cached state)
    assert buf.get_player_status() == status


# ---------------------------------------------------------------------------
# get_recent_chat
# ---------------------------------------------------------------------------


def test_get_recent_chat_returns_only_chat_events():
    """Non-chat events are excluded from recent chat."""
    buf = EventBuffer()
    buf.add({"type": "weather_change", "newWeather": "Rain"})
    buf.add({"type": "chat", "player": "Steve", "message": "hello"})
    buf.add({"type": "player_join", "player": "Alex"})
    chats = buf.get_recent_chat()
    assert len(chats) == 1
    assert chats[0]["message"] == "hello"


def test_get_recent_chat_respects_limit():
    """Only the last N chat events are returned."""
    buf = EventBuffer()
    for i in range(15):
        buf.add({"type": "chat", "player": "Steve", "message": f"msg {i}"})
    chats = buf.get_recent_chat(limit=5)
    assert len(chats) == 5
    assert chats[0]["message"] == "msg 10"
    assert chats[-1]["message"] == "msg 14"


def test_get_recent_chat_is_non_destructive():
    """Getting recent chat does not remove events from the buffer."""
    buf = EventBuffer()
    buf.add({"type": "chat", "player": "Steve", "message": "hello"})
    buf.get_recent_chat()
    summary = buf.drain_and_summarize()
    assert "hello" in summary
