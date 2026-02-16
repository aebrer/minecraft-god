"""Tests for the schematic search and build system.

Verifies search ranking (exact match, term matching, tag matching,
download popularity bonus), coordinate validation, and result formatting.

Uses a minimal test catalog instead of the real 2,139-blueprint catalog.
"""

from unittest.mock import patch

from server.schematics import _score_match, search_schematics, build_schematic_command


# ---------------------------------------------------------------------------
# Test catalog fixture
# ---------------------------------------------------------------------------


_TEST_CATALOG = {
    "categories": {
        "farms": {
            "count": 3,
            "blueprints": [
                {
                    "id": "iron-farm-basic",
                    "name": "Basic Iron Farm",
                    "tags": ["iron", "farm", "villager"],
                    "description": "A simple iron golem farm using villagers",
                    "downloads": 5000,
                    "dimensions": {"w": 10, "h": 15, "d": 10},
                },
                {
                    "id": "sugar-cane-farm",
                    "name": "Sugar Cane Farm",
                    "tags": ["sugar", "cane", "automatic"],
                    "description": "Automatic sugar cane harvester",
                    "downloads": 200,
                    "dimensions": {"w": 8, "h": 5, "d": 8},
                },
                {
                    "id": "iron-golem-mega",
                    "name": "Mega Iron Golem Farm",
                    "tags": ["iron", "farm", "mega", "villager"],
                    "description": "High-output iron farm",
                    "downloads": 15000,
                    "dimensions": {"w": 20, "h": 30, "d": 20},
                },
            ],
        },
        "houses": {
            "count": 1,
            "blueprints": [
                {
                    "id": "medieval-house",
                    "name": "Medieval House",
                    "tags": ["medieval", "house", "stone"],
                    "description": "A cozy medieval dwelling",
                    "downloads": 800,
                    "dimensions": {"w": 12, "h": 8, "d": 10},
                },
            ],
        },
    }
}


def _patched_search(query: str) -> str:
    """Run search_schematics with our test catalog."""
    with patch("server.schematics._load_catalog", return_value=_TEST_CATALOG):
        return search_schematics(query)


def _patched_build(blueprint_id: str, x: int, y: int, z: int, rotation: int = 0):
    """Run build_schematic_command with our test catalog."""
    with patch("server.schematics._load_catalog", return_value=_TEST_CATALOG):
        return build_schematic_command(blueprint_id, x, y, z, rotation)


# ---------------------------------------------------------------------------
# Search ranking
# ---------------------------------------------------------------------------


def test_exact_name_match_ranks_highest():
    result = _patched_search("Basic Iron Farm")
    # The exact match should be the first result
    lines = result.split("\n")
    first_result = [l for l in lines if l.strip().startswith("iron-farm-basic")
                    or l.strip().startswith("iron-golem-mega")]
    assert "iron-farm-basic" in result.split("\n")[2]  # first result line


def test_term_matching_across_name():
    result = _patched_search("iron farm")
    # Both iron farms should appear, but we just verify both are in results
    assert "iron-farm-basic" in result
    assert "iron-golem-mega" in result


def test_tag_matching():
    result = _patched_search("villager")
    # Both iron farms have the "villager" tag
    assert "iron-farm-basic" in result
    assert "iron-golem-mega" in result
    # Medieval house does not
    assert "medieval-house" not in result


def test_category_matching():
    result = _patched_search("houses")
    assert "medieval-house" in result


def test_no_results():
    result = _patched_search("spaceship")
    assert "No schematics found" in result


def test_empty_query():
    result = _patched_search("")
    assert "Please provide a search query" in result


def test_whitespace_query():
    result = _patched_search("   ")
    assert "Please provide a search query" in result


def test_result_shows_dimensions():
    result = _patched_search("medieval house")
    assert "12x8x10" in result


def test_result_shows_download_count():
    result = _patched_search("iron farm")
    assert "5000 downloads" in result or "15000 downloads" in result


def test_result_includes_valid_ids():
    result = _patched_search("farm")
    assert "Valid blueprint IDs:" in result


# ---------------------------------------------------------------------------
# Score match internals
# ---------------------------------------------------------------------------


def test_score_exact_full_query_in_name():
    bp = {"name": "Iron Farm", "tags": [], "description": "", "id": "x", "downloads": 0}
    score = _score_match(bp, "farms", ["iron", "farm"], "iron farm")
    # Should get the 100-point exact match bonus plus term bonuses
    assert score >= 100


def test_score_zero_for_no_match():
    bp = {"name": "Castle", "tags": ["castle"], "description": "A big castle", "id": "castle", "downloads": 0}
    score = _score_match(bp, "castles", ["spaceship"], "spaceship")
    assert score == 0


def test_download_popularity_bonus():
    bp_popular = {"name": "Farm", "tags": [], "description": "", "id": "x", "downloads": 10000}
    bp_niche = {"name": "Farm", "tags": [], "description": "", "id": "y", "downloads": 1}
    score_pop = _score_match(bp_popular, "farms", ["farm"], "farm")
    score_niche = _score_match(bp_niche, "farms", ["farm"], "farm")
    assert score_pop > score_niche


def test_download_bonus_capped():
    """Download bonus shouldn't dominate — capped at 20 points."""
    bp = {"name": "Farm", "tags": [], "description": "", "id": "x", "downloads": 999999}
    score = _score_match(bp, "farms", ["farm"], "farm")
    # Without downloads the base score is ~40 (name match 30 + word boundary 10)
    # With capped downloads it should be at most base + 20
    bp_zero = {"name": "Farm", "tags": [], "description": "", "id": "x", "downloads": 0}
    score_zero = _score_match(bp_zero, "farms", ["farm"], "farm")
    assert score - score_zero <= 20


# ---------------------------------------------------------------------------
# build_schematic_command
# ---------------------------------------------------------------------------


def test_build_valid_blueprint():
    result = _patched_build("iron-farm-basic", 100, 64, 200)
    assert result is not None
    assert result["type"] == "build_schematic"
    assert result["blueprint_id"] == "iron-farm-basic"
    assert result["x"] == 100
    assert result["y"] == 64
    assert result["z"] == 200


def test_build_unknown_blueprint():
    result = _patched_build("nonexistent-thing", 0, 64, 0)
    assert result is None


def test_build_invalid_coordinate():
    result = _patched_build("iron-farm-basic", 50000, 64, 0)
    assert result is None


def test_build_invalid_rotation_defaults_to_zero():
    result = _patched_build("iron-farm-basic", 0, 64, 0, rotation=45)
    assert result is not None
    assert result["rotation"] == 0


def test_build_valid_rotations():
    for rot in (0, 90, 180, 270):
        result = _patched_build("iron-farm-basic", 0, 64, 0, rotation=rot)
        assert result["rotation"] == rot
