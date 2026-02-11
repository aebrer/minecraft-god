"""FastAPI backend for minecraft-god.

Receives game events from the behavior pack, batches them, feeds them to the gods,
and queues commands for the behavior pack to execute.
"""

import asyncio
import collections
import logging
import time

from contextlib import asynccontextmanager
from fastapi import FastAPI
from pydantic import BaseModel

from server.config import GOD_TICK_INTERVAL, PRAYER_COOLDOWN, PRAYER_KEYWORDS, HERALD_KEYWORDS, MEMORY_CONSOLIDATION_INTERVAL_TICKS
from server.events import EventBuffer
from server.kind_god import KindGod
from server.deep_god import DeepGod
from server.herald_god import HeraldGod
from server.deaths import DeathMemorial

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("minecraft-god")

# Global state
event_buffer = EventBuffer()
command_queue: list[dict] = []
kind_god = KindGod()
deep_god = DeepGod()
herald_god = HeraldGod()
death_memorial = DeathMemorial()
last_prayer_time: float = 0
_tick_task: asyncio.Task | None = None
_tick_lock = asyncio.Lock()
_ticks_since_consolidation: int = 0
# Ring buffer of recent god decisions and commands for debugging
_recent_logs: collections.deque = collections.deque(maxlen=50)


class GameEvent(BaseModel):
    type: str
    # All other fields are dynamic, so we accept anything
    model_config = {"extra": "allow"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the god tick loop on startup, cancel on shutdown."""
    global _tick_task
    logger.info("minecraft-god backend starting up")
    _tick_task = asyncio.create_task(_god_tick_loop())
    yield
    logger.info("minecraft-god backend shutting down")
    if _tick_task:
        _tick_task.cancel()
        try:
            await _tick_task
        except asyncio.CancelledError:
            pass
    # Flush memory to disk on shutdown
    kind_god.memory._save()
    logger.info("Kind God memory saved")


app = FastAPI(title="minecraft-god", lifespan=lifespan)


@app.post("/event")
async def receive_event(event: GameEvent):
    """Receive a game event from the behavior pack."""
    event_data = event.model_dump()
    event_buffer.add(event_data)

    # Record player deaths persistently
    if event_data.get("type") == "entity_die" and event_data.get("isPlayer"):
        death_memorial.record_death(event_data)

    # Fast-path: if this chat contains prayer or herald keywords, trigger immediate tick
    if event_data.get("type") == "chat":
        message = event_data.get("message", "").lower()
        is_prayer = any(kw in message for kw in PRAYER_KEYWORDS)
        is_herald = any(kw in message for kw in HERALD_KEYWORDS)
        if is_prayer or is_herald:
            global last_prayer_time
            now = time.time()
            if now - last_prayer_time >= PRAYER_COOLDOWN:
                last_prayer_time = now
                if is_herald and not is_prayer:
                    # Herald-only — skip Kind God, just run Herald
                    logger.info("Herald invoked — triggering Herald-only tick")
                    asyncio.create_task(_herald_only_tick())
                else:
                    praying_player = event_data.get("player", event_data.get("sender"))
                    logger.info(f"Prayer detected from {praying_player} — triggering immediate god tick")
                    asyncio.create_task(_god_tick(praying_player=praying_player))

    return {"status": "ok"}


@app.get("/commands")
async def get_commands():
    """Return pending commands for the behavior pack to execute, then clear the queue.

    Uses atomic swap to prevent duplicate delivery — even if two clients
    poll simultaneously, only one will receive the commands.
    """
    global command_queue
    commands = command_queue
    command_queue = []
    return commands


@app.post("/commands")
async def inject_commands(commands: list[dict]):
    """Inject commands directly into the queue (for testing/admin use)."""
    global command_queue
    command_queue.extend(commands)
    logger.info(f"Injected {len(commands)} commands via POST /commands")
    return {"status": "ok", "queued": len(commands)}


@app.get("/status")
async def get_status():
    """Debug endpoint showing current state."""
    return {
        "event_buffer_size": len(event_buffer._events),
        "command_queue_size": len(command_queue),
        "kind_god_action_count": kind_god.action_count,
        "kind_god_history_length": len(kind_god.conversation_history),
        "deep_god_history_length": len(deep_god.conversation_history),
        "herald_history_length": len(herald_god.conversation_history),
        "kind_god_memory_count": len(kind_god.memory.memories),
        "last_consolidation": kind_god.memory.last_consolidation,
        "ticks_until_consolidation": max(0, MEMORY_CONSOLIDATION_INTERVAL_TICKS - _ticks_since_consolidation),
        "player_status": event_buffer.get_player_status(),
        "death_records": {p: len(d) for p, d in death_memorial.deaths.items()},
    }


@app.get("/logs")
async def get_logs():
    """Recent god decisions and commands — ring buffer of last 50 ticks."""
    return list(_recent_logs)


async def _herald_only_tick():
    """Run just the Herald, skipping the Kind/Deep God. Used when chat is directed at the Herald."""
    global command_queue

    if _tick_lock.locked():
        return

    async with _tick_lock:
        event_summary = event_buffer.drain_and_summarize(death_memorial=death_memorial)
        if not event_summary:
            return

        if herald_god.should_act(event_summary):
            logger.info("=== THE HERALD SPEAKS ===")
            herald_commands = await herald_god.think(event_summary)
            if herald_commands:
                command_queue.extend(herald_commands)
                logger.info(f"Queued {len(herald_commands)} Herald commands")


async def _god_tick_loop():
    """Background loop that runs the god tick at regular intervals."""
    logger.info(f"God tick loop started (interval: {GOD_TICK_INTERVAL}s)")
    while True:
        try:
            await asyncio.sleep(GOD_TICK_INTERVAL)
            await _god_tick()
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("God tick failed")


async def _god_tick(praying_player: str | None = None):
    """Run one cycle of divine deliberation."""
    global command_queue

    # Prevent concurrent ticks (prayer fast-path vs regular timer)
    if _tick_lock.locked():
        logger.debug("Tick already in progress, skipping")
        return

    async with _tick_lock:
        await _god_tick_inner(praying_player=praying_player)


async def _god_tick_inner(praying_player: str | None = None):
    """Actual tick logic, called under lock."""
    global command_queue

    event_summary = event_buffer.drain_and_summarize(death_memorial=death_memorial)
    if not event_summary:
        return

    player_status = event_buffer.get_player_status()

    # Check if the Deep God should act
    # When a prayer triggered this tick, only consider the praying player's position
    tick_ts = time.strftime("%H:%M:%S")
    acting_god = "kind"
    if deep_god.should_act(event_summary, player_status, kind_god.action_count,
                           praying_player=praying_player):
        acting_god = "deep"
        logger.info("=== THE DEEP GOD STIRS ===")
        commands = await deep_god.think(event_summary)

        # Notify the Kind God that the Other acted
        kind_god.notify_deep_god_acted()

        # Reset the Kind God's action counter
        kind_god.reset_action_count()
    else:
        commands = await kind_god.think(event_summary)

    if commands:
        command_queue.extend(commands)
        logger.info(f"Queued {len(commands)} commands")
        cmd_summaries = []
        for c in commands:
            if c.get("type") == "build_schematic":
                cmd_summaries.append(f"build_schematic({c.get('blueprint_id')} @ {c.get('x')},{c.get('y')},{c.get('z')})")
            else:
                cmd_summaries.append(c.get("command", "?")[:80])
        _recent_logs.append({"time": tick_ts, "god": acting_god, "action": "acted",
                             "commands": cmd_summaries, "prayer": praying_player})
    else:
        _recent_logs.append({"time": tick_ts, "god": acting_god, "action": "silent",
                             "prayer": praying_player})

    # The Herald speaks independently — not silenced by either god
    if herald_god.should_act(event_summary):
        logger.info("=== THE HERALD SPEAKS ===")
        herald_commands = await herald_god.think(event_summary)
        if herald_commands:
            command_queue.extend(herald_commands)
            logger.info(f"Queued {len(herald_commands)} Herald commands")
            herald_summaries = [c.get("command", "?")[:80] for c in herald_commands]
            _recent_logs.append({"time": tick_ts, "god": "herald", "action": "spoke",
                                 "commands": herald_summaries})

    # Memory consolidation check (only on full ticks, not herald-only)
    global _ticks_since_consolidation
    _ticks_since_consolidation += 1
    if _ticks_since_consolidation >= MEMORY_CONSOLIDATION_INTERVAL_TICKS:
        _ticks_since_consolidation = 0
        logger.info("=== KIND GOD MEMORY CONSOLIDATION ===")
        try:
            await kind_god.memory.consolidate(kind_god.conversation_history)
        except Exception:
            logger.exception("Memory consolidation failed")
