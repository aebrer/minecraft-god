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
    _assign_mission,
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
    _validate_block,
    _validate_command,
    _validate_coordinate,
    _validate_player_target,
    get_schematic_tool_results,
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


# ---------------------------------------------------------------------------
# clear_item
# ---------------------------------------------------------------------------


def test_clear_specific_item():
    result = _clear_item({"player": "Steve", "item": "diamond"})
    assert result is not None
    assert "clear Steve minecraft:diamond" in result["command"]


def test_clear_all_items():
    result = _clear_item({"player": "Steve"})
    assert result is not None
    assert result["command"] == "clear Steve"


def test_clear_invalid_item_blocked():
    result = _clear_item({"player": "Steve", "item": "diamond; /op hacker"})
    assert result is None


def test_clear_invalid_player_blocked():
    result = _clear_item({"player": "@e[type=zombie]"})
    assert result is None


# ---------------------------------------------------------------------------
# strike_lightning
# ---------------------------------------------------------------------------


def test_strike_lightning_valid():
    result = _strike_lightning({"near_player": "Steve"})
    assert result is not None
    assert "lightning_bolt" in result["command"]
    assert result["target_player"] == "Steve"


def test_strike_lightning_custom_offset():
    result = _strike_lightning({"near_player": "Steve", "offset": "~5 ~ ~-3"})
    assert "~5 ~ ~-3" in result["command"]


def test_strike_lightning_invalid_offset_blocked():
    result = _strike_lightning({"near_player": "Steve", "offset": "$(rm -rf /)"})
    assert result is None


# ---------------------------------------------------------------------------
# play_sound
# ---------------------------------------------------------------------------


def test_play_sound_valid():
    result = _play_sound({"sound": "ambient.cave", "target_player": "Steve"})
    assert result is not None
    assert "playsound" in result["command"]
    assert "minecraft:ambient.cave" in result["command"]


def test_play_sound_auto_prefix():
    result = _play_sound({"sound": "mob.wither.spawn"})
    assert "minecraft:mob.wither.spawn" in result["command"]


def test_play_sound_already_prefixed():
    result = _play_sound({"sound": "minecraft:mob.ghast.scream"})
    assert result["command"].count("minecraft:") == 1


def test_play_sound_invalid_chars_blocked():
    result = _play_sound({"sound": "sound; malicious"})
    assert result is None


# ---------------------------------------------------------------------------
# teleport_player
# ---------------------------------------------------------------------------


def test_teleport_valid():
    result = _teleport_player({"player": "Steve", "x": 100, "y": 64, "z": -200})
    assert result is not None
    assert "tp Steve 100 64 -200" in result["command"]


def test_teleport_invalid_player_blocked():
    result = _teleport_player({"player": "@e"})
    assert result is None


# ---------------------------------------------------------------------------
# assign_mission
# ---------------------------------------------------------------------------


def test_assign_mission_basic():
    result = _assign_mission({"player": "Steve", "mission_title": "Find Diamonds"}, "kind_god")
    assert isinstance(result, list)
    assert len(result) >= 2  # at least title + broadcast
    # Title command should be present
    assert any("title Steve title" in cmd["command"] for cmd in result)
    # Broadcast should mention the quest
    assert any("Find Diamonds" in cmd["command"] for cmd in result)


def test_assign_mission_with_subtitle():
    result = _assign_mission({
        "player": "Steve",
        "mission_title": "Explore",
        "mission_description": "Find the stronghold",
    }, "kind_god")
    # Should have subtitle + title + broadcast
    assert len(result) == 3
    assert any("subtitle" in cmd["command"] for cmd in result)


def test_assign_mission_with_reward():
    result = _assign_mission({
        "player": "Steve",
        "mission_title": "Slay",
        "reward_hint": "diamond sword",
    }, "kind_god")
    broadcast = [cmd for cmd in result if "tellraw" in cmd["command"]]
    assert any("diamond sword" in cmd["command"] for cmd in broadcast)


def test_assign_mission_invalid_player():
    result = _assign_mission({"player": "@e", "mission_title": "Test"}, "kind_god")
    assert result == []


# ---------------------------------------------------------------------------
# validate_block and validate_coordinate
# ---------------------------------------------------------------------------


def test_validate_block_valid():
    assert _validate_block("stone") == "stone"
    assert _validate_block("minecraft:oak_planks") == "oak_planks"


def test_validate_block_strips_prefix():
    assert _validate_block("minecraft:cobblestone") == "cobblestone"


def test_validate_block_blocked_item():
    assert _validate_block("command_block") is None
    assert _validate_block("bedrock") is None


