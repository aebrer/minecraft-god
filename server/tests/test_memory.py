"""Tests for the Kind God persistent memory system.

Covers KindGodMemory: loading/saving, wall-clock consolidation timing,
format_for_prompt output, the consolidation LLM call, and migration
from old ISO timestamp format to unix timestamps.
"""

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.memory import KindGodMemory


def _make_memory(tmp_path: Path, data: dict | None = None) -> KindGodMemory:
    """Create a KindGodMemory backed by a temp file.

    If data is provided, write it as the initial memory file before loading.
    """
    mem_file = tmp_path / "test_memory.json"
    if data is not None:
        mem_file.write_text(json.dumps(data))
    return KindGodMemory(mem_file)


# ---------------------------------------------------------------------------
# seconds_since_consolidation — wall-clock timer
# ---------------------------------------------------------------------------


def test_never_consolidated_returns_inf(tmp_path):
    mem = _make_memory(tmp_path)
    assert mem.seconds_since_consolidation() == float("inf")


def test_seconds_since_consolidation_after_set(tmp_path):
    mem = _make_memory(tmp_path)
    mem.last_consolidation = time.time() - 600  # 10 minutes ago
    secs = mem.seconds_since_consolidation()
    assert 599 <= secs <= 602  # allow small drift


# ---------------------------------------------------------------------------
# Loading — format migration and robustness
# ---------------------------------------------------------------------------


def test_load_unix_timestamp(tmp_path):
    """New format: last_consolidation is a unix float."""
    ts = time.time() - 3600
    mem = _make_memory(tmp_path, {
        "last_consolidation": ts,
        "consolidation_count": 5,
        "memories": [{"content": "Steve is kind", "created": "2026-01-01", "updated": "2026-01-01"}],
    })
    assert mem.last_consolidation == ts
    assert mem.consolidation_count == 5
    assert len(mem.memories) == 1


def test_load_iso_timestamp_migrates(tmp_path):
    """Old format: last_consolidation is an ISO string — should be migrated to unix."""
    mem = _make_memory(tmp_path, {
        "last_consolidation": "2026-01-15T12:00:00+00:00",
        "consolidation_count": 3,
        "memories": [],
    })
    # Should have been converted to a unix timestamp
    assert isinstance(mem.last_consolidation, float)
    assert mem.last_consolidation > 0


def test_load_null_timestamp(tmp_path):
    """Null last_consolidation (fresh file) → 0."""
    mem = _make_memory(tmp_path, {
        "last_consolidation": None,
        "consolidation_count": 0,
        "memories": [],
    })
    assert mem.last_consolidation == 0
    assert mem.seconds_since_consolidation() == float("inf")


def test_load_missing_file(tmp_path):
    """No file on disk → empty memories, zero state."""
    mem = _make_memory(tmp_path)
    assert mem.memories == []
    assert mem.consolidation_count == 0
    assert mem.last_consolidation == 0


def test_load_corrupt_json(tmp_path):
    """Corrupt JSON file → start fresh without crashing."""
    mem_file = tmp_path / "test_memory.json"
    mem_file.write_text("not valid json {{{")
    mem = KindGodMemory(mem_file)
    assert mem.memories == []


def test_load_corrupt_json_creates_backup(tmp_path):
    """Corrupt JSON file → backup file created before resetting."""
    mem_file = tmp_path / "test_memory.json"
    mem_file.write_text("not valid json {{{")
    KindGodMemory(mem_file)
    backups = list(tmp_path.glob("test_memory.corrupt.*.json"))
    assert len(backups) == 1
    assert backups[0].read_text() == "not valid json {{{"


# ---------------------------------------------------------------------------
# Saving — atomic write
# ---------------------------------------------------------------------------


def test_save_creates_file(tmp_path):
    mem = _make_memory(tmp_path)
    mem.memories = [{"content": "test", "created": "now", "updated": "now"}]
    mem.last_consolidation = 12345.0
    mem._save()

    data = json.loads(mem.memory_path.read_text())
    assert data["last_consolidation"] == 12345.0
    assert len(data["memories"]) == 1


# ---------------------------------------------------------------------------
# format_for_prompt — output for the Kind God system prompt
# ---------------------------------------------------------------------------


def test_format_empty_memories(tmp_path):
    mem = _make_memory(tmp_path)
    assert mem.format_for_prompt() == ""


def test_format_with_memories(tmp_path):
    mem = _make_memory(tmp_path, {
        "last_consolidation": 0,
        "consolidation_count": 0,
        "memories": [
            {"content": "Steve is a careful builder", "created": "now", "updated": "now"},
            {"content": "Alex prays often", "created": "now", "updated": "now"},
        ],
    })
    result = mem.format_for_prompt()
    assert "YOUR MEMORIES" in result
    assert "Steve is a careful builder" in result
    assert "Alex prays often" in result
    assert "END MEMORIES" in result


def test_format_handles_plain_string_memories(tmp_path):
    """Legacy format: memories as plain strings instead of dicts."""
    mem = _make_memory(tmp_path, {
        "last_consolidation": 0,
        "consolidation_count": 0,
        "memories": ["Steve is friendly"],
    })
    result = mem.format_for_prompt()
    assert "Steve is friendly" in result


# ---------------------------------------------------------------------------
# consolidate — LLM call, parsing, memory update
# ---------------------------------------------------------------------------


def _mock_llm_response(content: str):
    """Build a mock OpenAI chat completion response."""
    choice = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    return response


