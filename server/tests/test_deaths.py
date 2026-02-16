"""Tests for the DeathMemorial system.

Verifies death recording, proximity queries, summary formatting,
and the trimming logic that caps death records per player.
"""

from unittest.mock import patch, MagicMock

from server.deaths import DeathMemorial, MAX_DEATHS_PER_PLAYER


def _make_memorial() -> DeathMemorial:
    """Create a DeathMemorial that doesn't touch disk."""
    with patch.object(DeathMemorial, "_load"):
        dm = DeathMemorial()
    dm._save = MagicMock()
    return dm


def _death_event(player: str = "Steve", x: int = 0, y: int = 64, z: int = 0,
                 cause: str = "zombie", killer: str = "minecraft:zombie") -> dict:
    return {
        "isPlayer": True,
        "playerName": player,
        "location": {"x": x, "y": y, "z": z},
        "dimension": "overworld",
        "biome": "plains",
        "cause": cause,
        "damagingEntity": killer,
    }


# ---------------------------------------------------------------------------
# Recording deaths
# ---------------------------------------------------------------------------


def test_record_death():
    dm = _make_memorial()
    dm.record_death(_death_event("Steve"))
    assert dm.get_total_deaths("Steve") == 1


def test_record_non_player_ignored():
    dm = _make_memorial()
    dm.record_death({"isPlayer": False, "entity": "minecraft:cow"})
    assert dm.deaths == {}


def test_record_multiple_deaths():
    dm = _make_memorial()
    for i in range(5):
        dm.record_death(_death_event("Steve", x=i * 10))
    assert dm.get_total_deaths("Steve") == 5


def test_trimming_at_max():
    dm = _make_memorial()
    for i in range(MAX_DEATHS_PER_PLAYER + 10):
        dm.record_death(_death_event("Steve", x=i))
    assert dm.get_total_deaths("Steve") == MAX_DEATHS_PER_PLAYER
    # Most recent should be kept (highest x value)
    last = dm.get_player_deaths("Steve")[-1]
    assert last["x"] == MAX_DEATHS_PER_PLAYER + 9


def test_death_saves_to_disk():
    dm = _make_memorial()
    dm.record_death(_death_event("Steve"))
    dm._save.assert_called_once()


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def test_get_deaths_unknown_player():
    dm = _make_memorial()
    assert dm.get_player_deaths("Nobody") == []
    assert dm.get_total_deaths("Nobody") == 0


def test_nearby_deaths():
    dm = _make_memorial()
    # Death at (100, 64, 100)
    dm.record_death(_death_event("Steve", x=100, y=64, z=100))
    # Death far away at (1000, 64, 1000)
    dm.record_death(_death_event("Steve", x=1000, y=64, z=1000))

    nearby = dm.get_nearby_deaths("Steve", 105, 64, 105, radius=32)
    assert len(nearby) == 1
    assert nearby[0]["x"] == 100


def test_nearby_deaths_radius_boundary():
    dm = _make_memorial()
    # Death exactly at radius distance (32 blocks on X axis)
    dm.record_death(_death_event("Steve", x=32, y=0, z=0))
    # Should be included (dist_sq == radius * radius, check uses <=)
    nearby = dm.get_nearby_deaths("Steve", 0, 0, 0, radius=32)
    assert len(nearby) == 1


def test_nearby_deaths_just_outside_radius():
    dm = _make_memorial()
    dm.record_death(_death_event("Steve", x=33, y=0, z=0))
    nearby = dm.get_nearby_deaths("Steve", 0, 0, 0, radius=32)
    assert len(nearby) == 0


# ---------------------------------------------------------------------------
# Summary formatting
# ---------------------------------------------------------------------------


def test_format_no_deaths():
    dm = _make_memorial()
    assert dm.format_for_summary("Steve", 0, 0, 0) == ""


def test_format_single_death():
    dm = _make_memorial()
    dm.record_death(_death_event("Steve", x=10, y=64, z=20, cause="fall", killer=""))
    summary = dm.format_for_summary("Steve", 10, 64, 20)
    assert "1 total deaths" in summary
    assert "fall" in summary


def test_format_includes_nearby_count():
    dm = _make_memorial()
    # Three deaths near (0, 64, 0)
    for i in range(3):
        dm.record_death(_death_event("Steve", x=i, y=64, z=0))
    summary = dm.format_for_summary("Steve", 0, 64, 0)
    assert "Deaths near current location: 3" in summary


def test_format_killer_attribution():
    dm = _make_memorial()
    dm.record_death(_death_event("Steve", cause="entity_attack", killer="minecraft:creeper"))
    summary = dm.format_for_summary("Steve", 0, 64, 0)
    assert "creeper" in summary


def test_format_top_causes():
    dm = _make_memorial()
    for _ in range(3):
        dm.record_death(_death_event("Steve", cause="fall", killer=""))
    for _ in range(2):
        dm.record_death(_death_event("Steve", cause="lava", killer=""))
    summary = dm.format_for_summary("Steve", 0, 64, 0)
    assert "Top causes:" in summary
    assert "3x fall" in summary
    assert "2x lava" in summary
