"""FastAPI backend for minecraft-god.

Receives game events from the behavior pack, batches them, feeds them to the gods,
and queues commands for the behavior pack to execute.
"""

import asyncio
import logging
import time

from contextlib import asynccontextmanager
from fastapi import FastAPI
from pydantic import BaseModel

from server.config import GOD_TICK_INTERVAL, PRAYER_COOLDOWN
from server.events import EventBuffer
from server.kind_god import KindGod
from server.deep_god import DeepGod

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
last_prayer_time: float = 0
_tick_task: asyncio.Task | None = None


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


app = FastAPI(title="minecraft-god", lifespan=lifespan)


@app.post("/event")
async def receive_event(event: GameEvent):
    """Receive a game event from the behavior pack."""
    event_data = event.model_dump()
    event_buffer.add(event_data)

    # Prayer fast-path: if chat contains prayer keywords, trigger immediate tick
    if event_data.get("type") == "chat" and event_buffer.has_prayer():
        global last_prayer_time
        now = time.time()
        if now - last_prayer_time >= PRAYER_COOLDOWN:
            last_prayer_time = now
            logger.info("Prayer detected — triggering immediate god tick")
            asyncio.create_task(_god_tick())

    return {"status": "ok"}


@app.get("/commands")
async def get_commands():
    """Return pending commands for the behavior pack to execute, then clear the queue."""
    global command_queue
    commands = command_queue.copy()
    command_queue.clear()
    return commands


@app.get("/status")
async def get_status():
    """Debug endpoint showing current state."""
    return {
        "event_buffer_size": len(event_buffer._events),
        "command_queue_size": len(command_queue),
        "kind_god_action_count": kind_god.action_count,
        "kind_god_history_length": len(kind_god.conversation_history),
        "deep_god_history_length": len(deep_god.conversation_history),
        "player_status": event_buffer.get_player_status(),
    }


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


async def _god_tick():
    """Run one cycle of divine deliberation."""
    global command_queue

    event_summary = event_buffer.drain_and_summarize()
    if not event_summary:
        return

    player_status = event_buffer.get_player_status()

    # Check if the Deep God should act
    if deep_god.should_act(event_summary, player_status, kind_god.action_count):
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
