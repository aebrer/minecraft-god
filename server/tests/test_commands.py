"""Tests for command translation — the security boundary between LLM and Minecraft.

The command system validates and translates LLM tool calls into safe Minecraft
commands.  These tests verify allowlist enforcement, input sanitization,
coordinate clamping, and the build_schematic placement math.
"""

import json
import types
from unittest.mock import patch

from server.commands import (
    ALLOWED_COMMANDS,
    BLOCKED_ITEMS,
    VALID_EFFECTS,
    VALID_MOBS,
    _build_schematic,
    _change_weather,
    _clear_item,
    _give_effect,
    _give_item,
    _play_sound,
    _send_message,
    _set_difficulty,
    _set_time,
    _strike_lightning,
    _summon_mob,
    _teleport_player,
    _validate_command,
    _validate_player_target,
    translate_tool_calls,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool_call(name: str, args: dict, call_id: str = "tc_1"):
    """Build a fake LLM tool call object matching the OpenAI SDK shape."""
    tc = types.SimpleNamespace()
    tc.id = call_id
    tc.function = types.SimpleNamespace()
    tc.function.name = name
    tc.function.arguments = json.dumps(args)
    return tc


# ---------------------------------------------------------------------------
# Player target validation
# ---------------------------------------------------------------------------


def test_allowed_selectors():
    for sel in ("@a", "@s", "@p"):
        assert _validate_player_target(sel)


def test_disallowed_selectors():
    assert not _validate_player_target("@e")
    assert not _validate_player_target("@r")
    assert not _validate_player_target("@e[type=zombie]")


def test_valid_player_names():
    assert _validate_player_target("aeBRER")
    assert _validate_player_target("Steve")
    assert _validate_player_target("Player_1")


def test_invalid_player_names():
    assert not _validate_player_target("")
    assert not _validate_player_target("a" * 33)  # too long
    assert not _validate_player_target("player;drop table")  # injection attempt


# ---------------------------------------------------------------------------
# Command allowlist
# ---------------------------------------------------------------------------


def test_allowed_commands():
    assert _validate_command("summon minecraft:zombie ~ ~ ~")
    assert _validate_command("tellraw @a {}")
    assert _validate_command("give @s diamond 1")


def test_blocked_commands():
    assert not _validate_command("op aeBRER")
    assert not _validate_command("deop aeBRER")
    assert not _validate_command("ban Steve")
    assert not _validate_command("stop")
    assert not _validate_command("execute as @a run kill @s")


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


def test_chat_message_public():
    result = _send_message({"message": "Hello world", "style": "chat"}, "kind_god")
    assert result is not None
    assert result["command"].startswith("tellraw @a")
    assert "The Kind God" in result["command"]
    assert "Hello world" in result["command"]


def test_chat_message_private():
    result = _send_message(
        {"message": "Secret", "style": "chat", "target_player": "Steve"}, "kind_god")
    assert isinstance(result, list)
    assert len(result) == 2
    # First is whisper to target, second is notification to others
    assert "Steve" in result[0]["command"]
    assert "whispered" in result[0]["command"]
    assert "whispers to Steve" in result[1]["command"]


def test_title_message():
    result = _send_message({"message": "Big text", "style": "title"}, "kind_god")
    assert result is not None
    assert "title @a title" in result["command"]


def test_actionbar_message():
    result = _send_message({"message": "Status text", "style": "actionbar"}, "kind_god")
    assert result is not None
    assert "title @a actionbar" in result["command"]


def test_message_length_cap_title():
    long_msg = "A" * 100
    result = _send_message({"message": long_msg, "style": "title"}, "kind_god")
    # Title max is 40 chars — the JSON payload should contain truncated text
    payload = json.loads(result["command"].split("title @a title ")[1])
    assert len(payload["text"]) <= 40


def test_message_newline_stripped():
    result = _send_message({"message": "line1\nline2", "style": "chat"}, "kind_god")
    assert "\\n" not in result["command"]


def test_message_invalid_target_blocked():
    result = _send_message(
        {"message": "Hi", "style": "chat", "target_player": "@e[type=zombie]"}, "kind_god")
    assert result is None


# ---------------------------------------------------------------------------
# summon_mob
# ---------------------------------------------------------------------------


def test_summon_valid_mob():
    result = _summon_mob({"mob_type": "zombie", "near_player": "Steve", "count": 1})
    assert isinstance(result, list)
    assert len(result) == 1
    assert "minecraft:zombie" in result[0]["command"]
    assert result[0]["target_player"] == "Steve"


def test_summon_invalid_mob_blocked():
    assert _summon_mob({"mob_type": "wither"}) is None
    assert _summon_mob({"mob_type": "ender_dragon"}) is None


def test_summon_count_clamped():
    result = _summon_mob({"mob_type": "cow", "count": 100})
    assert len(result) == 5  # max 5

    result = _summon_mob({"mob_type": "cow", "count": -5})
    assert len(result) == 1  # min 1


def test_summon_minecraft_prefix_stripped():
    result = _summon_mob({"mob_type": "minecraft:sheep"})
    assert result is not None
    assert "minecraft:sheep" in result[0]["command"]


def test_summon_invalid_location_blocked():
    result = _summon_mob({"mob_type": "zombie", "location": "$(rm -rf /)"})
    assert result is None


# ---------------------------------------------------------------------------
# give_effect
# ---------------------------------------------------------------------------


def test_give_valid_effect():
    result = _give_effect({"target_player": "Steve", "effect": "speed", "duration": 30})
    assert result is not None
    assert "effect give Steve minecraft:speed 30" in result["command"]


def test_give_invalid_effect_blocked():
    assert _give_effect({"effect": "super_power"}) is None


def test_effect_duration_clamped():
    result = _give_effect({"effect": "speed", "duration": 9999})
    assert "120" in result["command"]  # max 120

    result = _give_effect({"effect": "speed", "duration": -5})
    assert " 1 " in result["command"]  # min 1


def test_effect_amplifier_clamped():
    result = _give_effect({"effect": "speed", "amplifier": 255})
    assert result["command"].endswith(" 3")  # max 3


# ---------------------------------------------------------------------------
# give_item
# ---------------------------------------------------------------------------


def test_give_valid_item():
    result = _give_item({"player": "Steve", "item": "diamond", "count": 5})
    assert result is not None
    assert "give Steve minecraft:diamond 5" in result["command"]


def test_give_blocked_item():
    for item in BLOCKED_ITEMS:
        assert _give_item({"item": item}) is None


def test_give_item_count_clamped():
    result = _give_item({"item": "diamond", "count": 1000})
    assert "64" in result["command"]  # max 64


def test_give_item_invalid_name_blocked():
    assert _give_item({"item": "diamond; /op Steve"}) is None


# ---------------------------------------------------------------------------
# change_weather
# ---------------------------------------------------------------------------


def test_valid_weather():
    for w in ("clear", "rain", "thunder"):
        result = _change_weather({"weather_type": w})
        assert result is not None
        assert w in result["command"]


def test_invalid_weather():
    assert _change_weather({"weather_type": "tornado"}) is None


def test_weather_duration_clamped():
    result = _change_weather({"weather_type": "rain", "duration": 999999})
    assert "24000" in result["command"]  # max


# ---------------------------------------------------------------------------
# set_time
# ---------------------------------------------------------------------------


def test_valid_times():
    for t in ("day", "noon", "sunset", "night", "midnight", "sunrise"):
        assert _set_time({"time": t}) is not None


def test_invalid_time():
    assert _set_time({"time": "apocalypse"}) is None


# ---------------------------------------------------------------------------
# set_difficulty
# ---------------------------------------------------------------------------


def test_valid_difficulties():
    for d in ("peaceful", "easy", "normal", "hard"):
        result = _set_difficulty({"difficulty": d})
        assert result is not None
        assert d in result["command"]


def test_invalid_difficulty():
    assert _set_difficulty({"difficulty": "impossible"}) is None


# ---------------------------------------------------------------------------
# build_schematic — direction math
# ---------------------------------------------------------------------------


def test_build_schematic_in_front_north():
    """Player facing north, build in front = negative Z."""
    player_ctx = {"steve": {"x": 100, "y": 64, "z": 200, "facing": "N"}}
    with patch("server.commands.build_schematic_command", return_value={"type": "build_schematic"}):
        result = _build_schematic(
            {"blueprint_id": "test", "near_player": "Steve", "in_front": True, "distance": "near"},
            player_context=player_ctx)
    assert result is not None


def test_build_schematic_in_front_resolves_coordinates():
    """Verify the actual coordinate math for in_front placement."""
    player_ctx = {"steve": {"x": 100, "y": 64, "z": 200, "facing": "S"}}
    captured = {}

    def capture_build(bp_id, x, y, z, rotation):
        captured.update({"x": x, "y": y, "z": z})
        return {"type": "build_schematic"}

    with patch("server.commands.build_schematic_command", side_effect=capture_build):
        _build_schematic(
            {"blueprint_id": "test", "near_player": "Steve", "in_front": True, "distance": "near"},
            player_context=player_ctx)

    # Facing south = +Z, "near" = 10 blocks
    assert captured["x"] == 100
    assert captured["z"] == 210
    assert captured["y"] == 64


def test_build_schematic_explicit_direction():
    """Use direction instead of in_front."""
    player_ctx = {"steve": {"x": 0, "y": 64, "z": 0, "facing": "N"}}
    captured = {}

    def capture_build(bp_id, x, y, z, rotation):
        captured.update({"x": x, "z": z})
        return {"type": "build_schematic"}

    with patch("server.commands.build_schematic_command", side_effect=capture_build):
        _build_schematic(
            {"blueprint_id": "test", "near_player": "Steve",
             "in_front": False, "direction": "E", "distance": "medium"},
            player_context=player_ctx)

    # East = +X, "medium" = 25 blocks
    assert captured["x"] == 25
    assert captured["z"] == 0


def test_build_schematic_diagonal_distance():
    """Diagonal direction scales by 0.7 to maintain similar total distance."""
    player_ctx = {"steve": {"x": 0, "y": 64, "z": 0, "facing": "N"}}
    captured = {}

    def capture_build(bp_id, x, y, z, rotation):
        captured.update({"x": x, "z": z})
        return {"type": "build_schematic"}

    with patch("server.commands.build_schematic_command", side_effect=capture_build):
        _build_schematic(
            {"blueprint_id": "test", "near_player": "Steve",
             "in_front": False, "direction": "NE", "distance": "near"},
            player_context=player_ctx)

    # NE = +X, -Z, scaled by 0.7: int(10 * 0.7) = 7
    assert captured["x"] == 7
    assert captured["z"] == -7


def test_build_schematic_missing_player_returns_none():
    result = _build_schematic({"blueprint_id": "test", "near_player": ""})
    assert result is None


def test_build_schematic_unknown_player_returns_none():
    player_ctx = {"steve": {"x": 0, "y": 64, "z": 0, "facing": "N"}}
    result = _build_schematic(
        {"blueprint_id": "test", "near_player": "Unknown"},
        player_context=player_ctx)
    assert result is None


# ---------------------------------------------------------------------------
# translate_tool_calls — integration through the public interface
# ---------------------------------------------------------------------------


def test_translate_do_nothing():
    """do_nothing returns no commands and no errors."""
    tc = _make_tool_call("do_nothing", {"reason": "all is well"})
    commands, errors = translate_tool_calls([tc])
    assert commands == []
    assert errors == {}


def test_translate_unknown_tool():
    """Unknown tools return no commands (dropped silently)."""
    tc = _make_tool_call("hack_server", {"target": "everything"})
    commands, errors = translate_tool_calls([tc])
    assert commands == []
    assert len(errors) == 1  # should get an error message


def test_translate_multiple_tools():
    """Multiple tool calls are processed in order."""
    tc1 = _make_tool_call("set_time", {"time": "day"}, "tc_1")
    tc2 = _make_tool_call("change_weather", {"weather_type": "clear"}, "tc_2")
    commands, errors = translate_tool_calls([tc1, tc2])
    assert len(commands) == 2
    assert errors == {}


def test_translate_invalid_json_handled():
    """Malformed JSON in tool call arguments doesn't crash."""
    tc = types.SimpleNamespace()
    tc.id = "tc_bad"
    tc.function = types.SimpleNamespace()
    tc.function.name = "set_time"
    tc.function.arguments = "not json at all"
    commands, errors = translate_tool_calls([tc])
    assert commands == []
    assert "tc_bad" in errors
