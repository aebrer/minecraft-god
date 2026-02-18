"""Tests for the God of Digging — classify priority, pydantic validation, memory FILO,
command building, and tool call handling.

Tests cover public interfaces: classify_divine_request() with dig priority,
DigResponse/shape param validation, DigMemory FILO behavior, and DigGod's
deterministic command-building logic (LLM calls mocked at the boundary).
"""

import json
import time
import types

import pytest

from server.prayer_queue import classify_divine_request, is_divine_request, DivineRequest
from server.dig_god import (
    DigGod,
    DigResponse,
    HoleParams,
    TunnelParams,
    StaircaseParams,
    ShaftParams,
    MemoryResponse,
    _validate_dig_params,
)
from server.dig_memory import DigMemory
from server.config import DIG_MAX_WIDTH, DIG_MAX_DEPTH, DIG_MAX_LENGTH, DIG_MAX_HEIGHT, DIG_MAX_STEPS


# ---------------------------------------------------------------------------
# Keyword classification — dig priority
# ---------------------------------------------------------------------------


def test_classify_dig_keyword():
    assert classify_divine_request("dig me a hole") == "dig"


def test_classify_hole_keyword():
    assert classify_divine_request("make a hole here") == "dig"


def test_classify_tunnel_keyword():
    assert classify_divine_request("make a tunnel please") == "dig"


def test_classify_excavate_keyword():
    assert classify_divine_request("excavate this area") == "dig"


def test_classify_shaft_keyword():
    assert classify_divine_request("I need a shaft down") == "dig"


def test_classify_staircase_keyword():
    assert classify_divine_request("build a staircase here") == "dig"


def test_classify_dig_beats_prayer():
    """Dig keywords take priority over prayer keywords."""
    assert classify_divine_request("God please dig me a hole") == "dig"


def test_classify_dig_beats_herald():
    """Dig keywords take priority over herald keywords."""
    assert classify_divine_request("herald dig a tunnel") == "dig"


def test_classify_remember_beats_dig():
    """Remember still has highest priority."""
    assert classify_divine_request("remember the hole you dug") == "remember"


def test_classify_mine_not_a_dig_keyword():
    """'mine' is intentionally NOT a dig keyword — too ambiguous."""
    assert classify_divine_request("I'm mining iron") is None


def test_classify_dig_case_insensitive():
    assert classify_divine_request("DIG a HOLE") == "dig"
    assert classify_divine_request("TUNNEL through here") == "dig"
    assert classify_divine_request("EXCAVATE") == "dig"


def test_is_divine_request_true_for_dig():
    assert is_divine_request("dig a hole here") is True


def test_dig_request_context_label():
    """Dig requests use 'REQUESTING PLAYER' label in context."""
    req = DivineRequest(
        player="Steve",
        message="dig me a hole",
        request_type="dig",
        timestamp=time.time(),
        player_snapshot={
            "name": "Steve",
            "location": {"x": 100, "y": 64, "z": -200},
            "dimension": "overworld",
            "biome": "plains",
            "facing": "N",
            "lookingVertical": "ahead",
            "health": 20.0, "maxHealth": 20.0,
            "foodLevel": 18, "level": 5,
            "armor": [], "inventory": {},
        },
        recent_chat=[{"player": "Steve", "message": "dig me a hole"}],
    )
    ctx = req.build_context()
    assert "REQUESTING PLAYER" in ctx


# ---------------------------------------------------------------------------
# Pydantic validation — DigResponse
# ---------------------------------------------------------------------------


def test_dig_response_valid_hole():
    resp = DigResponse(
        alias="Bore-is Johnson",
        announcement="Time to make a hole!",
        action="dig_hole",
        params={"near_player": "Steve", "width": 5, "depth": 10},
        review="8.5/10 — Excellent void.",
    )
    assert resp.action == "dig_hole"
    assert resp.alias == "Bore-is Johnson"


def test_dig_response_valid_tunnel():
    resp = DigResponse(
        alias="Tunnel Vision Turner",
        announcement="Let's bore through!",
        action="dig_tunnel",
        params={"near_player": "Steve", "width": 3, "height": 3, "length": 20, "direction": "N"},
        review="7/10",
    )
    assert resp.action == "dig_tunnel"


def test_dig_response_valid_staircase():
    resp = DigResponse(
        alias="Shaft-speare",
        announcement="To descend!",
        action="dig_staircase",
        params={"near_player": "Steve", "width": 2, "steps": 15, "direction": "S", "going": "down"},
        review="9/10",
    )
    assert resp.action == "dig_staircase"


