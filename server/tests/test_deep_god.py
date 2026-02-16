"""Tests for Deep God trigger logic.

The Deep God activates based on player position (depth, Nether),
mining activity (deep ores), and the Kind God's action count.
These tests verify the trigger decision tree without any LLM calls.
"""

from unittest.mock import patch

from server.config import KIND_GOD_ACTION_THRESHOLD
from server.deep_god import DeepGod, DEEP_ORES


def _player_status(name: str = "Steve", y: int = 64, dimension: str = "overworld") -> dict:
    return {
        "players": [{
            "name": name,
            "location": {"x": 0, "y": y, "z": 0},
            "dimension": dimension,
        }]
    }


# ---------------------------------------------------------------------------
# Forced trigger: Kind God action threshold
# ---------------------------------------------------------------------------


def test_forced_trigger_at_threshold():
    god = DeepGod()
    assert god.should_act(None, None, kind_god_action_count=KIND_GOD_ACTION_THRESHOLD) is True


def test_forced_trigger_above_threshold():
    god = DeepGod()
    assert god.should_act(None, None, kind_god_action_count=KIND_GOD_ACTION_THRESHOLD + 5) is True


def test_no_trigger_below_threshold_without_players():
    god = DeepGod()
    assert god.should_act(None, None, kind_god_action_count=0) is False


# ---------------------------------------------------------------------------
# Position-based triggers (with deterministic random)
# ---------------------------------------------------------------------------


def test_deep_player_triggers(monkeypatch):
    """Player below Y=0 has 70% chance — should trigger when random returns low."""
    god = DeepGod()
    monkeypatch.setattr("server.deep_god.random.random", lambda: 0.1)
    result = god.should_act("some events", _player_status(y=-10), kind_god_action_count=0)
    assert result is True


def test_deep_player_no_trigger_high_roll(monkeypatch):
    """Player below Y=0 but random returns above 70% — no trigger."""
    god = DeepGod()
    monkeypatch.setattr("server.deep_god.random.random", lambda: 0.8)
    result = god.should_act("some events", _player_status(y=-10), kind_god_action_count=0)
    assert result is False


def test_nether_triggers(monkeypatch):
    """Player in Nether has 50% chance."""
    god = DeepGod()
    monkeypatch.setattr("server.deep_god.random.random", lambda: 0.3)
    result = god.should_act("some events", _player_status(dimension="the_nether"), kind_god_action_count=0)
    assert result is True


def test_nether_no_trigger_high_roll(monkeypatch):
    god = DeepGod()
    monkeypatch.setattr("server.deep_god.random.random", lambda: 0.6)
    result = god.should_act("some events", _player_status(dimension="the_nether"), kind_god_action_count=0)
    assert result is False


def test_underground_has_base_chance(monkeypatch):
    """Player at Y=20 (underground) has at least the base random chance."""
    god = DeepGod()
    # Underground effective chance is 15% (night/underground baseline, see deep_god.py)
    monkeypatch.setattr("server.deep_god.random.random", lambda: 0.10)
    result = god.should_act("some events", _player_status(y=20), kind_god_action_count=0)
    assert result is True


def test_surface_player_no_trigger(monkeypatch):
    """Player at Y=64 (surface) has no position-based chance."""
    god = DeepGod()
    monkeypatch.setattr("server.deep_god.random.random", lambda: 0.01)
    result = god.should_act("some events", _player_status(y=64), kind_god_action_count=0)
    assert result is False


# ---------------------------------------------------------------------------
# Ore mining triggers
# ---------------------------------------------------------------------------


def test_deep_ore_mining_underground_triggers(monkeypatch):
    """Mining deep ores while underground has 40% chance."""
    god = DeepGod()
    monkeypatch.setattr("server.deep_god.random.random", lambda: 0.3)
    event_summary = "MINING ACTIVITY:\n  Steve: 3 deepslate_diamond_ore (deepest: Y=-50)"
    result = god.should_act(event_summary, _player_status(y=-50), kind_god_action_count=0)
    assert result is True


def test_deep_ore_mining_on_surface_no_ore_trigger(monkeypatch):
    """Mining deep ores while on surface — ore trigger requires being underground."""
    god = DeepGod()
    # With surface player, chance is 0 so even low roll doesn't trigger
    monkeypatch.setattr("server.deep_god.random.random", lambda: 0.01)
    event_summary = "MINING ACTIVITY:\n  Steve: 3 deepslate_diamond_ore"
    result = god.should_act(event_summary, _player_status(y=64), kind_god_action_count=0)
    assert result is False


def test_all_deep_ores_recognized():
    """Every ore in DEEP_ORES should trigger if found in event summary."""
    god = DeepGod()
    for ore in DEEP_ORES:
        ore_clean = ore.replace("minecraft:", "")
        with patch("server.deep_god.random.random", return_value=0.01):
            result = god.should_act(
                f"MINING: Steve: 1 {ore_clean}",
                _player_status(y=-10),  # deep = 70% chance, will always trigger at 0.01
                kind_god_action_count=0)
            assert result is True, f"Ore {ore} should trigger Deep God"


# ---------------------------------------------------------------------------
# Prayer-specific player filtering
# ---------------------------------------------------------------------------


def test_prayer_only_considers_praying_player():
    """When a prayer triggers the tick, only the praying player's position matters."""
    god = DeepGod()
    status = {
        "players": [
            {"name": "SurfacePlayer", "location": {"x": 0, "y": 64, "z": 0}, "dimension": "overworld"},
            {"name": "DeepPlayer", "location": {"x": 0, "y": -50, "z": 0}, "dimension": "overworld"},
        ]
    }
    # SurfacePlayer is praying — only their position should be checked
    with patch("server.deep_god.random.random", return_value=0.01):
        result = god.should_act("prayer event", status,
                                kind_god_action_count=0, praying_player="SurfacePlayer")
    # SurfacePlayer is at Y=64 (surface) — no position trigger
    assert result is False


def test_prayer_deep_player_triggers():
    """Praying player who is deep underground should trigger."""
    god = DeepGod()
    status = {
        "players": [
            {"name": "DeepPlayer", "location": {"x": 0, "y": -50, "z": 0}, "dimension": "overworld"},
        ]
    }
    with patch("server.deep_god.random.random", return_value=0.01):
        result = god.should_act("prayer event", status,
                                kind_god_action_count=0, praying_player="DeepPlayer")
    assert result is True


def test_no_prayer_checks_all_players():
    """Regular tick (no prayer) checks all players — one deep player triggers."""
    god = DeepGod()
    status = {
        "players": [
            {"name": "SurfacePlayer", "location": {"x": 0, "y": 64, "z": 0}, "dimension": "overworld"},
            {"name": "DeepPlayer", "location": {"x": 0, "y": -50, "z": 0}, "dimension": "overworld"},
        ]
    }
    with patch("server.deep_god.random.random", return_value=0.01):
        result = god.should_act("some events", status, kind_god_action_count=0)
    assert result is True  # DeepPlayer triggers it


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_no_events_no_status_no_trigger():
    god = DeepGod()
    assert god.should_act(None, None, kind_god_action_count=0) is False


def test_empty_player_list_no_trigger(monkeypatch):
    god = DeepGod()
    monkeypatch.setattr("server.deep_god.random.random", lambda: 0.01)
    result = god.should_act("some events", {"players": []}, kind_god_action_count=0)
    assert result is False
