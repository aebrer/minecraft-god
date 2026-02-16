import logging
import time
from collections import defaultdict
from threading import Lock

from server.prayer_queue import is_divine_request

logger = logging.getLogger("minecraft-god")


class EventBuffer:
    """Accumulates game events and drains them as summarized text for the LLM."""

    # Player status older than this is considered stale (no one online)
    _STATUS_STALE_SECONDS = 120

    def __init__(self):
        self._events: list[dict] = []
        self._lock = Lock()
        self._latest_player_status: dict | None = None
        self._player_status_time: float = 0

    def add(self, event: dict):
        with self._lock:
            if event.get("type") == "player_status":
                self._latest_player_status = event
                self._player_status_time = time.time()
            else:
                self._events.append(event)

    def has_divine_request(self) -> bool:
        """Check if any recent chat event contains prayer or herald keywords."""
        with self._lock:
            for event in self._events:
                if event.get("type") == "chat":
                    if is_divine_request(event.get("message", "")):
                        return True
        return False

    def get_recent_chat(self, limit: int = 10) -> list[dict]:
        """Return recent chat events (non-destructive) for prayer context snapshots."""
        with self._lock:
            chats = [e for e in self._events if e.get("type") == "chat"]
            return chats[-limit:]

    def get_player_status(self) -> dict | None:
        with self._lock:
            if self._latest_player_status is None:
                return None
            # Status beacon stops when no players are online — if it hasn't
            # been refreshed recently, treat it as stale (no one online)
            if time.time() - self._player_status_time > self._STATUS_STALE_SECONDS:
                return None
            return self._latest_player_status

    def drain_and_summarize(self, death_memorial=None, filter_divine: bool = False) -> str | None:
        """Drain the buffer and return a human-readable summary for the LLM.

        Returns None if nothing happened worth reporting.
        If filter_divine is True, chat messages containing prayer or herald keywords
        are excluded (they'll be handled by the divine request queue instead).
        """
        with self._lock:
            events = self._events.copy()
            self._events.clear()
            player_status = self._latest_player_status

        if filter_divine:
            filtered = []
            stripped_count = 0
            for e in events:
                if e.get("type") == "chat" and is_divine_request(e.get("message", "")):
                    stripped_count += 1
                    continue
                filtered.append(e)
            if stripped_count:
                logger.info(f"[tick] Filtered {stripped_count} divine request chat(s) from tick context")
            events = filtered

        if not events:
            return None

        sections = []

        # Player status snapshot
        if player_status and player_status.get("players"):
            lines = []
            for p in player_status["players"]:
                loc = p.get("location", {})
                # Basic info
                # Facing and biome
                facing = p.get("facing", "?")
                look_v = p.get("lookingVertical", "ahead")
                biome = p.get("biome", "?")
                info = (
                    f"  - {p['name']}: at ({loc.get('x', '?')}, {loc.get('y', '?')}, {loc.get('z', '?')}) "
                    f"in {p.get('dimension', '?')} ({biome}), facing {facing} looking {look_v}, "
                    f"health={p.get('health', '?')}/{p.get('maxHealth', '?')}, "
                    f"food={p.get('foodLevel', '?')}/20, level={p.get('level', '?')}"
                )
                # Armor
                armor = [a.replace("minecraft:", "") for a in p.get("armor", []) if a != "minecraft:air"]
                if armor:
                    info += f"\n    Armor: {', '.join(armor)}"
                else:
                    info += "\n    Armor: none"
                # Held item
                if p.get("mainHand"):
                    info += f" | Holding: {p['mainHand'].replace('minecraft:', '')}"
                # Full inventory
                inventory = p.get("inventory", {})
                if inventory:
                    # Sort by count descending for readability
                    sorted_inv = sorted(inventory.items(), key=lambda x: -x[1])
                    items_str = ", ".join(f"{count} {item}" for item, count in sorted_inv)
                    info += f"\n    Inventory: {items_str}"
                else:
                    info += "\n    Inventory: empty"
                # What the player is looking at
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
                # Immediate surroundings (8 blocks)
                close = p.get("closeEntities", {})
                if close:
                    sorted_close = sorted(close.items(), key=lambda x: -x[1])
                    close_str = ", ".join(f"{count} {etype}" for etype, count in sorted_close)
                    info += f"\n    Immediate vicinity (8 blocks): {close_str}"
                # Notable blocks nearby (8 blocks)
                notable = p.get("notableBlocks", {})
                if notable:
                    sorted_notable = sorted(notable.items(), key=lambda x: -x[1])
                    notable_str = ", ".join(f"{count} {block}" for block, count in sorted_notable)
                    info += f"\n    Notable blocks nearby (8 blocks): {notable_str}"
                # Nearby entities (wider area)
                nearby = p.get("nearbyEntities", {})
                if nearby:
                    sorted_nearby = sorted(nearby.items(), key=lambda x: -x[1])
                    nearby_str = ", ".join(f"{count} {etype}" for etype, count in sorted_nearby)
                    info += f"\n    Nearby entities (32 blocks): {nearby_str}"
                # Death history
                if death_memorial:
                    death_context = death_memorial.format_for_summary(
                        p["name"],
                        loc.get("x", 0),
                        loc.get("y", 0),
                        loc.get("z", 0),
                    )
                    if death_context:
                        info += f"\n{death_context}"
                lines.append(info)
            sections.append("PLAYERS ONLINE:\n" + "\n".join(lines))

        # Chat messages — verbatim, wrapped in delimiters
        chats = [e for e in events if e.get("type") == "chat"]
        if chats:
            lines = []
            for c in chats:
                lines.append(f'  [PLAYER CHAT] {c.get("player", "?")}: "{c.get("message", "")}"')
            sections.append("CHAT:\n" + "\n".join(lines))

        # Deaths — verbatim
        deaths = [e for e in events if e.get("type") == "entity_die" and e.get("isPlayer")]
        if deaths:
            lines = []
            for d in deaths:
                cause = d.get("cause", "unknown")
                killer = d.get("damagingEntity")
                loc = d.get("location", {})
                msg = f'  {d.get("playerName", "?")} died ({cause}'
                if killer:
                    msg += f", killed by {killer}"
                msg += f') at ({loc.get("x", "?")}, {loc.get("y", "?")}, {loc.get("z", "?")})'
                lines.append(msg)
            sections.append("PLAYER DEATHS:\n" + "\n".join(lines))

        # Mob kills by players
        mob_kills = [
            e for e in events
            if e.get("type") == "entity_die" and not e.get("isPlayer")
        ]
        if mob_kills:
            # Aggregate by killer and mob type
            kill_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
            for k in mob_kills:
                killer = k.get("damagingEntity", "unknown")
                mob = k.get("entity", "unknown")
                # Strip minecraft: prefix for readability
                mob = mob.replace("minecraft:", "")
                kill_counts[killer][mob] += 1

            lines = []
            for killer, mobs in kill_counts.items():
                parts = [f"{count} {mob}" for mob, count in mobs.items()]
                lines.append(f"  {killer} killed: {', '.join(parts)}")
            sections.append("MOB KILLS:\n" + "\n".join(lines))

        # Player joins/leaves
        joins = [e for e in events if e.get("type") in ("player_join", "player_initial_spawn")]
        leaves = [e for e in events if e.get("type") == "player_leave"]
        if joins or leaves:
            lines = []
            for j in joins:
                lines.append(f'  {j.get("player", "?")} joined the world')
            for l in leaves:
                lines.append(f'  {l.get("player", "?")} left the world')
            sections.append("ARRIVALS/DEPARTURES:\n" + "\n".join(lines))

        # Block breaks — aggregate by player, block type, and Y-level range
        breaks = [e for e in events if e.get("type") == "block_break"]
        if breaks:
            sections.append(_summarize_blocks(breaks, "MINING ACTIVITY"))

        # Block places — aggregate similarly
        places = [e for e in events if e.get("type") == "block_place"]
        if places:
            sections.append(_summarize_blocks(places, "BUILDING ACTIVITY"))

        # Combat — deduplicate and summarize
        combat = [e for e in events if e.get("type") == "combat"]
        if combat:
            sections.append(_summarize_combat(combat))

        # Weather changes
        weather = [e for e in events if e.get("type") == "weather_change"]
        if weather:
            latest = weather[-1]
            new_weather = latest.get("newWeather", "Clear")
            sections.append(f"WEATHER: Changed to {new_weather}")

        if not sections:
            return None

        return "\n\n".join(sections)


