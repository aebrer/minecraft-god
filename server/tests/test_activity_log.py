"""Tests for the consolidation activity log helpers in main.py.

Covers _log_activity timestamping, _summarize_commands extraction
for different command types (tellraw, build_schematic, plain commands),
and edge cases like empty command lists.
"""

import json
import time
from unittest.mock import patch

import server.main as main_module


# ---------------------------------------------------------------------------
# _log_activity — timestamped entry appending
# ---------------------------------------------------------------------------


def test_log_activity_appends_timestamped_entry():
    original = main_module._consolidation_log.copy()
    try:
        main_module._consolidation_log.clear()
        main_module._log_activity("CHAT: Steve: hello")
        assert len(main_module._consolidation_log) == 1
        entry = main_module._consolidation_log[0]
        # Should have [HH:MM] prefix
        assert entry.startswith("[")
        assert "] CHAT: Steve: hello" in entry
    finally:
        main_module._consolidation_log.clear()
        main_module._consolidation_log.extend(original)


def test_log_activity_multiple_entries():
    original = main_module._consolidation_log.copy()
    try:
        main_module._consolidation_log.clear()
        main_module._log_activity("CHAT: Steve: hello")
        main_module._log_activity("PRAYER: Alex: God help me")
        main_module._log_activity("Kind God acted spontaneously: said: \"I see you\"")
        assert len(main_module._consolidation_log) == 3
    finally:
        main_module._consolidation_log.clear()
        main_module._consolidation_log.extend(original)


# ---------------------------------------------------------------------------
# _summarize_commands — short summary for the activity log
# ---------------------------------------------------------------------------


def test_summarize_empty_commands():
    assert main_module._summarize_commands([]) == "silence"


def test_summarize_build_schematic():
    commands = [{"type": "build_schematic", "blueprint_id": "medieval-house", "x": 10, "y": 64, "z": 20}]
    result = main_module._summarize_commands(commands)
    assert "build_schematic(medieval-house)" in result


def test_summarize_plain_command():
    commands = [{"command": "effect give Steve minecraft:regeneration 30 1"}]
    result = main_module._summarize_commands(commands)
    assert "effect give Steve" in result


def test_summarize_tellraw_extracts_text():
    text_json = json.dumps([{"text": "I am watching over you, child.", "color": "gold"}])
    commands = [{"command": f"tellraw @a {text_json}"}]
    result = main_module._summarize_commands(commands)
    assert 'said: "I am watching over you, child."' in result


def test_summarize_multiple_commands():
    text_json = json.dumps([{"text": "Be brave.", "color": "gold"}])
    commands = [
        {"command": f"tellraw Steve {text_json}"},
        {"command": "effect give Steve minecraft:regeneration 30 1"},
    ]
    result = main_module._summarize_commands(commands)
    assert "Be brave." in result
    assert "effect give" in result
    assert "; " in result  # joined with semicolons


def test_summarize_truncates_long_commands():
    long_cmd = "x" * 200
    commands = [{"command": long_cmd}]
    result = main_module._summarize_commands(commands)
    assert len(result) <= 80


def test_summarize_tellraw_fallback_on_malformed():
    """tellraw without parseable JSON array falls back to truncated command."""
    commands = [{"command": 'tellraw @a {"text":"hello"}'}]
    result = main_module._summarize_commands(commands)
    assert "tellraw" in result


# ---------------------------------------------------------------------------
# _log_activity — cap enforcement
# ---------------------------------------------------------------------------


def test_log_activity_caps_at_max():
    """Activity log drops oldest entry when at capacity."""
    original = main_module._consolidation_log.copy()
    try:
        main_module._consolidation_log.clear()
        # Fill to capacity
        for i in range(main_module._CONSOLIDATION_LOG_MAX):
            main_module._consolidation_log.append(f"entry-{i}")
        # One more should drop the oldest
        main_module._log_activity("new entry")
        assert len(main_module._consolidation_log) == main_module._CONSOLIDATION_LOG_MAX
        assert "entry-0" not in main_module._consolidation_log
        assert "new entry" in main_module._consolidation_log[-1]
    finally:
        main_module._consolidation_log.clear()
        main_module._consolidation_log.extend(original)