def test_dig_response_valid_shaft():
    resp = DigResponse(
        alias="Dig Jagger",
        announcement="Going down!",
        action="dig_shaft",
        params={"near_player": "Steve", "width": 2, "length": 20, "going": "down"},
        review="6/10",
    )
    assert resp.action == "dig_shaft"


def test_dig_response_rejects_invalid_action():
    with pytest.raises(Exception):
        DigResponse(
            alias="Test",
            announcement="test",
            action="dig_canyon",
            params={"near_player": "Steve", "width": 5, "depth": 10},
            review="test",
        )


def test_dig_response_rejects_long_announcement():
    with pytest.raises(Exception):
        DigResponse(
            alias="Test",
            announcement="x" * 201,
            action="dig_hole",
            params={"near_player": "Steve", "width": 5, "depth": 10},
            review="ok",
        )


def test_dig_response_rejects_long_review():
    with pytest.raises(Exception):
        DigResponse(
            alias="Test",
            announcement="ok",
            action="dig_hole",
            params={"near_player": "Steve", "width": 5, "depth": 10},
            review="x" * 201,
        )


def test_dig_response_accepts_max_length_announcement():
    """Exactly 200 chars should pass."""
    resp = DigResponse(
        alias="Test",
        announcement="x" * 200,
        action="dig_hole",
        params={"near_player": "Steve", "width": 5, "depth": 10},
        review="ok",
    )
    assert len(resp.announcement) == 200


def test_memory_response_valid():
    resp = MemoryResponse(memory="Dug a 10x10 hole for Steve as Bore-is Johnson. 8/10.")
    assert "Steve" in resp.memory


def test_memory_response_rejects_too_long():
    with pytest.raises(Exception):
        MemoryResponse(memory="x" * 501)


# ---------------------------------------------------------------------------
# Shape-specific param validation
# ---------------------------------------------------------------------------


def test_hole_params_valid():
    p = HoleParams(near_player="Steve", width=10, depth=20)
    assert p.width == 10
    assert p.depth == 20


def test_hole_params_at_max():
    p = HoleParams(near_player="Steve", width=DIG_MAX_WIDTH, depth=DIG_MAX_DEPTH)
    assert p.width == DIG_MAX_WIDTH
    assert p.depth == DIG_MAX_DEPTH


def test_hole_params_rejects_width_over_max():
    with pytest.raises(Exception):
        HoleParams(near_player="Steve", width=DIG_MAX_WIDTH + 1, depth=10)


def test_hole_params_rejects_zero_width():
    with pytest.raises(Exception):
        HoleParams(near_player="Steve", width=0, depth=10)


def test_hole_params_rejects_zero_depth():
    with pytest.raises(Exception):
        HoleParams(near_player="Steve", width=5, depth=0)


def test_hole_params_rejects_negative_depth():
    with pytest.raises(Exception):
        HoleParams(near_player="Steve", width=5, depth=-1)


def test_tunnel_params_valid():
    p = TunnelParams(near_player="Steve", width=3, height=3, length=20, direction="N")
    assert p.direction == "N"


def test_tunnel_params_all_directions():
    """All four cardinal directions are accepted."""
    for d in ("N", "S", "E", "W"):
        p = TunnelParams(near_player="Steve", width=3, height=3, length=10, direction=d)
        assert p.direction == d


def test_tunnel_params_rejects_diagonal_direction():
    with pytest.raises(Exception):
        TunnelParams(near_player="Steve", width=3, height=3, length=20, direction="NW")


def test_tunnel_params_rejects_height_over_max():
    with pytest.raises(Exception):
        TunnelParams(near_player="Steve", width=3, height=DIG_MAX_HEIGHT + 1, length=20, direction="N")


def test_tunnel_params_rejects_length_over_max():
    with pytest.raises(Exception):
        TunnelParams(near_player="Steve", width=3, height=3, length=DIG_MAX_LENGTH + 1, direction="N")


def test_staircase_params_valid():
    p = StaircaseParams(near_player="Steve", width=3, steps=20, direction="S", going="down")
    assert p.going == "down"


def test_staircase_params_going_up():
    p = StaircaseParams(near_player="Steve", width=3, steps=10, direction="N", going="up")
    assert p.going == "up"


