"""Tests for command translation — the security boundary between LLM and Minecraft.

All tests go through the public interface: translate_tool_calls() and
get_schematic_tool_results(). This ensures the tests describe WHAT the
system does (blocks dangerous items, clamps durations, etc.) rather than
HOW it's implemented internally.

Tool call arguments are validated through pydantic models with helpful error
messages. Player names are validated against the server whitelist.
"""

import json
import types
from unittest.mock import patch

import pytest

from server.commands import (
    BLOCKED_ITEMS,
    get_schematic_tool_results,
    get_whitelist_names as _real_get_whitelist_names,
    translate_tool_calls,
)


# All tests use a mock whitelist so player names aren't rejected
# against the real server whitelist
_TEST_WHITELIST = {"Steve", "Alex", "aeBRER", "Player_1", "testplayer"}


@pytest.fixture(autouse=True)
def mock_whitelist():
    """Tests use a mock whitelist so test player names pass validation."""
    with patch("server.commands.get_whitelist_names", return_value=_TEST_WHITELIST):
        yield


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
                 {"message": "Secret", "target_player": "Steve"})
    assert len(cmds) == 2
    # First is whisper to target, second is notification to others
    assert "Steve" in cmds[0]["command"]
    assert "whispered" in cmds[0]["command"]
    assert "whispers to Steve" in cmds[1]["command"]


def test_extra_fields_are_silently_ignored():
    """Unknown fields in tool call args don't break translation."""
    cmd = _cmd("send_message", {"message": "Big text", "style": "title", "color": "red"})
    assert "tellraw @a" in cmd["command"]
    assert "Big text" in cmd["command"]


def test_long_message_wraps_into_multiple_commands():
    long_msg = "A " * 100  # 200 chars, well over chat width
    cmds = _cmds("send_message", {"message": long_msg})
    # Should produce multiple commands from word-wrapping
    assert len(cmds) > 1
    # First command has the god name prefix
    assert "The Kind God" in cmds[0]["command"]
    # No single command contains the full original message
    for cmd in cmds:
        assert "A " * 100 not in cmd["command"]


def test_message_newlines_split_into_lines():
    cmds = _cmds("send_message", {"message": "line1\nline2", "style": "chat"})
    # Newlines produce separate tellraw commands
    assert len(cmds) >= 2
    assert "line1" in cmds[0]["command"]
    assert "line2" in cmds[1]["command"]


def test_message_invalid_target_produces_no_commands():
    cmds = _cmds("send_message",
                 {"message": "Hi", "target_player": "@e[type=zombie]"})
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
    for name in ("a" * 33, "player;drop table"):
        cmds = _cmds("give_effect", {"target_player": name, "effect": "speed"})
        assert cmds == []


def test_player_not_on_whitelist_rejected():
    """Player names not on the whitelist produce a helpful error."""
    commands, errors = _translate_one("give_effect",
                                      {"target_player": "FakePlayer", "effect": "speed"})
    assert commands == []
    assert len(errors) == 1
    error_msg = list(errors.values())[0]
    assert "not on the whitelist" in error_msg
    assert "Steve" in error_msg  # should list valid players


def test_whitelist_check_is_case_insensitive():
    """Whitelist matching is case-insensitive."""
    cmd = _cmd("give_effect", {"target_player": "steve", "effect": "speed"})
    assert cmd is not None
    cmd = _cmd("give_effect", {"target_player": "AEBRER", "effect": "speed"})
    assert cmd is not None


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
    assert len(cmds) == 1  # single tellraw announcement
    assert "tellraw @a" in cmds[0]["command"]
    assert "Find Diamonds" in cmds[0]["command"]
    assert "Steve" in cmds[0]["command"]


def test_assign_mission_with_description():
    cmds = _cmds("assign_mission", {
        "player": "Steve",
        "mission_title": "Explore",
        "mission_description": "Find the stronghold",
    })
    assert len(cmds) == 1
    assert "Find the stronghold" in cmds[0]["command"]


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


def _translate_build(args: dict, player_context: dict,
                     requesting_player: str | None = None) -> tuple[list[dict], dict]:
    """Translate a build_schematic call with patched catalog validation."""
    tc = _make_tool_call("build_schematic", args)
    with patch("server.commands.build_schematic_command") as mock_build:
        # Make build_schematic_command pass through coordinates into a command dict
        def capture(bp_id, x, y, z, rotation):
            return {"type": "build_schematic", "blueprint_id": bp_id,
                    "x": x, "y": y, "z": z, "rotation": rotation}
        mock_build.side_effect = capture
        return translate_tool_calls([tc], player_context=player_context,
                                    requesting_player=requesting_player)


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


