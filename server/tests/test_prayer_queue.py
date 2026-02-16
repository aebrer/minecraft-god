"""Tests for the divine request queue and keyword detection.

Tests cover the public interface: classify_divine_request(),
is_divine_request(), DivineRequest.build_context(), and
DivineRequestQueue.enqueue/dequeue/requeue.
"""

import asyncio
import time

from server.prayer_queue import (
    DivineRequest,
    DivineRequestQueue,
    MAX_ATTEMPTS,
    classify_divine_request,
    is_divine_request,
)


# ---------------------------------------------------------------------------
# classify_divine_request — single source of truth for keyword detection
# ---------------------------------------------------------------------------


def test_classify_prayer_keyword():
    assert classify_divine_request("God help me") == "prayer"


def test_classify_herald_keyword():
    assert classify_divine_request("herald tell me a story") == "herald"


def test_classify_no_keyword():
    assert classify_divine_request("nice base!") is None


def test_classify_prayer_takes_priority_over_herald():
    """When both prayer and herald keywords are present, prayer wins."""
    assert classify_divine_request("God please herald guide me") == "prayer"


def test_classify_remember_keyword():
    assert classify_divine_request("remember this place") == "remember"


def test_classify_prayer_takes_priority_over_remember():
    """Prayer keywords beat remember when both present."""
    assert classify_divine_request("God please remember me") == "prayer"


def test_classify_remember_takes_priority_over_herald():
    """Remember beats herald when both present."""
    assert classify_divine_request("remember herald") == "remember"


def test_classify_case_insensitive():
    assert classify_divine_request("GOD HELP ME") == "prayer"
    assert classify_divine_request("HERALD tell me") == "herald"
    assert classify_divine_request("REMEMBER this") == "remember"


def test_is_divine_request_true_for_prayer():
    assert is_divine_request("God help me") is True


def test_is_divine_request_true_for_herald():
    assert is_divine_request("herald guide me") is True


def test_is_divine_request_true_for_remember():
    assert is_divine_request("remember this") is True


def test_is_divine_request_false_for_regular_chat():
    assert is_divine_request("nice base!") is False


def test_classify_empty_string():
    assert classify_divine_request("") is None


# ---------------------------------------------------------------------------
# DivineRequest.build_context — context snapshot formatting
# ---------------------------------------------------------------------------


def _make_request(player: str = "Steve", message: str = "God help me",
                  request_type: str = "prayer", **snapshot_overrides) -> DivineRequest:
    snapshot = {
        "name": player,
        "location": {"x": 100, "y": 64, "z": -200},
        "dimension": "overworld",
        "biome": "plains",
        "facing": "N",
        "lookingVertical": "ahead",
        "health": 20.0,
        "maxHealth": 20.0,
        "foodLevel": 18,
        "level": 5,
        "armor": ["minecraft:iron_chestplate", "minecraft:air", "minecraft:air", "minecraft:air"],
        "mainHand": "minecraft:diamond_pickaxe",
        "inventory": {"cobblestone": 64, "diamond": 3, "torch": 12},
    }
    snapshot.update(snapshot_overrides)
    return DivineRequest(
        player=player,
        message=message,
        request_type=request_type,
        timestamp=time.time(),
        player_snapshot=snapshot,
        recent_chat=[
            {"player": player, "message": message},
            {"player": "Alex", "message": "good luck!"},
        ],
    )


def test_context_includes_player_position():
    req = _make_request()
    ctx = req.build_context()
    assert "x=100" in ctx
    assert "y=64" in ctx
    assert "z=-200" in ctx


def test_context_includes_inventory():
    req = _make_request()
    ctx = req.build_context()
    assert "cobblestone" in ctx
    assert "diamond" in ctx


def test_context_includes_armor():
    req = _make_request()
    ctx = req.build_context()
    assert "iron_chestplate" in ctx


def test_context_includes_held_item():
    req = _make_request()
    ctx = req.build_context()
    assert "diamond_pickaxe" in ctx


def test_context_labels_prayer_correctly():
    req = _make_request(request_type="prayer")
    ctx = req.build_context()
    assert "PRAYING PLAYER" in ctx


def test_context_labels_herald_correctly():
    req = _make_request(request_type="herald", message="herald guide me")
    ctx = req.build_context()
    assert "INVOKING PLAYER" in ctx


def test_context_includes_request_chat():
    req = _make_request()
    ctx = req.build_context()
    assert "God help me" in ctx