def test_staircase_params_rejects_invalid_going():
    with pytest.raises(Exception):
        StaircaseParams(near_player="Steve", width=3, steps=20, direction="S", going="sideways")


def test_staircase_params_rejects_steps_over_max():
    with pytest.raises(Exception):
        StaircaseParams(near_player="Steve", width=3, steps=DIG_MAX_STEPS + 1, direction="S", going="down")


def test_shaft_params_valid():
    p = ShaftParams(near_player="Steve", width=2, length=30, going="down")
    assert p.going == "down"


def test_shaft_params_going_up():
    p = ShaftParams(near_player="Steve", width=2, length=20, going="up")
    assert p.going == "up"


def test_shaft_params_rejects_invalid_going():
    with pytest.raises(Exception):
        ShaftParams(near_player="Steve", width=2, length=30, going="sideways")


def test_validate_dig_params_dispatches_to_hole():
    result = _validate_dig_params("dig_hole", {"near_player": "Steve", "width": 5, "depth": 10})
    assert isinstance(result, HoleParams)


def test_validate_dig_params_dispatches_to_tunnel():
    result = _validate_dig_params("dig_tunnel", {
        "near_player": "Steve", "width": 3, "height": 3, "length": 20, "direction": "N"})
    assert isinstance(result, TunnelParams)


def test_validate_dig_params_dispatches_to_staircase():
    result = _validate_dig_params("dig_staircase", {
        "near_player": "Steve", "width": 3, "steps": 10, "direction": "S", "going": "down"})
    assert isinstance(result, StaircaseParams)


def test_validate_dig_params_dispatches_to_shaft():
    result = _validate_dig_params("dig_shaft", {
        "near_player": "Steve", "width": 2, "length": 20, "going": "down"})
    assert isinstance(result, ShaftParams)


def test_validate_dig_params_unknown_action():
    with pytest.raises(ValueError, match="Unknown dig action"):
        _validate_dig_params("dig_canyon", {"near_player": "Steve"})


def test_validate_dig_params_rejects_missing_required_field():
    """Missing 'depth' for dig_hole should fail validation."""
    with pytest.raises(Exception):
        _validate_dig_params("dig_hole", {"near_player": "Steve", "width": 5})


# ---------------------------------------------------------------------------
# _build_dig_commands — deterministic command generation
# ---------------------------------------------------------------------------


def _make_dig_response(**overrides) -> DigResponse:
    """Build a valid DigResponse for testing."""
    defaults = {
        "alias": "Bore-is Johnson",
        "announcement": "Time to dig!",
        "action": "dig_hole",
        "params": {"near_player": "Steve", "width": 5, "depth": 10},
        "review": "8/10 — Excellent.",
    }
    defaults.update(overrides)
    return DigResponse(**defaults)


def _make_player_context(player="Steve", x=100, y=64, z=-200, facing="N"):
    """Build a player_context dict for testing."""
    return {player.lower(): {"x": x, "y": y, "z": z, "facing": facing}}


def test_build_dig_commands_produces_announcement():
    """First command should be a tellraw with the announcement and alias."""
    god = DigGod.__new__(DigGod)  # skip __init__ (avoids disk I/O)
    resp = _make_dig_response()
    params = HoleParams(near_player="Steve", width=5, depth=10)
    ctx = _make_player_context()

    commands = god._build_dig_commands(resp, params, ctx, "Steve")

    announce_cmd = commands[0]["command"]
    assert "tellraw" in announce_cmd
    assert "Bore-is Johnson" in announce_cmd
    assert "Time to dig!" in announce_cmd


def test_build_dig_commands_produces_dig_command():
    """Second command should be the typed dig dict with correct params."""
    god = DigGod.__new__(DigGod)
    resp = _make_dig_response()
    params = HoleParams(near_player="Steve", width=5, depth=10)
    ctx = _make_player_context()

    commands = god._build_dig_commands(resp, params, ctx, "Steve")

    dig_cmd = commands[1]
    assert dig_cmd["type"] == "dig_hole"
    assert dig_cmd["near_player"] == "Steve"
    assert dig_cmd["player_x"] == 100
    assert dig_cmd["player_y"] == 64
    assert dig_cmd["player_z"] == -200
    assert dig_cmd["width"] == 5
    assert dig_cmd["depth"] == 10


