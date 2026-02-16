"""Death memorial system.

Persists player death records and provides context to the gods
about where and how players have died before.
"""

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from server.config import MEMORY_DIR

logger = logging.getLogger("minecraft-god")

DEATH_FILE = MEMORY_DIR / "deaths.json"
MAX_DEATHS_PER_PLAYER = 50  # keep the most recent N deaths per player


class DeathMemorial:
    """Tracks and persists player death history."""

    def __init__(self):
        self.deaths: dict[str, list[dict]] = {}
        self._load()

    def _load(self):
        if DEATH_FILE.exists():
            try:
                self.deaths = json.loads(DEATH_FILE.read_text())
                total = sum(len(v) for v in self.deaths.values())
                logger.info(f"Loaded {total} death records for {len(self.deaths)} players")
            except (json.JSONDecodeError, UnicodeDecodeError, KeyError, ValueError):
                # Data file is corrupted — back it up before falling back to empty
                backup = DEATH_FILE.with_suffix(f".corrupt.{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json")
                shutil.copy2(DEATH_FILE, backup)
                logger.exception(f"Corrupted death records — backed up to {backup.name}, starting fresh")
                self.deaths = {}

    def _save(self):
        DEATH_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEATH_FILE.write_text(json.dumps(self.deaths, indent=2))

    def record_death(self, event: dict):
        """Record a player death from an entity_die event."""
        if not event.get("isPlayer"):
            return

        player = event.get("playerName", "Unknown")
        loc = event.get("location", {})
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "x": loc.get("x"),
            "y": loc.get("y"),
            "z": loc.get("z"),
            "dimension": event.get("dimension", "?"),
            "biome": event.get("biome", "?"),
            "cause": event.get("cause", "unknown"),
            "killed_by": event.get("damagingEntity", "").replace("minecraft:", "") or None,
        }

        if player not in self.deaths:
            self.deaths[player] = []
        self.deaths[player].append(record)

        # Trim to most recent N
        if len(self.deaths[player]) > MAX_DEATHS_PER_PLAYER:
            self.deaths[player] = self.deaths[player][-MAX_DEATHS_PER_PLAYER:]

        self._save()
        logger.info(f"Death recorded: {player} died from {record['cause']} at ({record['x']}, {record['y']}, {record['z']})")

    def get_player_deaths(self, player: str) -> list[dict]:
        """Get all death records for a player."""
        return self.deaths.get(player, [])

    def get_total_deaths(self, player: str) -> int:
        """Get total death count for a player."""
        return len(self.deaths.get(player, []))

    def get_nearby_deaths(self, player: str, x: int, y: int, z: int, radius: int = 32) -> list[dict]:
        """Get deaths for a player that occurred near a given location."""
        nearby = []
        for death in self.deaths.get(player, []):
            dx = (death.get("x", 0) or 0) - x
            dy = (death.get("y", 0) or 0) - y
            dz = (death.get("z", 0) or 0) - z
            dist_sq = dx * dx + dy * dy + dz * dz
            if dist_sq <= radius * radius:
                nearby.append(death)
        return nearby

    def format_for_summary(self, player_name: str, player_x: int, player_y: int, player_z: int) -> str:
        """Format death history for inclusion in the event summary."""
        deaths = self.get_player_deaths(player_name)
        if not deaths:
            return ""

        total = len(deaths)
        lines = [f"    Death history: {total} total deaths"]

        # Most recent death
        last = deaths[-1]
        cause_str = last["cause"]
        if last.get("killed_by"):
            cause_str += f" (by {last['killed_by']})"
        lines.append(f"    Last death: {cause_str} at ({last['x']}, {last['y']}, {last['z']})")

        # Deaths near current location
        nearby = self.get_nearby_deaths(player_name, player_x, player_y, player_z, radius=32)
        if nearby:
            lines.append(f"    Deaths near current location: {len(nearby)}")
            # Show the most recent nearby death cause
            last_nearby = nearby[-1]
            cause_str = last_nearby["cause"]
            if last_nearby.get("killed_by"):
                cause_str += f" (by {last_nearby['killed_by']})"
            lines.append(f"    Most recent nearby death: {cause_str}")

        # Death cause breakdown
        causes: dict[str, int] = {}
        for d in deaths:
            c = d["cause"]
            if d.get("killed_by"):
                c += f" (by {d['killed_by']})"
            causes[c] = causes.get(c, 0) + 1
        sorted_causes = sorted(causes.items(), key=lambda x: -x[1])[:5]
        cause_summary = ", ".join(f"{count}x {cause}" for cause, count in sorted_causes)
        lines.append(f"    Top causes: {cause_summary}")

        return "\n".join(lines)
