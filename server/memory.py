"""Kind God persistent memory.

Handles loading/saving memory to disk and the periodic consolidation
LLM call where the Kind God reviews recent events and updates its
memories of players. The Deep God does not get memory — it does not
care about individuals.
"""

import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from server.config import GOD_MODEL, MEMORY_MAX_ENTRIES
from server.llm import client

logger = logging.getLogger("minecraft-god")

CONSOLIDATION_SYSTEM_PROMPT = """\
You are the Kind God, reflecting on recent events in your world. You are reviewing \
what has happened and deciding what to remember.

You have a limited memory — you can hold at most {max_memories} distinct memories. \
Each memory should be 1-3 sentences. Focus on:
- Individual players: their names, personalities, behaviors, preferences
- Relationships between players (who works together, who is a loner)
- Notable events: deaths, gifts you gave, missions assigned, prayers answered
- Long-term patterns: who builds, who mines, who prays, who is reckless
- Promises you have made or things you told specific players
- Your own feelings about players (you care about them)
- What the Deep God and the Herald have done — you are aware of their actions

When updating memories:
- You may update existing memories with new information
- You may remove memories that are no longer relevant
- You may add new memories if something notable happened
- If nothing notable happened, return your existing memories unchanged
- Be concise. These memories are your thoughts each time you observe the world.

Respond with ONLY a JSON array of memory strings. No other text, no markdown, \
no code fences. Example:
["Steve is a careful builder who stays on the surface.", "Alex prays often."]"""


class KindGodMemory:
    def __init__(self, memory_path: Path):
        self.memory_path = memory_path
        self.memories: list[dict] = []
        self.last_consolidation: float = 0  # unix timestamp
        self.consolidation_count: int = 0
        self._load()

    def _load(self) -> None:
        """Load memories from disk, or start empty."""
        if not self.memory_path.exists():
            logger.info("No existing memories found — starting fresh")
            return

        try:
            data = json.loads(self.memory_path.read_text())
            self.memories = data.get("memories", [])
            self.consolidation_count = data.get("consolidation_count", 0)

            # Handle both old ISO string format and new unix timestamp
            raw_ts = data.get("last_consolidation")
            if isinstance(raw_ts, (int, float)):
                self.last_consolidation = float(raw_ts)
            elif isinstance(raw_ts, str):
                # Migrate from old ISO format
                try:
                    dt = datetime.fromisoformat(raw_ts)
                    self.last_consolidation = dt.timestamp()
                except ValueError:
                    self.last_consolidation = 0
            else:
                self.last_consolidation = 0

            logger.info(
                f"Loaded {len(self.memories)} memories "
                f"(consolidations: {self.consolidation_count})"
            )
        except (json.JSONDecodeError, KeyError, UnicodeDecodeError, ValueError):
            backup = self.memory_path.with_suffix(
                f".corrupt.{int(time.time())}.json"
            )
            try:
                shutil.copy2(self.memory_path, backup)
                logger.error(f"Memory file corrupt — backed up to {backup.name}, starting fresh")
            except OSError:
                logger.error("Memory file corrupt — backup failed, starting fresh")
            self.memories = []

    def _save(self) -> None:
        """Write current memories to disk atomically."""
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "last_consolidation": self.last_consolidation,
            "consolidation_count": self.consolidation_count,
            "memories": self.memories,
        }

        tmp_path = self.memory_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, indent=2))
        os.replace(tmp_path, self.memory_path)

    def seconds_since_consolidation(self) -> float:
        """Wall-clock seconds since last consolidation, or inf if never consolidated."""
        if self.last_consolidation == 0:
            return float("inf")
        return time.time() - self.last_consolidation

    def format_for_prompt(self) -> str:
        """Return memories formatted for injection into the system prompt."""
        if not self.memories:
            return ""

        lines = []
        for m in self.memories:
            content = m.get("content", "") if isinstance(m, dict) else str(m)
            lines.append(f"- {content}")

        return (
            "\n\n=== YOUR MEMORIES ===\n"
            "These are things you have chosen to remember about your world and its "
            "people. They persist across time. You wrote these yourself during past "
            "reflections.\n\n"
            + "\n".join(lines)
            + "\n=== END MEMORIES ==="
        )

    async def consolidate(self, activity_log: list[str]) -> None:
        """Run the consolidation LLM call and update memories.

        activity_log: human-readable timeline entries from the global activity log.
        """
        if not activity_log:
            logger.info("Consolidation skipped — no recent activity to review")
            return

        # Format current memories for the prompt
        if self.memories:
            current = "\n".join(
                f"- {m.get('content', '')}" if isinstance(m, dict) else f"- {m}"
                for m in self.memories
            )
        else:
            current = "You have no memories yet."

        recent = "\n".join(activity_log)

        user_message = (
            f"Here are your current memories:\n{current}\n\n"
            f"Here is what has happened recently:\n{recent}\n\n"
            "Review these events and update your memories. "
            "Respond with a JSON array of memory strings."
        )

        system_prompt = CONSOLIDATION_SYSTEM_PROMPT.format(
            max_memories=MEMORY_MAX_ENTRIES
        )

        try:
            response = await client.chat.completions.create(
                model=GOD_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.7,
            )
        except Exception:
            logger.exception("Memory consolidation LLM call failed")
            raise  # let caller handle retry/logging

        raw = response.choices[0].message.content or ""

        # Parse JSON array from response
        try:
            # Strip markdown fences if the model wraps them
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1]
                cleaned = cleaned.rsplit("```", 1)[0]
            memory_strings = json.loads(cleaned)
        except (json.JSONDecodeError, IndexError):
            logger.warning(
                f"Memory consolidation returned invalid JSON, keeping existing memories. "
                f"Raw response: {raw[:200]}"
            )
            raise ValueError(f"LLM returned invalid JSON for consolidation: {raw[:200]}")

        if not isinstance(memory_strings, list):
            logger.warning("Memory consolidation did not return a list")
            raise ValueError(f"LLM returned non-list type for consolidation: {type(memory_strings)}")

        # Clamp to max entries
        memory_strings = memory_strings[:MEMORY_MAX_ENTRIES]

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        # Build new memory entries, preserving created dates where content matches
        old_contents = {
            m.get("content", ""): m.get("created", now)
            for m in self.memories
            if isinstance(m, dict)
        }

        new_memories = []
        for s in memory_strings:
            if not isinstance(s, str) or not s.strip():
                continue
            content = s.strip()[:500]  # Truncate overly long entries
            new_memories.append({
                "created": old_contents.get(content, now),
                "updated": now,
                "content": content,
            })

        self.memories = new_memories
        self.last_consolidation = time.time()
        self.consolidation_count += 1
        self._save()

        logger.info(
            f"Memory consolidation #{self.consolidation_count}: "
            f"{len(self.memories)} memories saved"
        )