def test_build_defaults_near_player_to_requesting_player():
    """When LLM omits near_player, it defaults to the requesting (praying) player."""
    player_ctx = {"testplayer": {"x": 100, "y": 64, "z": 200, "facing": "N"}}
    cmds, _ = _translate_build(
        {"blueprint_id": "test", "in_front": True, "distance": "near"},
        player_ctx, requesting_player="testplayer")
    assert len(cmds) == 1
    assert cmds[0]["x"] == 100
    assert cmds[0]["z"] == 190  # N = -Z, near = 10


def test_build_llm_misspelled_player_falls_back_to_requesting():
    """When LLM misspells near_player, falls back to requesting player."""
    player_ctx = {"testplayer": {"x": 100, "y": 64, "z": 200, "facing": "N"}}
    cmds, _ = _translate_build(
        {"blueprint_id": "test", "near_player": "tstplayer", "in_front": True, "distance": "near"},
        player_ctx, requesting_player="testplayer")
    assert len(cmds) == 1
    assert cmds[0]["x"] == 100
    assert cmds[0]["z"] == 190  # Falls back to testplayer's position


# ---------------------------------------------------------------------------
# Validation error messages (helpful feedback for the LLM)
# ---------------------------------------------------------------------------


def test_invalid_mob_error_lists_valid_mobs():
    """Error message for invalid mob includes the list of valid mob types."""
    _, errors = _translate_one("summon_mob", {"mob_type": "wither"})
    assert len(errors) == 1
    error_msg = list(errors.values())[0]
    assert "zombie" in error_msg  # at least one valid mob listed
    assert "cow" in error_msg


def test_invalid_effect_error_lists_valid_effects():
    """Error message for invalid effect includes valid effect names."""
    _, errors = _translate_one("give_effect", {"target_player": "@a", "effect": "super_power"})
    assert len(errors) == 1
    error_msg = list(errors.values())[0]
    assert "speed" in error_msg
    assert "regeneration" in error_msg


def test_blocked_item_error_mentions_restricted():
    """Error message for blocked items says it's restricted."""
    _, errors = _translate_one("give_item", {"player": "@a", "item": "command_block"})
    assert len(errors) == 1
    error_msg = list(errors.values())[0]
    assert "restricted" in error_msg.lower() or "command_block" in error_msg


def test_validation_error_includes_field_name():
    """Error messages identify which field is invalid."""
    _, errors = _translate_one("summon_mob", {"mob_type": "wither", "location": "$(rm -rf /)"})
    assert len(errors) == 1
    error_msg = list(errors.values())[0]
    # Should mention at least one of the failing fields
    assert "mob_type" in error_msg or "location" in error_msg


def test_unknown_tool_error_is_descriptive():
    """Unknown tool names produce a clear error, not a traceback."""
    _, errors = _translate_one("hack_server", {"target": "everything"})
    error_msg = list(errors.values())[0]
    assert "hack_server" in error_msg.lower() or "unknown" in error_msg.lower()


# ---------------------------------------------------------------------------
# Whitelist file loading (uses the real function, bypasses autouse mock)
# ---------------------------------------------------------------------------


def test_whitelist_loads_from_file(tmp_path):
    """Whitelist names are loaded from the Paper server whitelist file."""
    import server.commands as cmd_module

    whitelist_file = tmp_path / "whitelist.json"
    whitelist_file.write_text(json.dumps([
        {"uuid": "abc", "name": "TestPlayer1"},
        {"uuid": "def", "name": "TestPlayer2"},
    ]))

    old_file = cmd_module.WHITELIST_FILE
    old_cache = cmd_module._whitelist_cache
    old_mtime = cmd_module._whitelist_mtime
    try:
        cmd_module.WHITELIST_FILE = whitelist_file
        cmd_module._whitelist_cache = None
        cmd_module._whitelist_mtime = 0
        # Call the real function (stashed at import before autouse mock)
        names = _real_get_whitelist_names()
        assert names == {"TestPlayer1", "TestPlayer2"}
    finally:
        cmd_module.WHITELIST_FILE = old_file
        cmd_module._whitelist_cache = old_cache
        cmd_module._whitelist_mtime = old_mtime


def test_whitelist_missing_file_returns_empty_set():
    """Missing whitelist file returns empty set (graceful degradation)."""
    import server.commands as cmd_module
    from pathlib import Path

    old_file = cmd_module.WHITELIST_FILE
    old_cache = cmd_module._whitelist_cache
    old_mtime = cmd_module._whitelist_mtime
    try:
        cmd_module.WHITELIST_FILE = Path("/nonexistent/whitelist.json")
        cmd_module._whitelist_cache = None
        cmd_module._whitelist_mtime = 0
        names = _real_get_whitelist_names()
        assert names == set()
    finally:
        cmd_module.WHITELIST_FILE = old_file
        cmd_module._whitelist_cache = old_cache
        cmd_module._whitelist_mtime = old_mtime


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
