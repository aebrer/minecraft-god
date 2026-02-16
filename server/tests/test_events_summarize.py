"""Tests for EventBuffer.drain_and_summarize — the event-to-text pipeline.

Each test adds specific events to the buffer and verifies the summarized
text output contains the right information. These tests validate the
formatting logic that feeds context to the gods.
"""

from server.events import EventBuffer


# ---------------------------------------------------------------------------
# Weather events
# ---------------------------------------------------------------------------


def test_weather_change():
    buf = EventBuffer()
    buf.add({"type": "weather_change", "newWeather": "Thunder"})
    summary = buf.drain_and_summarize()
    assert "WEATHER:" in summary
    assert "Thunder" in summary


def test_multiple_weather_shows_latest():
    buf = EventBuffer()
    buf.add({"type": "weather_change", "newWeather": "Rain"})
    buf.add({"type": "weather_change", "newWeather": "Clear"})
    summary = buf.drain_and_summarize()
    assert "Clear" in summary


# ---------------------------------------------------------------------------
# Chat events
# ---------------------------------------------------------------------------


def test_chat_messages():
    buf = EventBuffer()
    buf.add({"type": "chat", "player": "Steve", "message": "hello world"})
    summary = buf.drain_and_summarize()
    assert "CHAT:" in summary
    assert "Steve" in summary
    assert "hello world" in summary


def test_chat_wrapped_in_delimiters():
    buf = EventBuffer()
    buf.add({"type": "chat", "player": "Steve", "message": "test"})
    summary = buf.drain_and_summarize()
    assert "[PLAYER CHAT]" in summary


# ---------------------------------------------------------------------------
# Player joins and leaves
# ---------------------------------------------------------------------------


def test_player_join():
    buf = EventBuffer()
    buf.add({"type": "player_join", "player": "Steve"})
    summary = buf.drain_and_summarize()
    assert "ARRIVALS/DEPARTURES:" in summary
    assert "Steve" in summary
    assert "joined" in summary


def test_player_leave():
    buf = EventBuffer()
    buf.add({"type": "player_leave", "player": "Steve"})
    summary = buf.drain_and_summarize()
    assert "left" in summary


# ---------------------------------------------------------------------------
# Player deaths
# ---------------------------------------------------------------------------


def test_player_death():
    buf = EventBuffer()
    buf.add({
        "type": "entity_die",
        "isPlayer": True,
        "playerName": "Steve",
        "cause": "fall",
        "damagingEntity": None,
        "location": {"x": 10, "y": -30, "z": 50},
    })
    summary = buf.drain_and_summarize()
    assert "PLAYER DEATHS:" in summary
    assert "Steve" in summary
    assert "fall" in summary


def test_player_death_with_killer():
    buf = EventBuffer()
    buf.add({
        "type": "entity_die",
        "isPlayer": True,
        "playerName": "Steve",
        "cause": "entity_attack",
        "damagingEntity": "minecraft:creeper",
        "location": {"x": 0, "y": 64, "z": 0},
    })
    summary = buf.drain_and_summarize()
    assert "creeper" in summary


# ---------------------------------------------------------------------------
# Mob kills
# ---------------------------------------------------------------------------


def test_mob_kills_aggregated():
    buf = EventBuffer()
    for _ in range(3):
        buf.add({
            "type": "entity_die",
            "isPlayer": False,
            "entity": "minecraft:zombie",
            "damagingEntity": "Steve",
        })
    summary = buf.drain_and_summarize()
    assert "MOB KILLS:" in summary
    assert "3 zombie" in summary
    assert "Steve" in summary


def test_mob_kills_multiple_types():
    buf = EventBuffer()
    buf.add({"type": "entity_die", "isPlayer": False, "entity": "minecraft:zombie", "damagingEntity": "Steve"})
    buf.add({"type": "entity_die", "isPlayer": False, "entity": "minecraft:skeleton", "damagingEntity": "Steve"})
    summary = buf.drain_and_summarize()
    assert "zombie" in summary
    assert "skeleton" in summary


# ---------------------------------------------------------------------------
# Block events (mining and building)
# ---------------------------------------------------------------------------


def test_mining_activity():
    buf = EventBuffer()
    for _ in range(5):
        buf.add({
            "type": "block_break",
            "player": "Steve",
            "block": "minecraft:diamond_ore",
            "location": {"x": 0, "y": -50, "z": 0},
        })
    summary = buf.drain_and_summarize()
    assert "MINING ACTIVITY:" in summary
    assert "5 diamond_ore" in summary
    assert "deepest: Y=-50" in summary


def test_building_activity():
    buf = EventBuffer()
    buf.add({
        "type": "block_place",
        "player": "Steve",
        "block": "minecraft:cobblestone",
        "location": {"x": 0, "y": 64, "z": 0},
    })
    summary = buf.drain_and_summarize()
    assert "BUILDING ACTIVITY:" in summary
    assert "cobblestone" in summary


