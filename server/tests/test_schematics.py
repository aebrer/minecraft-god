"""Tests for the schematic search and build system.

All tests go through the public interface: search_schematics() and
build_schematic_command(). Search ranking is verified by checking result
ordering and inclusion, not internal scoring.

Uses a minimal test catalog instead of the real 2,139-blueprint catalog.
"""

from unittest.mock import patch

from server.schematics import search_schematics, build_schematic_command


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


def _search(query: str) -> str:
    """Run search_schematics with our test catalog."""
    with patch("server.schematics._load_catalog", return_value=_TEST_CATALOG):
        return search_schematics(query)


def _build(blueprint_id: str, x: int, y: int, z: int, rotation: int = 0):
    """Run build_schematic_command with our test catalog."""
    with patch("server.schematics._load_catalog", return_value=_TEST_CATALOG):
        return build_schematic_command(blueprint_id, x, y, z, rotation)


def _first_result_id(result: str) -> str:
    """Extract the blueprint ID from the first search result line."""
    for line in result.split("\n"):
        line = line.strip()
        if ":" in line and not line.startswith("Search") and not line.startswith("Valid") and not line.startswith("Pick"):
            return line.split(":")[0].strip()
    return ""


# ---------------------------------------------------------------------------
# Search ranking (verified through result ordering)
# ---------------------------------------------------------------------------


def test_exact_name_match_ranks_first():
    result = _search("Basic Iron Farm")
    assert _first_result_id(result) == "iron-farm-basic"


def test_term_matching_finds_both_iron_farms():
    result = _search("iron farm")
    assert "iron-farm-basic" in result
    assert "iron-golem-mega" in result


def test_exact_name_match_beats_downloads():
    """Exact query in name beats higher download count."""
    result = _search("iron farm")
    # "Basic Iron Farm" contains "iron farm" exactly, mega doesn't
    basic_pos = result.index("iron-farm-basic")
    mega_pos = result.index("iron-golem-mega")
    assert basic_pos < mega_pos


def test_tag_matching():
    result = _search("villager")
    assert "iron-farm-basic" in result
    assert "iron-golem-mega" in result
    assert "medieval-house" not in result


def test_category_matching():
    result = _search("houses")
    assert "medieval-house" in result


def test_unrelated_query_no_results():
    result = _search("spaceship")
    assert "No schematics found" in result


# ---------------------------------------------------------------------------
# Search input handling
# ---------------------------------------------------------------------------


def test_empty_query():
    result = _search("")
    assert "Please provide a search query" in result


def test_whitespace_query():
    result = _search("   ")
    assert "Please provide a search query" in result


# ---------------------------------------------------------------------------
# Search result formatting
# ---------------------------------------------------------------------------


def test_result_shows_dimensions():
    result = _search("medieval house")
    assert "12x8x10" in result


def test_result_shows_download_count():
    result = _search("iron farm")
    assert "5000 downloads" in result or "15000 downloads" in result


def test_result_includes_valid_ids_section():
    result = _search("farm")
    assert "Valid blueprint IDs:" in result


def test_result_includes_build_instruction():
    result = _search("farm")
    assert "build_schematic" in result


# ---------------------------------------------------------------------------
# build_schematic_command
# ---------------------------------------------------------------------------


def test_build_valid_blueprint():
    result = _build("iron-farm-basic", 100, 64, 200)
    assert result is not None
    assert result["type"] == "build_schematic"
    assert result["blueprint_id"] == "iron-farm-basic"
    assert result["x"] == 100
    assert result["y"] == 64
    assert result["z"] == 200


def test_build_unknown_blueprint():
    assert _build("nonexistent-thing", 0, 64, 0) is None


def test_build_invalid_coordinate():
    assert _build("iron-farm-basic", 50000, 64, 0) is None


def test_build_invalid_rotation_defaults_to_zero():
    result = _build("iron-farm-basic", 0, 64, 0, rotation=45)
    assert result is not None
    assert result["rotation"] == 0


def test_build_valid_rotations():
    for rot in (0, 90, 180, 270):
        result = _build("iron-farm-basic", 0, 64, 0, rotation=rot)
        assert result["rotation"] == rot