def test_validate_block_invalid_chars():
    assert _validate_block("stone; /op hacker") is None
    assert _validate_block("") is None


def test_validate_coordinate_valid():
    assert _validate_coordinate(100) == 100
    assert _validate_coordinate(-30000) == -30000
    assert _validate_coordinate(0) == 0


def test_validate_coordinate_out_of_range():
    assert _validate_coordinate(50000) is None
    assert _validate_coordinate(-50000) is None


def test_validate_coordinate_invalid_type():
    assert _validate_coordinate("not_a_number") is None
    assert _validate_coordinate(None) is None


# ---------------------------------------------------------------------------
# build_schematic — invalid direction fallback
# ---------------------------------------------------------------------------


def test_build_schematic_invalid_direction_falls_back_to_north():
    """Invalid direction string defaults to 'N'."""
    player_ctx = {"steve": {"x": 0, "y": 64, "z": 0, "facing": "N"}}
    captured = {}

    def capture_build(bp_id, x, y, z, rotation):
        captured.update({"x": x, "z": z})
        return {"type": "build_schematic"}

    with patch("server.commands.build_schematic_command", side_effect=capture_build):
        _build_schematic(
            {"blueprint_id": "test", "near_player": "Steve",
             "in_front": False, "direction": "INVALID", "distance": "near"},
            player_context=player_ctx)

    # Falls back to N: (0, -1), near=10 blocks
    assert captured["x"] == 0
    assert captured["z"] == -10


# ---------------------------------------------------------------------------
# get_schematic_tool_results
# ---------------------------------------------------------------------------


def test_get_schematic_tool_results_search():
    tc = _make_tool_call("search_schematics", {"query": "iron farm"})
    with patch("server.commands.search_schematics", return_value="mock results"):
        results = get_schematic_tool_results([tc])
    assert "tc_1" in results
    assert results["tc_1"] == "mock results"


def test_get_schematic_tool_results_invalid_json():
    tc = types.SimpleNamespace()
    tc.id = "tc_bad"
    tc.function = types.SimpleNamespace()
    tc.function.name = "search_schematics"
    tc.function.arguments = "{bad json"
    results = get_schematic_tool_results([tc])
    assert "ERROR" in results["tc_bad"]


# ---------------------------------------------------------------------------
# translate_tool_calls — full dispatch coverage
# ---------------------------------------------------------------------------


def test_translate_dispatch_all_tool_types():
    """Verify the dispatch chain routes each tool type correctly."""
    tool_calls = [
        _make_tool_call("send_message", {"message": "hi", "style": "chat"}, "tc_msg"),
        _make_tool_call("change_weather", {"weather_type": "rain"}, "tc_weather"),
        _make_tool_call("give_effect", {"target_player": "@a", "effect": "speed"}, "tc_effect"),
        _make_tool_call("set_time", {"time": "day"}, "tc_time"),
        _make_tool_call("give_item", {"player": "@a", "item": "diamond"}, "tc_item"),
        _make_tool_call("set_difficulty", {"difficulty": "hard"}, "tc_diff"),
    ]
    commands, errors = translate_tool_calls(tool_calls)
    assert len(commands) == 6
    assert errors == {}


def test_translate_dispatch_strike_lightning():
    tc = _make_tool_call("strike_lightning", {"near_player": "Steve"})
    commands, errors = translate_tool_calls([tc])
    assert len(commands) == 1
    assert "lightning_bolt" in commands[0]["command"]


def test_translate_dispatch_play_sound():
    tc = _make_tool_call("play_sound", {"sound": "ambient.cave"})
    commands, errors = translate_tool_calls([tc])
    assert len(commands) == 1
    assert "playsound" in commands[0]["command"]


def test_translate_dispatch_clear_item():
    tc = _make_tool_call("clear_item", {"player": "Steve", "item": "dirt"})
    commands, errors = translate_tool_calls([tc])
    assert len(commands) == 1
    assert "clear" in commands[0]["command"]


def test_translate_dispatch_teleport():
    tc = _make_tool_call("teleport_player", {"player": "Steve", "x": 0, "y": 64, "z": 0})
    commands, errors = translate_tool_calls([tc])
    assert len(commands) == 1
    assert "tp" in commands[0]["command"]


def test_translate_dispatch_assign_mission():
    tc = _make_tool_call("assign_mission", {"player": "Steve", "mission_title": "Quest"})
    commands, errors = translate_tool_calls([tc])
    assert len(commands) >= 2  # title + broadcast
    assert errors == {}


def test_translate_dispatch_summon():
    tc = _make_tool_call("summon_mob", {"mob_type": "cow", "count": 2})
    commands, errors = translate_tool_calls([tc])
    assert len(commands) == 2
