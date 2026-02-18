"""Dig God memory — FILO deque of hole records with JSON persistence.

Each record contains a free-text memory composed by the LLM plus auto-attached
metadata (shape, coordinates, player, dimensions, timestamp). Oldest entries
are evicted when the deque is full.
"""

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("minecraft-god")


class DigMemory:
    """FILO deque of dig records, persisted to JSON."""

    def __init__(self, path: Path, max_entries: int = 15):
        self.path = path
        self.max_entries = max_entries
        self.records: list[dict] = []
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
            if isinstance(data, list):
                self.records = data[-self.max_entries:]
                logger.info(f"Loaded {len(self.records)} dig memory records")
            else:
                logger.warning("Dig memory file contains non-list data — starting empty")
        except (json.JSONDecodeError, OSError):
            logger.exception("Dig memory file corrupt — starting empty")

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.records, indent=2))
            os.replace(tmp, self.path)
        except OSError:
            logger.exception("Failed to save dig memory")

    def add(self, memory_text: str, metadata: dict):
        """Add a new memory record. Evicts oldest if at capacity."""
        record = {
            "memory": memory_text,
            "timestamp": time.strftime("%Y-%m-%d %H:%M"),
            **metadata,
        }
        self.records.append(record)
        if len(self.records) > self.max_entries:
            self.records = self.records[-self.max_entries:]
        self._save()
        logger.info(f"Dig memory: added record ({len(self.records)}/{self.max_entries})")

    def format_for_prompt(self) -> str:
        """Format memory records for injection into the system prompt."""
        if not self.records:
            return "\n\nHOLE ARCHIVE: Empty. No holes have been dug yet. Make history!"
        lines = ["\n\nHOLE ARCHIVE (your personal record of past excavations, newest first):"]
        for r in reversed(self.records):
            meta_parts = []
            if r.get("player"):
                meta_parts.append(f"for {r['player']}")
            if r.get("shape"):
                meta_parts.append(r["shape"])
            if r.get("dimensions"):
                meta_parts.append(r["dimensions"])
            if r.get("location"):
                meta_parts.append(f"at {r['location']}")
            if r.get("timestamp"):
                meta_parts.append(r["timestamp"])
            meta = " | ".join(meta_parts)
            lines.append(f"  [{meta}] {r.get('memory', '')}")
        return "\n".join(lines)