def test_build_dig_commands_produces_review():
    """Third command should be a tellraw with the review."""
    god = DigGod.__new__(DigGod)
    resp = _make_dig_response()
    params = HoleParams(near_player="Steve", width=5, depth=10)
    ctx = _make_player_context()

    commands = god._build_dig_commands(resp, params, ctx, "Steve")

    review_cmd = commands[2]["command"]
    assert "tellraw" in review_cmd
    assert "8/10" in review_cmd


def test_build_dig_commands_produces_sound():
    """Fourth command should be a playsound."""
    god = DigGod.__new__(DigGod)
    resp = _make_dig_response()
    params = HoleParams(near_player="Steve", width=5, depth=10)
    ctx = _make_player_context()

    commands = god._build_dig_commands(resp, params, ctx, "Steve")

    sound_cmd = commands[3]["command"]
    assert "playsound" in sound_cmd
    assert "entity.warden.dig" in sound_cmd


def test_build_dig_commands_total_count():
    """A successful dig produces exactly 4 commands: announce, dig, review, sound."""
    god = DigGod.__new__(DigGod)
    resp = _make_dig_response()
    params = HoleParams(near_player="Steve", width=5, depth=10)
    ctx = _make_player_context()

    commands = god._build_dig_commands(resp, params, ctx, "Steve")
    assert len(commands) == 4


def test_build_dig_commands_falls_back_to_requesting_player():
    """When LLM's near_player isn't in context, falls back to requesting_player."""
    god = DigGod.__new__(DigGod)
    resp = _make_dig_response(params={"near_player": "Stve", "width": 5, "depth": 10})  # typo
    params = HoleParams(near_player="Stve", width=5, depth=10)
    ctx = _make_player_context(player="Steve")  # correct name in context

    commands = god._build_dig_commands(resp, params, ctx, "Steve")

    dig_cmd = commands[1]
    assert dig_cmd["near_player"] == "Steve"  # fell back to requesting_player
    assert dig_cmd["player_x"] == 100


def test_build_dig_commands_error_when_no_player_data():
    """When player can't be found at all, returns announcement + error message."""
    god = DigGod.__new__(DigGod)
    resp = _make_dig_response()
    params = HoleParams(near_player="Steve", width=5, depth=10)

    commands = god._build_dig_commands(resp, params, {}, None)  # empty context

    assert len(commands) == 2  # announcement + error
    assert "cannot find you" in commands[1]["command"].lower()


def test_build_dig_commands_includes_player_facing():
    """Dig command includes the player's facing direction."""
    god = DigGod.__new__(DigGod)
    resp = _make_dig_response()
    params = HoleParams(near_player="Steve", width=5, depth=10)
    ctx = _make_player_context(facing="SE")

    commands = god._build_dig_commands(resp, params, ctx, "Steve")

    dig_cmd = commands[1]
    assert dig_cmd["player_facing"] == "SE"


def test_build_dig_commands_tunnel_includes_direction():
    """Tunnel dig command includes the tunnel direction param."""
    god = DigGod.__new__(DigGod)
    resp = _make_dig_response(
        action="dig_tunnel",
        params={"near_player": "Steve", "width": 3, "height": 3, "length": 20, "direction": "E"},
    )
    params = TunnelParams(near_player="Steve", width=3, height=3, length=20, direction="E")
    ctx = _make_player_context()

    commands = god._build_dig_commands(resp, params, ctx, "Steve")

    dig_cmd = commands[1]
    assert dig_cmd["type"] == "dig_tunnel"
    assert dig_cmd["direction"] == "E"
    assert dig_cmd["length"] == 20
    assert dig_cmd["height"] == 3


def test_build_dig_commands_staircase_includes_going():
    """Staircase dig command includes going direction."""
    god = DigGod.__new__(DigGod)
    resp = _make_dig_response(
        action="dig_staircase",
        params={"near_player": "Steve", "width": 2, "steps": 15, "direction": "S", "going": "down"},
    )
    params = StaircaseParams(near_player="Steve", width=2, steps=15, direction="S", going="down")
    ctx = _make_player_context()

    commands = god._build_dig_commands(resp, params, ctx, "Steve")

    dig_cmd = commands[1]
    assert dig_cmd["type"] == "dig_staircase"
    assert dig_cmd["going"] == "down"
    assert dig_cmd["steps"] == 15


# ---------------------------------------------------------------------------
# _handle_tool_calls — non-dig actions
# ---------------------------------------------------------------------------