def _summarize_blocks(events: list[dict], header: str) -> str:
    """Aggregate block events by player and block type."""
    # player -> {block_type: count}
    by_player: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    # Track notable Y levels per player
    min_y: dict[str, int] = {}

    for e in events:
        player = e.get("player", "?")
        block = e.get("block", "unknown").replace("minecraft:", "")
        by_player[player][block] += 1
        loc = e.get("location", {})
        y = loc.get("y")
        if y is not None:
            if player not in min_y or y < min_y[player]:
                min_y[player] = y

    lines = []
    for player, blocks in by_player.items():
        # Sort by count descending, show top 5
        sorted_blocks = sorted(blocks.items(), key=lambda x: -x[1])
        notable = sorted_blocks[:5]
        total = sum(blocks.values())
        parts = [f"{count} {block}" for block, count in notable]
        if len(sorted_blocks) > 5:
            rest = total - sum(c for _, c in notable)
            parts.append(f"{rest} other blocks")

        depth_note = ""
        if player in min_y:
            depth_note = f" (deepest: Y={min_y[player]})"

        lines.append(f"  {player}: {', '.join(parts)}{depth_note}")

    return f"{header}:\n" + "\n".join(lines)


def _summarize_combat(events: list[dict]) -> str:
    """Deduplicate and summarize combat events."""
    # Group by (attacker, target) pair within 5-second windows
    fights: dict[tuple, dict] = {}
    for e in events:
        attacker = e.get("attackerName") or e.get("attacker", "?")
        target = e.get("hurtEntityName", "?")
        key = (attacker, target)
        ts = e.get("timestamp", 0)

        if key in fights and abs(ts - fights[key]["last_ts"]) < 5000:
            fights[key]["total_damage"] += e.get("damage", 0)
            fights[key]["hits"] += 1
            fights[key]["last_ts"] = ts
        else:
            fights[key] = {
                "total_damage": e.get("damage", 0),
                "hits": 1,
                "last_ts": ts,
                "cause": e.get("cause", "unknown"),
                "location": e.get("location", {}),
            }

    lines = []
    for (attacker, target), info in fights.items():
        loc = info["location"]
        target_clean = target.replace("minecraft:", "")
        attacker_clean = attacker.replace("minecraft:", "")
        lines.append(
            f"  {attacker_clean} vs {target_clean}: "
            f"{info['hits']} hits, {info['total_damage']:.0f} total damage "
            f"at ({loc.get('x', '?')}, {loc.get('y', '?')}, {loc.get('z', '?')})"
        )

    return "COMBAT:\n" + "\n".join(lines)