def test_consolidate_skips_empty_log(tmp_path):
    """No activity → skip consolidation entirely (no LLM call)."""
    async def _test():
        mem = _make_memory(tmp_path)
        with patch("server.memory.client") as mock_client:
            await mem.consolidate([])
            mock_client.chat.completions.create.assert_not_called()
        assert mem.consolidation_count == 0

    asyncio.run(_test())


def test_consolidate_updates_memories(tmp_path):
    """Valid LLM response → memories are updated and saved."""
    async def _test():
        mem = _make_memory(tmp_path)
        llm_response = '["Steve builds impressive castles.", "Alex is a miner."]'

        with patch("server.memory.client") as mock_client:
            mock_client.chat.completions.create = AsyncMock(
                return_value=_mock_llm_response(llm_response)
            )
            await mem.consolidate(["[12:00] CHAT: Steve: hello", "[12:01] Kind God acted"])

        assert len(mem.memories) == 2
        assert mem.memories[0]["content"] == "Steve builds impressive castles."
        assert mem.memories[1]["content"] == "Alex is a miner."
        assert mem.consolidation_count == 1
        assert mem.last_consolidation > 0

    asyncio.run(_test())


def test_consolidate_preserves_created_dates(tmp_path):
    """Unchanged memories keep their original created date."""
    async def _test():
        mem = _make_memory(tmp_path, {
            "last_consolidation": 0,
            "consolidation_count": 1,
            "memories": [
                {"content": "Steve is kind", "created": "2026-01-01T00:00:00", "updated": "2026-01-01T00:00:00"},
            ],
        })
        llm_response = '["Steve is kind", "Alex is new"]'

        with patch("server.memory.client") as mock_client:
            mock_client.chat.completions.create = AsyncMock(
                return_value=_mock_llm_response(llm_response)
            )
            await mem.consolidate(["[12:00] Something happened"])

        steve_mem = next(m for m in mem.memories if "Steve" in m["content"])
        assert steve_mem["created"] == "2026-01-01T00:00:00"  # preserved
        alex_mem = next(m for m in mem.memories if "Alex" in m["content"])
        assert alex_mem["created"] != "2026-01-01T00:00:00"  # new timestamp

    asyncio.run(_test())


def test_consolidate_strips_markdown_fences(tmp_path):
    """LLM wraps response in markdown code fences → still parsed correctly."""
    async def _test():
        mem = _make_memory(tmp_path)
        llm_response = '```json\n["Memory with fences"]\n```'

        with patch("server.memory.client") as mock_client:
            mock_client.chat.completions.create = AsyncMock(
                return_value=_mock_llm_response(llm_response)
            )
            await mem.consolidate(["[12:00] activity"])

        assert len(mem.memories) == 1
        assert mem.memories[0]["content"] == "Memory with fences"

    asyncio.run(_test())


def test_consolidate_invalid_json_raises_and_keeps_existing(tmp_path):
    """Invalid JSON from LLM → raises ValueError, existing memories unchanged."""
    async def _test():
        mem = _make_memory(tmp_path, {
            "last_consolidation": 0,
            "consolidation_count": 1,
            "memories": [{"content": "existing memory", "created": "x", "updated": "x"}],
        })

        with patch("server.memory.client") as mock_client:
            mock_client.chat.completions.create = AsyncMock(
                return_value=_mock_llm_response("not valid json at all")
            )
            with pytest.raises(ValueError, match="invalid JSON"):
                await mem.consolidate(["[12:00] activity"])

        # Existing memories should be unchanged
        assert len(mem.memories) == 1
        assert mem.memories[0]["content"] == "existing memory"
        # Count should NOT have incremented
        assert mem.consolidation_count == 1

    asyncio.run(_test())


def test_consolidate_non_list_json_raises(tmp_path):
    """LLM returns valid JSON that's not a list → raises ValueError."""
    async def _test():
        mem = _make_memory(tmp_path)

        with patch("server.memory.client") as mock_client:
            mock_client.chat.completions.create = AsyncMock(
                return_value=_mock_llm_response('{"memories": ["Steve is kind"]}')
            )
            with pytest.raises(ValueError, match="non-list"):
                await mem.consolidate(["[12:00] activity"])

        assert mem.consolidation_count == 0

    asyncio.run(_test())


def test_consolidate_llm_failure_raises(tmp_path):
    """LLM call failure → exception propagates to caller."""
    async def _test():
        mem = _make_memory(tmp_path)

        with patch("server.memory.client") as mock_client:
            mock_client.chat.completions.create = AsyncMock(
                side_effect=RuntimeError("API timeout")
            )
            with pytest.raises(RuntimeError, match="API timeout"):
                await mem.consolidate(["[12:00] activity"])

        assert mem.consolidation_count == 0

    asyncio.run(_test())


def test_consolidate_clamps_to_max_entries(tmp_path):
    """LLM returns more than MEMORY_MAX_ENTRIES → clamped."""
    async def _test():
        mem = _make_memory(tmp_path)
        many_memories = [f"Memory {i}" for i in range(50)]
        llm_response = json.dumps(many_memories)

        with patch("server.memory.client") as mock_client, \
             patch("server.memory.MEMORY_MAX_ENTRIES", 15):
            mock_client.chat.completions.create = AsyncMock(
                return_value=_mock_llm_response(llm_response)
            )
            await mem.consolidate(["[12:00] activity"])

        assert len(mem.memories) <= 15

    asyncio.run(_test())
