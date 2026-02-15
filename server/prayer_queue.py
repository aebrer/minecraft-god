"""Prayer queue: FIFO queue of prayers with snapshot context.

Each prayer captures the game state at the moment it was spoken,
so the god can respond with accurate context even if the player
has moved on by the time the prayer is processed.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

from server.config import PRAYER_KEYWORDS

logger = logging.getLogger("minecraft-god")

MAX_PRAYER_ATTEMPTS = 5


def _is_prayer(message: str) -> bool:
    """Check if a chat message contains prayer keywords."""
    lower = message.lower()
    return any(kw in lower for kw in PRAYER_KEYWORDS)


@dataclass
class Prayer:
    """A prayer waiting to be answered, with snapshot context."""
    player: str
    message: str
    timestamp: float
    player_snapshot: dict          # full player status dict at prayer time
    recent_chat: list[dict]       # nearby chat messages for context
    attempts: int = 0

    def build_context(self) -> str:
        """Build the LLM context string from the snapshot."""
        sections = []

        # Player status from snapshot
        p = self.player_snapshot
        if p:
            loc = p.get("location", {})
            facing = p.get("facing", "?")
            look_v = p.get("lookingVertical", "ahead")
            biome = p.get("biome", "?")
            info = (
                f"  - {p['name']}: at ({loc.get('x', '?')}, {loc.get('y', '?')}, {loc.get('z', '?')}) "
                f"in {p.get('dimension', '?')} ({biome}), facing {facing} looking {look_v}, "
                f"health={p.get('health', '?')}/{p.get('maxHealth', '?')}, "
                f"food={p.get('foodLevel', '?')}/20, level={p.get('level', '?')}"
            )
            armor = [a.replace("minecraft:", "") for a in p.get("armor", []) if a != "minecraft:air"]
            info += f"\n    Armor: {', '.join(armor)}" if armor else "\n    Armor: none"
            if p.get("mainHand"):
                info += f" | Holding: {p['mainHand'].replace('minecraft:', '')}"
            inventory = p.get("inventory", {})
            if inventory:
                sorted_inv = sorted(inventory.items(), key=lambda x: -x[1])
                items_str = ", ".join(f"{count} {item}" for item, count in sorted_inv)
                info += f"\n    Inventory: {items_str}"
            else:
                info += "\n    Inventory: empty"
            looking_at = p.get("lookingAt", {})
            if looking_at:
                parts = []
                if looking_at.get("block"):
                    bloc = looking_at.get("blockLocation", {})
                    parts.append(f"{looking_at['block']} at ({bloc.get('x', '?')}, {bloc.get('y', '?')}, {bloc.get('z', '?')})")
                if looking_at.get("entity"):
                    parts.append(looking_at["entity"])
                if parts:
                    info += f"\n    Looking at: {', '.join(parts)}"
            close = p.get("closeEntities", {})
            if close:
                sorted_close = sorted(close.items(), key=lambda x: -x[1])
                close_str = ", ".join(f"{count} {etype}" for etype, count in sorted_close)
                info += f"\n    Immediate vicinity (8 blocks): {close_str}"
            notable = p.get("notableBlocks", {})
            if notable:
                sorted_notable = sorted(notable.items(), key=lambda x: -x[1])
                notable_str = ", ".join(f"{count} {block}" for block, count in sorted_notable)
                info += f"\n    Notable blocks nearby (8 blocks): {notable_str}"
            nearby = p.get("nearbyEntities", {})
            if nearby:
                sorted_nearby = sorted(nearby.items(), key=lambda x: -x[1])
                nearby_str = ", ".join(f"{count} {etype}" for etype, count in sorted_nearby)
                info += f"\n    Nearby entities (32 blocks): {nearby_str}"
            sections.append("PRAYING PLAYER:\n" + info)

        # The prayer itself plus surrounding non-prayer chat
        # Filter out other players' prayers so the god only sees this one
        chat_lines = []
        for c in self.recent_chat:
            msg = c.get("message", "")
            sender = c.get("player", "?")
            is_this_prayer = (sender == self.player and msg == self.message)
            is_other_prayer = (not is_this_prayer and _is_prayer(msg))
            if not is_other_prayer:
                chat_lines.append(f'  [PLAYER CHAT] {sender}: "{msg}"')
        # Make sure the prayer itself is included even if it wasn't in recent_chat
        prayer_line = f'  [PLAYER CHAT] {self.player}: "{self.message}"'
        if prayer_line not in chat_lines:
            chat_lines.append(prayer_line)
        if chat_lines:
            sections.append("CHAT:\n" + "\n".join(chat_lines))

        return "\n\n".join(sections) if sections else f'{self.player} prayed: "{self.message}"'


class PrayerQueue:
    """FIFO queue of prayers awaiting divine response."""

    def __init__(self):
        self._queue: asyncio.Queue[Prayer] = asyncio.Queue()

    def enqueue(self, prayer: Prayer):
        self._queue.put_nowait(prayer)
        logger.info(
            f"Prayer queued from {prayer.player}: \"{prayer.message[:60]}\" "
            f"(queue depth: {self._queue.qsize()})"
        )

    async def dequeue(self) -> Prayer:
        """Block until a prayer is available."""
        return await self._queue.get()

    def requeue(self, prayer: Prayer):
        """Put a failed prayer back for retry."""
        prayer.attempts += 1
        if prayer.attempts < MAX_PRAYER_ATTEMPTS:
            self._queue.put_nowait(prayer)
            logger.info(
                f"Prayer from {prayer.player} requeued (attempt {prayer.attempts}/{MAX_PRAYER_ATTEMPTS})"
            )
        else:
            logger.warning(
                f"Prayer from {prayer.player} abandoned after {MAX_PRAYER_ATTEMPTS} attempts: "
                f"\"{prayer.message[:60]}\""
            )

    @property
    def size(self) -> int:
        return self._queue.qsize()