def _make_tool_call(name: str, args: dict, call_id: str = "tc_1"):
    """Build a fake LLM tool call object matching the OpenAI SDK shape."""
    tc = types.SimpleNamespace()
    tc.id = call_id
    tc.function = types.SimpleNamespace()
    tc.function.name = name
    tc.function.arguments = json.dumps(args)
    return tc


def test_handle_tool_calls_send_message():
    """send_message produces a tellraw command."""
    god = DigGod.__new__(DigGod)
    tc = _make_tool_call("send_message", {"message": "Greetings, mortal!"})

    commands = god._handle_tool_calls([tc], None, None)

    assert len(commands) == 1
    assert "tellraw" in commands[0]["command"]
    assert "Greetings, mortal!" in commands[0]["command"]


def test_handle_tool_calls_send_message_uses_dig_god_style():
    """send_message from dig god uses the dig_god chat style."""
    god = DigGod.__new__(DigGod)
    tc = _make_tool_call("send_message", {"message": "Hello!"})

    commands = god._handle_tool_calls([tc], None, None)

    assert "God of Digging" in commands[0]["command"]


def test_handle_tool_calls_pray_to_kind_god():
    """pray_to_kind_god produces a sentinel dict for main.py to intercept."""
    god = DigGod.__new__(DigGod)
    tc = _make_tool_call("pray_to_kind_god", {
        "message": "This player wants a diamond pickaxe",
        "player": "Steve",
    })

    commands = god._handle_tool_calls([tc], None, None)

    assert len(commands) == 1
    assert commands[0]["type"] == "pray_to_kind_god"
    assert commands[0]["player"] == "Steve"
    assert "diamond pickaxe" in commands[0]["message"]


def test_handle_tool_calls_pray_falls_back_to_requesting_player():
    """pray_to_kind_god uses requesting_player when LLM omits the player arg."""
    god = DigGod.__new__(DigGod)
    tc = _make_tool_call("pray_to_kind_god", {"message": "needs items"})

    commands = god._handle_tool_calls([tc], None, "Alex")

    assert commands[0]["player"] == "Alex"


def test_handle_tool_calls_undo():
    """undo_last_dig produces an undo_last_build command for the shared history."""
    god = DigGod.__new__(DigGod)
    tc = _make_tool_call("undo_last_dig", {})

    commands = god._handle_tool_calls([tc], None, None)

    assert len(commands) == 1
    assert commands[0]["type"] == "undo_last_build"


def test_handle_tool_calls_do_nothing():
    """do_nothing produces no commands."""
    god = DigGod.__new__(DigGod)
    tc = _make_tool_call("do_nothing", {"reason": "Not a dig request"})

    commands = god._handle_tool_calls([tc], None, None)

    assert commands == []


def test_handle_tool_calls_unknown_tool_ignored():
    """Unknown tool names are silently skipped."""
    god = DigGod.__new__(DigGod)
    tc = _make_tool_call("teleport_player", {"player": "Steve", "x": 0, "y": 64, "z": 0})

    commands = god._handle_tool_calls([tc], None, None)

    assert commands == []


def test_handle_tool_calls_multiple():
    """Multiple tool calls are processed in order."""
    god = DigGod.__new__(DigGod)
    tc1 = _make_tool_call("send_message", {"message": "First!"}, call_id="tc_1")
    tc2 = _make_tool_call("pray_to_kind_god", {"message": "needs items", "player": "Steve"}, call_id="tc_2")

    commands = god._handle_tool_calls([tc1, tc2], None, None)

    assert len(commands) == 2
    assert "tellraw" in commands[0]["command"]
    assert commands[1]["type"] == "pray_to_kind_god"


# ---------------------------------------------------------------------------
# DigMemory — FILO deque
# ---------------------------------------------------------------------------


def test_memory_add_and_retrieve(tmp_path):
    mem = DigMemory(tmp_path / "test_mem.json", max_entries=5)
    mem.add("Dug a great hole", {"player": "Steve", "shape": "hole"})
    assert len(mem.records) == 1
    assert mem.records[0]["memory"] == "Dug a great hole"
    assert mem.records[0]["player"] == "Steve"


def test_memory_auto_attaches_timestamp(tmp_path):
    mem = DigMemory(tmp_path / "test_mem.json", max_entries=5)
    mem.add("A hole", {"player": "Steve"})
    assert "timestamp" in mem.records[0]
    assert len(mem.records[0]["timestamp"]) > 0