def test_context_filters_other_prayers():
    """Other players' prayer keywords are excluded from context."""
    req = _make_request()
    req.recent_chat = [
        {"player": "Steve", "message": "God help me"},
        {"player": "Alex", "message": "God please help me too"},  # another prayer
        {"player": "Bob", "message": "nice base!"},  # regular chat
    ]
    ctx = req.build_context()
    # Steve's prayer and Bob's chat should be included
    assert "God help me" in ctx
    assert "nice base" in ctx
    # Alex's separate prayer should be filtered out
    assert "help me too" not in ctx


def test_context_filters_other_herald_invocations():
    """Other players' herald keywords are also excluded from context."""
    req = _make_request(request_type="herald", message="herald guide me")
    req.recent_chat = [
        {"player": "Steve", "message": "herald guide me"},
        {"player": "Alex", "message": "hey bard tell me something"},  # another herald call
        {"player": "Bob", "message": "cool build!"},  # regular chat
    ]
    ctx = req.build_context()
    assert "herald guide me" in ctx
    assert "cool build" in ctx
    assert "tell me something" not in ctx


def test_context_filters_keyword_substrings_keeps_regular_chat():
    """Messages containing keyword substrings ('god' in 'godly') are filtered;
    messages with no keywords are kept."""
    req = _make_request()
    req.recent_chat = [
        {"player": "Steve", "message": "God help me"},
        {"player": "Alex", "message": "this is a nice godly view"},  # contains 'god' = filtered
        {"player": "Bob", "message": "I found diamonds!"},  # no keywords = kept
    ]
    ctx = req.build_context()
    assert "God help me" in ctx
    assert "I found diamonds" in ctx
    assert "godly view" not in ctx


def test_context_empty_inventory():
    req = _make_request(inventory={})
    ctx = req.build_context()
    assert "Inventory: empty" in ctx


def test_context_no_armor():
    req = _make_request(armor=["minecraft:air", "minecraft:air", "minecraft:air", "minecraft:air"])
    ctx = req.build_context()
    assert "Armor: none" in ctx


def test_context_looking_at_block():
    req = _make_request(lookingAt={"block": "diamond_ore", "blockLocation": {"x": 99, "y": 63, "z": -200}})
    ctx = req.build_context()
    assert "diamond_ore" in ctx


def test_context_nearby_entities():
    req = _make_request(nearbyEntities={"zombie": 3, "skeleton": 1})
    ctx = req.build_context()
    assert "zombie" in ctx
    assert "Nearby entities" in ctx


# ---------------------------------------------------------------------------
# Queue operations
# ---------------------------------------------------------------------------


def test_enqueue_and_size():
    q = DivineRequestQueue()
    assert q.size == 0
    q.enqueue(_make_request())
    assert q.size == 1


def test_dequeue_fifo():
    """Requests come out in the order they were enqueued."""
    async def _test():
        q = DivineRequestQueue()
        req1 = _make_request(player="Alice", message="first prayer")
        req2 = _make_request(player="Bob", message="second prayer")
        q.enqueue(req1)
        q.enqueue(req2)

        result = await q.dequeue()
        assert result.player == "Alice"
        assert q.size == 1

    asyncio.run(_test())


def test_requeue_increments_attempts():
    q = DivineRequestQueue()
    req = _make_request()
    assert req.attempts == 0
    q.requeue(req)
    assert req.attempts == 1
    assert q.size == 1


def test_requeue_returns_true_under_max():
    q = DivineRequestQueue()
    req = _make_request()
    for _ in range(MAX_ATTEMPTS - 1):
        assert q.requeue(req) is True


def test_requeue_returns_false_at_max():
    q = DivineRequestQueue()
    req = _make_request()
    req.attempts = MAX_ATTEMPTS - 1
    assert q.requeue(req) is False
    # Abandoned request is NOT put back in the queue
    assert q.size == 0


def test_requeue_abandon_after_max_attempts():
    """After MAX_ATTEMPTS, the request is abandoned — not requeued."""
    async def _test():
        q = DivineRequestQueue()
        req = _make_request()
        for i in range(MAX_ATTEMPTS):
            result = q.requeue(req)
            if i < MAX_ATTEMPTS - 1:
                assert result is True
                # Drain it so it doesn't pile up
                await q.dequeue()
            else:
                assert result is False
        assert q.size == 0

    asyncio.run(_test())