def test_mining_top_5_with_overflow():
    """When more than 5 block types are mined, extras are summarized."""
    buf = EventBuffer()
    blocks = ["stone", "cobblestone", "dirt", "gravel", "sand", "granite", "diorite"]
    for block in blocks:
        buf.add({
            "type": "block_break",
            "player": "Steve",
            "block": f"minecraft:{block}",
            "location": {"x": 0, "y": 30, "z": 0},
        })
    summary = buf.drain_and_summarize()
    assert "other blocks" in summary


# ---------------------------------------------------------------------------
# Combat events
# ---------------------------------------------------------------------------


def test_combat_event():
    buf = EventBuffer()
    buf.add({
        "type": "combat",
        "attackerName": "Steve",
        "hurtEntityName": "minecraft:zombie",
        "damage": 7.0,
        "cause": "entity_attack",
        "timestamp": 1000,
        "location": {"x": 10, "y": 64, "z": 20},
    })
    summary = buf.drain_and_summarize()
    assert "COMBAT:" in summary
    assert "Steve vs zombie" in summary
    assert "7 total damage" in summary


def test_combat_deduplication():
    """Hits within 5 seconds against same target are merged."""
    buf = EventBuffer()
    for i in range(3):
        buf.add({
            "type": "combat",
            "attackerName": "Steve",
            "hurtEntityName": "minecraft:zombie",
            "damage": 5.0,
            "cause": "entity_attack",
            "timestamp": 1000 + i * 1000,  # 1s apart = within 5s window
            "location": {"x": 10, "y": 64, "z": 20},
        })
    summary = buf.drain_and_summarize()
    assert "3 hits" in summary
    assert "15 total damage" in summary


def test_combat_separate_targets():
    """Hits against different targets are NOT merged."""
    buf = EventBuffer()
    buf.add({
        "type": "combat",
        "attackerName": "Steve",
        "hurtEntityName": "minecraft:zombie",
        "damage": 5.0,
        "timestamp": 1000,
        "cause": "entity_attack",
        "location": {"x": 0, "y": 64, "z": 0},
    })
    buf.add({
        "type": "combat",
        "attackerName": "Steve",
        "hurtEntityName": "minecraft:skeleton",
        "damage": 3.0,
        "timestamp": 1001,
        "cause": "entity_attack",
        "location": {"x": 0, "y": 64, "z": 0},
    })
    summary = buf.drain_and_summarize()
    assert "zombie" in summary
    assert "skeleton" in summary


# ---------------------------------------------------------------------------
# Divine keyword filtering
# ---------------------------------------------------------------------------


def test_filter_divine_removes_prayer_chat():
    buf = EventBuffer()
    buf.add({"type": "chat", "player": "Steve", "message": "God please help me"})
    buf.add({"type": "chat", "player": "Alex", "message": "nice base!"})
    summary = buf.drain_and_summarize(filter_divine=True)
    # Prayer should be filtered, regular chat kept
    assert "nice base" in summary
    assert "please help" not in summary


def test_filter_divine_keeps_non_prayer_chat():
    buf = EventBuffer()
    buf.add({"type": "chat", "player": "Steve", "message": "hello everyone"})
    summary = buf.drain_and_summarize(filter_divine=True)
    assert "hello everyone" in summary


def test_filter_divine_with_only_prayers_returns_none():
    buf = EventBuffer()
    buf.add({"type": "chat", "player": "Steve", "message": "God help me"})
    summary = buf.drain_and_summarize(filter_divine=True)
    assert summary is None


# ---------------------------------------------------------------------------
# Mixed event types
# ---------------------------------------------------------------------------


def test_mixed_events_all_sections_present():
    buf = EventBuffer()
    buf.add({"type": "chat", "player": "Steve", "message": "hello"})
    buf.add({"type": "weather_change", "newWeather": "Rain"})
    buf.add({"type": "player_join", "player": "Alex"})
    buf.add({"type": "block_break", "player": "Steve", "block": "minecraft:stone",
             "location": {"x": 0, "y": 30, "z": 0}})
    summary = buf.drain_and_summarize()
    assert "CHAT:" in summary
    assert "WEATHER:" in summary
    assert "ARRIVALS/DEPARTURES:" in summary
    assert "MINING ACTIVITY:" in summary


def test_player_status_included_in_summary():
    """When player status exists and has players, it appears in the summary."""
    buf = EventBuffer()
    buf.add({"type": "player_status", "players": [
        {"name": "Steve", "location": {"x": 10, "y": 64, "z": -20},
         "dimension": "overworld", "biome": "forest",
         "facing": "N", "lookingVertical": "ahead",
         "health": 20, "maxHealth": 20, "foodLevel": 18, "level": 7,
         "armor": [], "inventory": {}}
    ]})
    buf.add({"type": "weather_change", "newWeather": "Clear"})
    summary = buf.drain_and_summarize()
    assert "PLAYERS ONLINE:" in summary
    assert "Steve" in summary