def test_memory_evicts_oldest(tmp_path):
    mem = DigMemory(tmp_path / "test_mem.json", max_entries=3)
    for i in range(5):
        mem.add(f"Hole #{i}", {"player": "Steve", "shape": "hole"})
    assert len(mem.records) == 3
    # Oldest (0, 1) should be evicted; newest (2, 3, 4) remain
    assert mem.records[0]["memory"] == "Hole #2"
    assert mem.records[-1]["memory"] == "Hole #4"


def test_memory_evicts_at_exactly_max(tmp_path):
    """At max_entries, no eviction yet. One over triggers eviction."""
    mem = DigMemory(tmp_path / "test_mem.json", max_entries=3)
    for i in range(3):
        mem.add(f"Hole #{i}", {})
    assert len(mem.records) == 3
    assert mem.records[0]["memory"] == "Hole #0"  # all three present

    mem.add("Hole #3", {})
    assert len(mem.records) == 3
    assert mem.records[0]["memory"] == "Hole #1"  # oldest evicted


def test_memory_persists_to_disk(tmp_path):
    path = tmp_path / "test_mem.json"
    mem1 = DigMemory(path, max_entries=10)
    mem1.add("First hole", {"player": "Steve", "shape": "hole"})
    mem1.add("Second hole", {"player": "Alex", "shape": "tunnel"})

    # Load from same file
    mem2 = DigMemory(path, max_entries=10)
    assert len(mem2.records) == 2
    assert mem2.records[0]["memory"] == "First hole"
    assert mem2.records[1]["memory"] == "Second hole"


def test_memory_load_corrupt_file_starts_empty(tmp_path):
    """Corrupt JSON file is handled gracefully — starts empty."""
    path = tmp_path / "test_mem.json"
    path.write_text("not json at all{{{")

    mem = DigMemory(path, max_entries=5)
    assert len(mem.records) == 0


def test_memory_load_non_list_starts_empty(tmp_path):
    """A JSON file containing a dict instead of list starts empty."""
    path = tmp_path / "test_mem.json"
    path.write_text('{"oops": "wrong format"}')

    mem = DigMemory(path, max_entries=5)
    assert len(mem.records) == 0


def test_memory_load_truncates_to_max(tmp_path):
    """If file has more records than max_entries, only the newest are loaded."""
    path = tmp_path / "test_mem.json"
    records = [{"memory": f"Hole #{i}", "timestamp": "2026-01-01"} for i in range(20)]
    path.write_text(json.dumps(records))

    mem = DigMemory(path, max_entries=5)
    assert len(mem.records) == 5
    assert mem.records[0]["memory"] == "Hole #15"  # last 5


def test_memory_format_for_prompt_empty(tmp_path):
    mem = DigMemory(tmp_path / "test_mem.json", max_entries=5)
    text = mem.format_for_prompt()
    assert "Empty" in text
    assert "Make history" in text


def test_memory_format_for_prompt_includes_records(tmp_path):
    mem = DigMemory(tmp_path / "test_mem.json", max_entries=5)
    mem.add("Epic hole for Steve", {"player": "Steve", "shape": "hole", "dimensions": "10x10x20"})
    text = mem.format_for_prompt()
    assert "Epic hole for Steve" in text
    assert "Steve" in text
    assert "HOLE ARCHIVE" in text


def test_memory_format_includes_metadata_fields(tmp_path):
    """All metadata fields appear in the formatted prompt."""
    mem = DigMemory(tmp_path / "test_mem.json", max_entries=5)
    mem.add("Great tunnel", {
        "player": "Alex",
        "shape": "tunnel",
        "dimensions": "3x3x20 N",
        "location": "(100, 64, -200)",
    })
    text = mem.format_for_prompt()
    assert "Alex" in text
    assert "tunnel" in text
    assert "3x3x20 N" in text
    assert "(100, 64, -200)" in text


def test_memory_format_newest_first(tmp_path):
    mem = DigMemory(tmp_path / "test_mem.json", max_entries=5)
    mem.add("First", {"player": "A"})
    mem.add("Second", {"player": "B"})
    text = mem.format_for_prompt()
    # "Second" should appear before "First" (newest first)
    assert text.index("Second") < text.index("First")


def test_memory_nonexistent_file_starts_empty(tmp_path):
    """Loading from a path that doesn't exist yet works fine."""
    mem = DigMemory(tmp_path / "does_not_exist.json", max_entries=5)
    assert len(mem.records) == 0
