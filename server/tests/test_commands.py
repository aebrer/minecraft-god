"""Tests for command translation — the security boundary between LLM and Minecraft.

All tests go through the public interface: translate_tool_calls() and
get_schematic_tool_results(). This ensures the tests describe WHAT the
system does (blocks dangerous items, clamps durations, etc.) rather than
HOW it's implemented internally.
"""

import json
import types
from unittest.mock import patch

from server.commands import (
    BLOCKED_ITEMS,
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


def _translate_one(name: str, args: dict, **kwargs) -> tuple[list[dict], dict]:
    """Shorthand: translate a single tool call and return (commands, errors)."""
    tc = _make_tool_call(name, args)
    return translate_tool_calls([tc], **kwargs)


def _cmds(name: str, args: dict, **kwargs) -> list[dict]:
    """Shorthand: translate a single tool call and return just the commands."""
    commands, _ = _translate_one(name, args, **kwargs)
    return commands


def _cmd(name: str, args: dict, **kwargs) -> dict | None:
    """Shorthand: translate a single tool call expecting exactly one command."""
    commands = _cmds(name, args, **kwargs)
    return commands[0] if commands else None


# ---------------------------------------------------------------------------
# General dispatch and error handling
# ---------------------------------------------------------------------------


def test_do_nothing_returns_no_commands():
    commands, errors = _translate_one("do_nothing", {"reason": "all is well"})
    assert commands == []
    assert errors == {}


def test_unknown_tool_returns_error():
    commands, errors = _translate_one("hack_server", {"target": "everything"})
    assert commands == []
    assert len(errors) == 1


def test_multiple_tools_processed_in_order():
    tc1 = _make_tool_call("set_time", {"time": "day"}, "tc_1")
    tc2 = _make_tool_call("change_weather", {"weather_type": "clear"}, "tc_2")
    commands, errors = translate_tool_calls([tc1, tc2])
    assert len(commands) == 2
    assert errors == {}


def test_malformed_json_returns_error():
    tc = types.SimpleNamespace()
    tc.id = "tc_bad"
    tc.function = types.SimpleNamespace()
    tc.function.name = "set_time"
    tc.function.arguments = "not json at all"
    commands, errors = translate_tool_calls([tc])
    assert commands == []
    assert "tc_bad" in errors


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


def test_chat_message_public():
    cmd = _cmd("send_message", {"message": "Hello world", "style": "chat"})
    assert cmd is not None
    assert cmd["command"].startswith("tellraw @a")
    assert "The Kind God" in cmd["command"]
    assert "Hello world" in cmd["command"]


def test_chat_message_private():
    cmds = _cmds("send_message",
                 {"message": "Secret", "style": "chat", "target_player": "Steve"})
    assert len(cmds) == 2
    # First is whisper to target, second is notification to others
    assert "Steve" in cmds[0]["command"]
    assert "whispered" in cmds[0]["command"]
    assert "whispers to Steve" in cmds[1]["command"]


def test_title_message():
    cmd = _cmd("send_message", {"message": "Big text", "style": "title"})
    assert "title @a title" in cmd["command"]


def test_actionbar_message():
    cmd = _cmd("send_message", {"message": "Status text", "style": "actionbar"})
    assert "title @a actionbar" in cmd["command"]


def test_message_length_cap_title():
    long_msg = "A" * 100
    cmd = _cmd("send_message", {"message": long_msg, "style": "title"})
    payload = json.loads(cmd["command"].split("title @a title ")[1])
    assert len(payload["text"]) <= 40


def test_message_newline_stripped():
    cmd = _cmd("send_message", {"message": "line1\nline2", "style": "chat"})
    assert "\\n" not in cmd["command"]


def test_message_invalid_target_produces_no_commands():
    cmds = _cmds("send_message",
                 {"message": "Hi", "style": "chat", "target_player": "@e[type=zombie]"})
    assert cmds == []


def test_message_god_attribution():
    """Each god source produces its own attributed name."""
    for source, expected_name in [
        ("kind_god", "The Kind God"),
        ("deep_god", "???"),
        ("herald", "The Herald"),
    ]:
        cmd = _cmd("send_message", {"message": "test", "style": "chat"}, source=source)
        assert expected_name in cmd["command"]


# ---------------------------------------------------------------------------
# Player target validation (observable through any player-targeting tool)
# ---------------------------------------------------------------------------


def test_allowed_selectors():
    for sel in ("@a", "@s", "@p"):
        cmd = _cmd("give_effect", {"target_player": sel, "effect": "speed"})
        assert cmd is not None


def test_disallowed_selectors():
    for sel in ("@e", "@r", "@e[type=zombie]"):
        cmds = _cmds("give_effect", {"target_player": sel, "effect": "speed"})
        assert cmds == []


def test_valid_player_names():
    for name in ("aeBRER", "Steve", "Player_1"):
        cmd = _cmd("give_effect", {"target_player": name, "effect": "speed"})
        assert cmd is not None


def test_invalid_player_names():
    for name in ("", "a" * 33, "player;drop table"):
        cmds = _cmds("give_effect", {"target_player": name, "effect": "speed"})
        assert cmds == []


# ---------------------------------------------------------------------------
# summon_mob
# ---------------------------------------------------------------------------


def test_summon_valid_mob():
    cmds = _cmds("summon_mob", {"mob_type": "zombie", "near_player": "Steve", "count": 1})
    assert len(cmds) == 1
    assert "minecraft:zombie" in cmds[0]["command"]
    assert cmds[0]["target_player"] == "Steve"


def test_summon_invalid_mob_blocked():
    for mob in ("wither", "ender_dragon"):
        cmds = _cmds("summon_mob", {"mob_type": mob})
        assert cmds == []


def test_summon_count_clamped():
    cmds = _cmds("summon_mob", {"mob_type": "cow", "count": 100})
    assert len(cmds) == 5  # max 5

    cmds = _cmds("summon_mob", {"mob_type": "cow", "count": -5})
    assert len(cmds) == 1  # min 1


def test_summon_minecraft_prefix_stripped():
    cmds = _cmds("summon_mob", {"mob_type": "minecraft:sheep"})
    assert len(cmds) == 1
    assert "minecraft:sheep" in cmds[0]["command"]


def test_summon_invalid_location_blocked():
    cmds = _cmds("summon_mob", {"mob_type": "zombie", "location": "$(rm -rf /)"})
    assert cmds == []


# ---------------------------------------------------------------------------
# give_effect
# ---------------------------------------------------------------------------


def test_give_valid_effect():
    cmd = _cmd("give_effect", {"target_player": "Steve", "effect": "speed", "duration": 30})
    assert "effect give Steve minecraft:speed 30" in cmd["command"]


def test_give_invalid_effect_blocked():
    cmds = _cmds("give_effect", {"target_player": "@a", "effect": "super_power"})
    assert cmds == []


def test_effect_duration_clamped():
    cmd = _cmd("give_effect", {"target_player": "@a", "effect": "speed", "duration": 9999})
    assert "120" in cmd["command"]  # max 120

    cmd = _cmd("give_effect", {"target_player": "@a", "effect": "speed", "duration": -5})
    assert " 1 " in cmd["command"]  # min 1


def test_effect_amplifier_clamped():
    cmd = _cmd("give_effect", {"target_player": "@a", "effect": "speed", "amplifier": 255})
    assert cmd["command"].endswith(" 3")  # max 3


# ---------------------------------------------------------------------------
# give_item
# ---------------------------------------------------------------------------


def test_give_valid_item():
    cmd = _cmd("give_item", {"player": "Steve", "item": "diamond", "count": 5})
    assert "give Steve minecraft:diamond 5" in cmd["command"]


def test_give_blocked_item():
    for item in BLOCKED_ITEMS:
        cmds = _cmds("give_item", {"player": "@a", "item": item})
        assert cmds == [], f"{item} should be blocked"


def test_give_item_count_clamped():
    cmd = _cmd("give_item", {"player": "@a", "item": "diamond", "count": 1000})
    assert "64" in cmd["command"]  # max 64


def test_give_item_invalid_name_blocked():
    cmds = _cmds("give_item", {"player": "@a", "item": "diamond; /op Steve"})
    assert cmds == []


# ---------------------------------------------------------------------------
# change_weather
# ---------------------------------------------------------------------------


def test_valid_weather():
    for w in ("clear", "rain", "thunder"):
        cmd = _cmd("change_weather", {"weather_type": w})
        assert cmd is not None
        assert w in cmd["command"]


def test_invalid_weather():
    cmds = _cmds("change_weather", {"weather_type": "tornado"})
    assert cmds == []


def test_weather_duration_clamped():
    cmd = _cmd("change_weather", {"weather_type": "rain", "duration": 999999})
    assert "24000" in cmd["command"]  # max


# ---------------------------------------------------------------------------
# set_time
# ---------------------------------------------------------------------------


def test_valid_times():
    for t in ("day", "noon", "sunset", "night", "midnight", "sunrise"):
        assert _cmd("set_time", {"time": t}) is not None


def test_invalid_time():
    cmds = _cmds("set_time", {"time": "apocalypse"})
    assert cmds == []


# ---------------------------------------------------------------------------
# set_difficulty
# ---------------------------------------------------------------------------


def test_valid_difficulties():
    for d in ("peaceful", "easy", "normal", "hard"):
        cmd = _cmd("set_difficulty", {"difficulty": d})
        assert cmd is not None
        assert d in cmd["command"]


def test_invalid_difficulty():
    cmds = _cmds("set_difficulty", {"difficulty": "impossible"})
    assert cmds == []


# ---------------------------------------------------------------------------
# clear_item
# ---------------------------------------------------------------------------


def test_clear_specific_item():
    cmd = _cmd("clear_item", {"player": "Steve", "item": "diamond"})
    assert "clear Steve minecraft:diamond" in cmd["command"]


def test_clear_all_items():
    cmd = _cmd("clear_item", {"player": "Steve"})
    assert cmd["command"] == "clear Steve"


def test_clear_invalid_item_blocked():
    cmds = _cmds("clear_item", {"player": "Steve", "item": "diamond; /op hacker"})
    assert cmds == []


def test_clear_invalid_player_blocked():
    cmds = _cmds("clear_item", {"player": "@e[type=zombie]"})
    assert cmds == []


# ---------------------------------------------------------------------------
# strike_lightning
# ---------------------------------------------------------------------------


def test_strike_lightning_valid():
    cmd = _cmd("strike_lightning", {"near_player": "Steve"})
    assert "lightning_bolt" in cmd["command"]
    assert cmd["target_player"] == "Steve"


def test_strike_lightning_custom_offset():
    cmd = _cmd("strike_lightning", {"near_player": "Steve", "offset": "~5 ~ ~-3"})
    assert "~5 ~ ~-3" in cmd["command"]


def test_strike_lightning_invalid_offset_blocked():
    cmds = _cmds("strike_lightning", {"near_player": "Steve", "offset": "$(rm -rf /)"})
    assert cmds == []


# ---------------------------------------------------------------------------
# play_sound
# ---------------------------------------------------------------------------


def test_play_sound_valid():
    cmd = _cmd("play_sound", {"sound": "ambient.cave", "target_player": "Steve"})
    assert "playsound" in cmd["command"]
    assert "minecraft:ambient.cave" in cmd["command"]


def test_play_sound_auto_prefix():
    cmd = _cmd("play_sound", {"sound": "mob.wither.spawn"})
    assert "minecraft:mob.wither.spawn" in cmd["command"]


def test_play_sound_already_prefixed():
    cmd = _cmd("play_sound", {"sound": "minecraft:mob.ghast.scream"})
    assert cmd["command"].count("minecraft:") == 1


def test_play_sound_invalid_chars_blocked():
    cmds = _cmds("play_sound", {"sound": "sound; malicious"})
    assert cmds == []


# ---------------------------------------------------------------------------
# teleport_player
# ---------------------------------------------------------------------------


def test_teleport_valid():
    cmd = _cmd("teleport_player", {"player": "Steve", "x": 100, "y": 64, "z": -200})
    assert "tp Steve 100 64 -200" in cmd["command"]


def test_teleport_invalid_player_blocked():
    cmds = _cmds("teleport_player", {"player": "@e"})
    assert cmds == []


# ---------------------------------------------------------------------------
# assign_mission
# ---------------------------------------------------------------------------


def test_assign_mission_basic():
    cmds = _cmds("assign_mission", {"player": "Steve", "mission_title": "Find Diamonds"})
    assert len(cmds) >= 2  # at least title + broadcast
    assert any("title Steve title" in c["command"] for c in cmds)
    assert any("Find Diamonds" in c["command"] for c in cmds)


def test_assign_mission_with_subtitle():
    cmds = _cmds("assign_mission", {
        "player": "Steve",
        "mission_title": "Explore",
        "mission_description": "Find the stronghold",
    })
    assert len(cmds) == 3  # subtitle + title + broadcast
    assert any("subtitle" in c["command"] for c in cmds)


def test_assign_mission_with_reward():
    cmds = _cmds("assign_mission", {
        "player": "Steve",
        "mission_title": "Slay",
        "reward_hint": "diamond sword",
    })
    broadcast = [c for c in cmds if "tellraw" in c["command"]]
    assert any("diamond sword" in c["command"] for c in broadcast)


def test_assign_mission_invalid_player():
    cmds = _cmds("assign_mission", {"player": "@e", "mission_title": "Test"})
    assert cmds == []


# ---------------------------------------------------------------------------
# build_schematic — direction math
# ---------------------------------------------------------------------------


def _translate_build(args: dict, player_context: dict) -> tuple[list[dict], dict]:
    """Translate a build_schematic call with patched catalog validation."""
    tc = _make_tool_call("build_schematic", args)
    with patch("server.commands.build_schematic_command") as mock_build:
        # Make build_schematic_command pass through coordinates into a command dict
        def capture(bp_id, x, y, z, rotation):
            return {"type": "build_schematic", "blueprint_id": bp_id,
                    "x": x, "y": y, "z": z, "rotation": rotation}
        mock_build.side_effect = capture
        return translate_tool_calls([tc], player_context=player_context)


def test_build_in_front_south():
    """Player facing south, build in front = positive Z."""
    player_ctx = {"steve": {"x": 100, "y": 64, "z": 200, "facing": "S"}}
    cmds, _ = _translate_build(
        {"blueprint_id": "test", "near_player": "Steve", "in_front": True, "distance": "near"},
        player_ctx)
    assert len(cmds) == 1
    assert cmds[0]["x"] == 100
    assert cmds[0]["z"] == 210  # S = +Z, near = 10
    assert cmds[0]["y"] == 64


def test_build_explicit_direction_east():
    player_ctx = {"steve": {"x": 0, "y": 64, "z": 0, "facing": "N"}}
    cmds, _ = _translate_build(
        {"blueprint_id": "test", "near_player": "Steve",
         "in_front": False, "direction": "E", "distance": "medium"},
        player_ctx)
    assert cmds[0]["x"] == 25  # E = +X, medium = 25
    assert cmds[0]["z"] == 0


def test_build_diagonal_scales_distance():
    """Diagonal direction scales by 0.7 to maintain similar total distance."""
    player_ctx = {"steve": {"x": 0, "y": 64, "z": 0, "facing": "N"}}
    cmds, _ = _translate_build(
        {"blueprint_id": "test", "near_player": "Steve",
         "in_front": False, "direction": "NE", "distance": "near"},
        player_ctx)
    assert cmds[0]["x"] == 7   # int(10 * 0.7)
    assert cmds[0]["z"] == -7  # NE = +X, -Z


def test_build_invalid_direction_falls_back_to_north():
    player_ctx = {"steve": {"x": 0, "y": 64, "z": 0, "facing": "N"}}
    cmds, _ = _translate_build(
        {"blueprint_id": "test", "near_player": "Steve",
         "in_front": False, "direction": "INVALID", "distance": "near"},
        player_ctx)
    assert cmds[0]["x"] == 0
    assert cmds[0]["z"] == -10  # N = -Z


def test_build_missing_player_produces_no_commands():
    cmds, _ = _translate_build({"blueprint_id": "test", "near_player": ""}, {})
    assert cmds == []


def test_build_unknown_player_produces_no_commands():
    player_ctx = {"steve": {"x": 0, "y": 64, "z": 0, "facing": "N"}}
    cmds, _ = _translate_build(
        {"blueprint_id": "test", "near_player": "Unknown"}, player_ctx)
    assert cmds == []


# ---------------------------------------------------------------------------
# get_schematic_tool_results
# ---------------------------------------------------------------------------


def test_schematic_search_returns_results():
    tc = _make_tool_call("search_schematics", {"query": "iron farm"})
    with patch("server.commands.search_schematics", return_value="mock results"):
        results = get_schematic_tool_results([tc])
    assert results["tc_1"] == "mock results"


def test_schematic_search_invalid_json_returns_error():
    tc = types.SimpleNamespace()
    tc.id = "tc_bad"
    tc.function = types.SimpleNamespace()
    tc.function.name = "search_schematics"
    tc.function.arguments = "{bad json"
    results = get_schematic_tool_results([tc])
    assert "ERROR" in results["tc_bad"]
