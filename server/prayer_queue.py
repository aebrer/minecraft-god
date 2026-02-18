"""Divine request detection and queuing.

Provides keyword classification (is_divine_request, classify_divine_request)
as the single source of truth for prayer/herald/remember detection, and a FIFO
queue for processing player-initiated god invocations. Each request captures the
game state at the moment it was spoken, so the god can respond with accurate
context even if the player has moved on by the time the request is processed.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Literal

from server.config import PRAYER_KEYWORDS, HERALD_KEYWORDS, DIG_KEYWORDS, REMEMBER_KEYWORDS

logger = logging.getLogger("minecraft-god")

MAX_ATTEMPTS = 5

# All keywords that trigger divine requests (prayers + herald invocations + remember).
# "remember" is included because it goes through the DivineRequestQueue just like
# prayers and heralds — it should be filtered from the spontaneous tick context.
_ALL_DIVINE_KEYWORDS = PRAYER_KEYWORDS | HERALD_KEYWORDS | DIG_KEYWORDS | REMEMBER_KEYWORDS


def is_divine_request(message: str) -> bool:
    """Check if a chat message contains any divine request keywords."""
    lower = message.lower()
    return any(kw in lower for kw in _ALL_DIVINE_KEYWORDS)


def classify_divine_request(message: str) -> str | None:
    """Classify a chat message as "remember", "dig", "prayer", "herald", or None.

    Priority: remember > dig > prayer > herald.
    """
    lower = message.lower()
    is_remember = any(kw in lower for kw in REMEMBER_KEYWORDS)
    is_dig = any(kw in lower for kw in DIG_KEYWORDS)
    is_prayer = any(kw in lower for kw in PRAYER_KEYWORDS)
    is_herald = any(kw in lower for kw in HERALD_KEYWORDS)
    if is_remember:
        return "remember"
    if is_dig:
        return "dig"
    if is_prayer:
        return "prayer"
    if is_herald:
        return "herald"
    return None


@dataclass
class DivineRequest:
    """A player-initiated request waiting for divine response."""
    player: str
    message: str
    request_type: Literal["prayer", "herald", "dig", "remember"]
    timestamp: float
    player_snapshot: dict          # full player status dict at request time
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
            x, y, z = loc.get('x', '?'), loc.get('y', '?'), loc.get('z', '?')
            info = (
                f"  - {p['name']}: POSITION: x={x}, y={y}, z={z} "
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

            label = {
                "herald": "INVOKING PLAYER",
                "dig": "REQUESTING PLAYER",
            }.get(self.request_type, "PRAYING PLAYER")
            sections.append(f"{label}:\n" + info)

        # The request itself plus surrounding non-divine chat
        # Filter out other players' prayers/herald invocations so the god only sees this one
        chat_lines = []
        for c in self.recent_chat:
            msg = c.get("message", "")
            sender = c.get("player", "?")
            is_this_request = (sender == self.player and msg == self.message)
            is_other_request = (not is_this_request and is_divine_request(msg))
            if not is_other_request:
                chat_lines.append(f'  [PLAYER CHAT] {sender}: "{msg}"')
        # Make sure the request itself is included even if it wasn't in recent_chat
        request_line = f'  [PLAYER CHAT] {self.player}: "{self.message}"'
        if request_line not in chat_lines:
            chat_lines.append(request_line)
        if chat_lines:
            sections.append("CHAT:\n" + "\n".join(chat_lines))

        return "\n\n".join(sections) if sections else f'{self.player}: "{self.message}"'


class DivineRequestQueue:
    """FIFO queue of divine requests (prayers + herald invocations + remember)."""

    def __init__(self):
        self._queue: asyncio.Queue[DivineRequest] = asyncio.Queue()

    def enqueue(self, request: DivineRequest):
        self._queue.put_nowait(request)
        logger.info(
            f"[{request.request_type}] Queued from {request.player}: "
            f"\"{request.message[:60]}\" (queue depth: {self._queue.qsize()})"
        )

    async def dequeue(self) -> DivineRequest:
        """Block until a request is available."""
        return await self._queue.get()

    def requeue(self, request: DivineRequest) -> bool:
        """Put a failed request back for retry. Returns False if abandoned."""
        request.attempts += 1
        if request.attempts < MAX_ATTEMPTS:
            self._queue.put_nowait(request)
            logger.info(
                f"[{request.request_type}] {request.player} requeued "
                f"(attempt {request.attempts}/{MAX_ATTEMPTS})"
            )
            return True
        else:
            logger.warning(
                f"[{request.request_type}] {request.player} abandoned after "
                f"{MAX_ATTEMPTS} attempts: \"{request.message[:60]}\""
            )
            return False

    @property
    def size(self) -> int:
        return self._queue.qsize()
