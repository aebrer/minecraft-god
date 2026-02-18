"""Tests for the god thinking broadcast system — filtering and tellraw generation.

Tests cover the _filter_thinking_lines function which determines what thinking
text gets shown to players in-game, and the _make_thinking_callback factory
which produces tellraw commands for the command queue.
"""

import json
import pytest

from server.main import _filter_thinking_lines, _make_thinking_callback, command_queue


# ─── Filtering: narrative text passes through ─────────────────────────────────

class TestFilterNarrativeText:
    """Normal god thinking text should pass through with markdown stripped."""

    def test_simple_text(self):
        lines = _filter_thinking_lines("The player seeks wisdom.")
        assert lines == ["The player seeks wisdom."]

    def test_multiline_text(self):
        text = "The player prays.\nI shall grant them haste.\nSo it is done."
        lines = _filter_thinking_lines(text)
        assert lines == [
            "The player prays.",
            "I shall grant them haste.",
            "So it is done.",
        ]

    def test_strips_markdown_bold(self):
        lines = _filter_thinking_lines("**The Kind God** considers this.")
        assert lines == ["The Kind God considers this."]

    def test_strips_markdown_italic(self):
        lines = _filter_thinking_lines("*chuckles softly*")
        assert lines == ["chuckles softly"]

    def test_strips_backticks(self):
        lines = _filter_thinking_lines("The `haste` effect is granted.")
        assert lines == ["The haste effect is granted."]

    def test_empty_lines_skipped(self):
        text = "First line.\n\n\nSecond line."
        lines = _filter_thinking_lines(text)
        assert lines == ["First line.", "Second line."]

    def test_whitespace_only_lines_skipped(self):
        text = "Hello.\n   \nWorld."
        lines = _filter_thinking_lines(text)
        assert lines == ["Hello.", "World."]


# ─── Filtering: JSON is filtered out ─────────────────────────────────────────

class TestFilterJSON:
    """JSON responses (from dig god structured output) should be filtered."""

    def test_json_object_filtered(self):
        text = '{"action": "dig_hole", "params": {"width": 5, "depth": 10}}'
        lines = _filter_thinking_lines(text)
        assert lines == []

    def test_json_array_filtered(self):
        text = '[{"type": "send_message"}]'
        lines = _filter_thinking_lines(text)
        assert lines == []

    def test_fenced_json_filtered(self):
        text = '```json\n{"action": "dig_hole"}\n```'
        lines = _filter_thinking_lines(text)
        assert lines == []

    def test_fenced_code_block_filtered(self):
        text = '```\nsome code\n```'
        lines = _filter_thinking_lines(text)
        assert lines == []

    def test_invalid_json_starting_with_brace_kept(self):
        """Text that starts with { but isn't valid JSON should be kept."""
        text = "{The player asks for help}"
        lines = _filter_thinking_lines(text)
        assert lines == ["{The player asks for help}"]

    def test_json_line_in_mixed_text_filtered(self):
        text = 'I shall help.\n{"action": "dig_hole"}\nIt is done.'
        lines = _filter_thinking_lines(text)
        assert lines == ["I shall help.", "It is done."]


# ─── Filtering: minecraft commands filtered ───────────────────────────────────

class TestFilterCommands:
    """Lines that look like minecraft commands should be filtered."""

    def test_slash_command_filtered(self):
        lines = _filter_thinking_lines("/effect give player haste")
        assert lines == []

    def test_tellraw_filtered(self):
        lines = _filter_thinking_lines('tellraw @a [{"text":"hello"}]')
        assert lines == []

    def test_effect_command_filtered(self):
        lines = _filter_thinking_lines("effect give player minecraft:haste 60")
        assert lines == []

    def test_command_in_mixed_text_filtered(self):
        text = "I shall help the player.\n/give player diamond 1\nSo it is done."
        lines = _filter_thinking_lines(text)
        assert lines == ["I shall help the player.", "So it is done."]


# ─── Filtering: empty input ──────────────────────────────────────────────────

class TestFilterEmpty:
    def test_empty_string(self):
        assert _filter_thinking_lines("") == []

    def test_whitespace_only(self):
        assert _filter_thinking_lines("   \n  \n  ") == []


# ─── Callback: tellraw generation ─────────────────────────────────────────────

class TestThinkingCallback:
    """The callback should produce correctly formatted tellraw commands."""

    def setup_method(self):
        command_queue.clear()

    def test_callback_produces_header_and_lines(self):
        callback = _make_thinking_callback("kind")
        callback("The player needs help.\nI shall grant haste.")
        # Should produce: 1 header + 2 content lines = 3 commands
        assert len(command_queue) == 3
        # Header
        assert "The Kind God thinks:" in command_queue[0]["command"]
        assert "gold" in command_queue[0]["command"]
        # Content lines
        assert "The player needs help." in command_queue[1]["command"]
        assert "I shall grant haste." in command_queue[2]["command"]

    def test_callback_uses_correct_god_color(self):
        callback = _make_thinking_callback("deep")
        callback("The depths stir.")
        assert len(command_queue) == 2
        assert "dark_red" in command_queue[0]["command"]
        assert "??? thinks:" in command_queue[0]["command"]

    def test_callback_herald_color(self):
        callback = _make_thinking_callback("herald")
        callback("A verse forms.")
        assert "green" in command_queue[0]["command"]
        assert "The Herald thinks:" in command_queue[0]["command"]

    def test_callback_dig_color(self):
        callback = _make_thinking_callback("dig")
        callback("A hole calls.")
        assert "dark_aqua" in command_queue[0]["command"]
        assert "The God of Digging thinks:" in command_queue[0]["command"]

    def test_callback_skips_json_entirely(self):
        callback = _make_thinking_callback("kind")
        callback('{"action": "dig_hole", "params": {}}')
        assert len(command_queue) == 0

    def test_callback_targets_all_players(self):
        callback = _make_thinking_callback("kind")
        callback("Hello world.")
        for cmd in command_queue:
            assert "@a" in cmd["command"]

    def test_callback_strips_markdown(self):
        callback = _make_thinking_callback("kind")
        callback("*chuckles* **boldly**")
        # The content line should have markdown stripped
        content_cmd = command_queue[1]["command"]
        assert "chuckles boldly" in content_cmd
        assert "**" not in content_cmd
        assert "*" not in json.loads(content_cmd.split("tellraw @a ", 1)[1])[0]["text"]

    def test_multi_turn_accumulates(self):
        """Kind God calls the callback multiple times (once per turn)."""
        callback = _make_thinking_callback("kind")
        callback("Turn 1: searching for a castle.")
        callback("Turn 2: found one, placing it.")
        # Should be 2 headers + 2 content lines = 4 commands total
        assert len(command_queue) == 4
        assert "Turn 1" in command_queue[1]["command"]
        assert "Turn 2" in command_queue[3]["command"]
