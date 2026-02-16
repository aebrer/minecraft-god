"""FastAPI backend for minecraft-god.

Receives game events from the Paper plugin, batches them, feeds them to the gods,
and queues commands for the Paper plugin to execute.
"""

import asyncio
import collections
import json
import logging
import random
import time

from contextlib import asynccontextmanager
from fastapi import FastAPI
from pydantic import BaseModel

from server.config import GOD_TICK_INTERVAL, PRAYER_KEYWORDS, HERALD_KEYWORDS, MEMORY_CONSOLIDATION_INTERVAL_TICKS
from server.events import EventBuffer
from server.kind_god import KindGod
from server.deep_god import DeepGod
from server.herald_god import HeraldGod
from server.deaths import DeathMemorial
from server.prayer_queue import DivineRequest, DivineRequestQueue, MAX_ATTEMPTS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("minecraft-god")

# Suppress noisy uvicorn websocket warnings (Paper plugin sends Upgrade headers)
logging.getLogger("uvicorn.error").addFilter(
    lambda record: "Unsupported upgrade" not in record.getMessage()
    and "No supported WebSocket" not in record.getMessage()
)

# Global state
event_buffer = EventBuffer()
command_queue: list[dict] = []
kind_god = KindGod()
deep_god = DeepGod()
herald_god = HeraldGod()
death_memorial = DeathMemorial()
prayer_queue = DivineRequestQueue()
_tick_task: asyncio.Task | None = None
_prayer_task: asyncio.Task | None = None
_tick_lock = asyncio.Lock()
_ticks_since_consolidation: int = 0
# Ring buffer of recent god decisions and commands for debugging
_recent_logs: collections.deque = collections.deque(maxlen=50)

# --- In-game feedback messages ---

# Deep God prayer interception — thematic flavor text
_INTERCEPT_GENERIC = [
    "Your prayer sinks into the stone. Something else hears it.",
    "The words do not rise. They fall.",
    "A deeper voice answers.",
    "Your prayer echoes... wrong.",
    "The Kind God reaches for you — and stops.",
    "Something ancient turns its attention toward you.",
    "The silence after your prayer is not empty.",
    "Your words dissolve into pressure and dark.",
    "The prayer lands, but not where you sent it.",
]

_INTERCEPT_DEEP = [
    "You pray from the deep places. The deep places answer.",
    "At this depth, prayers do not rise. They are absorbed.",
    "The stone around you hums. Your prayer was received.",
    "Something beneath the bedrock acknowledges you.",
]

_INTERCEPT_NETHER = [
    "Prayers do not travel here. But something listened.",
    "In this place, the Kind God cannot reach you.",
    "Your words burn before they reach the sky.",
    "The Nether swallows your prayer whole. Something spits back.",
]

_INTERCEPT_THRESHOLD = [
    "The boundary thins. Another presence answers.",
    "The Kind God has spoken too much. The balance shifts.",
    "Too many kindnesses. The deep corrects.",
    "The weight of mercy tips the scales. Something else rises.",
]


def _pick_intercept_message(player_status: dict | None, praying_player: str | None,
                            kind_god_action_count: int) -> str:
    """Pick a context-appropriate interception message."""
    from server.config import KIND_GOD_ACTION_THRESHOLD

    # Check if this was a forced threshold trigger
    if kind_god_action_count >= KIND_GOD_ACTION_THRESHOLD:
        return random.choice(_INTERCEPT_THRESHOLD)

    # Check praying player's location for context
    if player_status and player_status.get("players") and praying_player:
        for p in player_status["players"]:
            if p.get("name", "").lower() != praying_player.lower():
                continue
            dim = p.get("dimension", "")
            y = p.get("location", {}).get("y", 64)

            if "nether" in dim.lower():
                return random.choice(_INTERCEPT_NETHER)
            if y < 0:
                return random.choice(_INTERCEPT_DEEP)

    return random.choice(_INTERCEPT_GENERIC)


def _make_tellraw(message: str, target: str = "@a",
                  color: str = "dark_purple", italic: bool = True) -> dict:
    """Create a tellraw command dict for system/narrator messages."""
    text_json = json.dumps([{"text": message, "color": color, "italic": italic}])
    return {"command": f"tellraw {target} {text_json}"}


def _god_failure_commands(god_name: str, target: str = "@a") -> list[dict]:
    """Generate in-game feedback commands when a god's LLM call fails."""
    messages = {
        "kind": "The Kind God stirs, but cannot speak. Something is wrong.",
        "deep": "The deep rumbles, but forms no words. An error in the stone.",
        "herald": "The Herald opens their mouth, but silence falls. The verse is lost.",
    }
    msg = messages.get(god_name, f"A divine presence falters. ({god_name} error)")
    return [
        _make_tellraw(msg, target=target, color="red", italic=True),
        {"command": f"playsound minecraft:entity.elder_guardian.curse master {target}"},
    ]


def _prayer_abandoned_commands(player: str) -> list[dict]:
    """Generate in-game feedback when a prayer is abandoned after max retries."""
    return [
        _make_tellraw(
            "Your prayer dissolves into silence. The gods cannot reach you now.",
            target=player, color="gray", italic=True,
        ),
        {"command": f"playsound minecraft:block.amethyst_block.resonate master {player}"},
    ]


def _build_player_context(player_status: dict | None, prayer_snapshot: dict | None = None) -> dict:
    """Build a player_context dict mapping lowercase player names to position/facing data.

    Combines the latest player_status beacon with an optional prayer snapshot
    (which has the most accurate position for the praying player).
    """
    ctx = {}

    # Start with the periodic status beacon (covers all online players)
    if player_status and player_status.get("players"):
        for p in player_status["players"]:
            loc = p.get("location", {})
            x, y, z = loc.get("x"), loc.get("y"), loc.get("z")
            if x is not None and y is not None and z is not None:
                ctx[p["name"].lower()] = {
                    "x": int(x), "y": int(y), "z": int(z),
                    "facing": p.get("facing", "N"),
                }

    # Override with the prayer snapshot (captured at the exact moment they spoke)
    if prayer_snapshot:
        loc = prayer_snapshot.get("location", {})
        x, y, z = loc.get("x"), loc.get("y"), loc.get("z")
        name = prayer_snapshot.get("name", "")
        if x is not None and y is not None and z is not None and name:
            ctx[name.lower()] = {
                "x": int(x), "y": int(y), "z": int(z),
                "facing": prayer_snapshot.get("facing", "N"),
            }

    return ctx


class GameEvent(BaseModel):
    type: str
    # All other fields are dynamic, so we accept anything
    model_config = {"extra": "allow"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the god tick loop and prayer loop on startup, cancel on shutdown."""
    global _tick_task, _prayer_task
    logger.info("minecraft-god backend starting up")
    _tick_task = asyncio.create_task(_god_tick_loop())
    _prayer_task = asyncio.create_task(_prayer_loop())
    yield
    logger.info("minecraft-god backend shutting down")
    for task in (_tick_task, _prayer_task):
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    # Flush memory to disk on shutdown
    kind_god.memory._save()
    logger.info("Kind God memory saved")


app = FastAPI(title="minecraft-god", lifespan=lifespan)


@app.post("/event")
async def receive_event(event: GameEvent):
    """Receive a game event from the Paper plugin."""
    event_data = event.model_dump()
    event_buffer.add(event_data)

    # Record player deaths persistently
    if event_data.get("type") == "entity_die" and event_data.get("isPlayer"):
        death_memorial.record_death(event_data)

    # Check chat for prayer or herald keywords — both go through the queue
    if event_data.get("type") == "chat":
        message = event_data.get("message", "").lower()
        is_prayer = any(kw in message for kw in PRAYER_KEYWORDS)
        is_herald = any(kw in message for kw in HERALD_KEYWORDS)

        if is_prayer or is_herald:
            # Determine request type: prayer takes priority if both match
            request_type = "prayer" if is_prayer else "herald"
            player_name = event_data.get("player", event_data.get("sender", "?"))
            # Use the inline snapshot from the chat event (built on the plugin's
            # main thread at the moment the player spoke — always fresh)
            player_snapshot = event_data.get("playerSnapshot") or {}
            recent_chat = event_buffer.get_recent_chat(limit=10)
            request = DivineRequest(
                player=player_name,
                message=event_data.get("message", ""),
                request_type=request_type,
                timestamp=time.time(),
                player_snapshot=player_snapshot,
                recent_chat=recent_chat,
            )
            prayer_queue.enqueue(request)

    return {"status": "ok"}


@app.get("/commands")
async def get_commands():
    """Return pending commands for the Paper plugin to execute, then clear the queue.

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
        "divine_queue_size": prayer_queue.size,
        "command_queue_size": len(command_queue),
        "kind_god_action_count": kind_god.action_count,
        "kind_god_activity_log": len(kind_god._recent_activity),
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


async def _prayer_loop():
    """Background loop that processes divine requests (prayers + herald) from the queue.

    Acquires _tick_lock to prevent concurrent god think() calls with the
    timer tick — conversation history is not safe for concurrent access.
    """
    logger.info("Divine request processing loop started")
    while True:
        try:
            request = await prayer_queue.dequeue()
            logger.info(
                f"[{request.request_type}] Dequeued from {request.player}: "
                f"\"{request.message[:60]}\" "
                f"(attempt {request.attempts + 1}/{MAX_ATTEMPTS}, "
                f"queue remaining: {prayer_queue.size})"
            )

            # Acquire the tick lock — requests and timer ticks must not overlap
            async with _tick_lock:
                await _process_divine_request(request)

        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Divine request processing failed")
            # Requeue so the player isn't silently ignored
            try:
                prayer_queue.requeue(request)
                if request.attempts >= MAX_ATTEMPTS:
                    command_queue.extend(_prayer_abandoned_commands(request.player))
            except Exception:
                logger.exception("Failed to requeue after processing error")


async def _process_divine_request(request: DivineRequest):
    """Process a single divine request (prayer or herald invocation) under _tick_lock."""
    global command_queue

    event_summary = request.build_context()
    player_status = event_buffer.get_player_status()
    player_context = _build_player_context(player_status, request.player_snapshot)
    tick_ts = time.strftime("%H:%M:%S")
    rt = request.request_type  # "prayer" or "herald"

    logger.info(f"[{rt}] Processing {request.player}'s {rt} (lock acquired)")
    logger.info(f"[{rt}] LLM context for {request.player}:\n{event_summary}")

    if rt == "herald":
        # Herald invocations go directly to the Herald
        acting_god = "herald"
        logger.info(f"[herald] === THE HERALD SPEAKS ===")
        commands = await herald_god.think(event_summary)

        if commands is None:
            _recent_logs.append({"time": tick_ts, "god": "herald", "action": "herald_error",
                                 "error": herald_god.last_error, "player": request.player})
            prayer_queue.requeue(request)
            if request.attempts >= MAX_ATTEMPTS:
                command_queue.extend(_prayer_abandoned_commands(request.player))
            logger.warning(f"[herald] Herald failed for {request.player} "
                           f"(attempt {request.attempts}/{MAX_ATTEMPTS})")
            return
    else:
        # Prayers route through Deep God trigger logic
        acting_god = "kind"
        intercept_target = request.player

        if deep_god.should_act(event_summary, player_status, kind_god.action_count,
                               praying_player=request.player):
            acting_god = "deep"
            logger.info("[prayer] === THE DEEP GOD STIRS ===")

            intercept_msg = _pick_intercept_message(
                player_status, request.player, kind_god.action_count)
            command_queue.append(_make_tellraw(intercept_msg, target=intercept_target))
            logger.info(f"[prayer] Interception message to {intercept_target}: {intercept_msg}")

            commands = await deep_god.think(event_summary)

            if commands is None:
                _recent_logs.append({"time": tick_ts, "god": "deep", "action": "prayer_error",
                                     "error": deep_god.last_error, "player": request.player})
                prayer_queue.requeue(request)
                if request.attempts >= MAX_ATTEMPTS:
                    command_queue.extend(_prayer_abandoned_commands(request.player))
                logger.warning(f"[prayer] Deep God failed for {request.player} "
                               f"(attempt {request.attempts}/{MAX_ATTEMPTS})")
                return

            kind_god.notify_deep_god_acted()
            kind_god.reset_action_count()
        else:
            commands = await kind_god.think(event_summary, player_context=player_context)

            if commands is None:
                _recent_logs.append({"time": tick_ts, "god": "kind", "action": "prayer_error",
                                     "error": kind_god.last_error, "player": request.player})
                prayer_queue.requeue(request)
                if request.attempts >= MAX_ATTEMPTS:
                    command_queue.extend(_prayer_abandoned_commands(request.player))
                logger.warning(f"[prayer] Kind God failed for {request.player} "
                               f"(attempt {request.attempts}/{MAX_ATTEMPTS})")
                return

    if commands:
        command_queue.extend(commands)
        cmd_summaries = []
        for c in commands:
            if c.get("type") == "build_schematic":
                cmd_summaries.append(f"build_schematic({c.get('blueprint_id')} @ {c.get('x')},{c.get('y')},{c.get('z')})")
            else:
                cmd_summaries.append(c.get("command", "?")[:80])
        logger.info(f"[{rt}] Answered {request.player}: {len(commands)} commands queued")
        _recent_logs.append({"time": tick_ts, "god": acting_god, "action": f"{rt}_answered",
                             "commands": cmd_summaries, "player": request.player,
                             "context": event_summary})
    else:
        logger.info(f"[{rt}] {acting_god} god was silent for {request.player}'s {rt}")
        _recent_logs.append({"time": tick_ts, "god": acting_god, "action": f"{rt}_silent",
                             "player": request.player, "context": event_summary})

    # Herald can also respond to prayers independently (but not to herald invocations —
    # that would double-trigger)
    if rt == "prayer" and herald_god.should_act(event_summary):
        logger.info("[prayer] === THE HERALD ALSO SPEAKS ===")
        herald_commands = await herald_god.think(event_summary)
        if herald_commands is None:
            command_queue.extend(_god_failure_commands("herald"))
        elif herald_commands:
            command_queue.extend(herald_commands)
            logger.info(f"[prayer] Queued {len(herald_commands)} Herald commands")


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
    """Run one cycle of spontaneous divine deliberation (not prayers)."""
    if _tick_lock.locked():
        logger.debug("[tick] Tick skipped — lock held (prayer or previous tick in progress)")
        return

    async with _tick_lock:
        await _god_tick_inner()


async def _god_tick_inner():
    """Actual tick logic for spontaneous god actions (not prayers).

    Prayers are filtered out of the event summary — they're handled
    separately by the prayer queue.
    """
    global command_queue

    event_summary = event_buffer.drain_and_summarize(
        death_memorial=death_memorial, filter_divine=True)
    if not event_summary:
        return

    player_status = event_buffer.get_player_status()
    player_context = _build_player_context(player_status)

    tick_ts = time.strftime("%H:%M:%S")
    acting_god = "kind"

    logger.info(f"[tick] LLM context:\n{event_summary}")

    if deep_god.should_act(event_summary, player_status, kind_god.action_count):
        acting_god = "deep"
        logger.info("[tick] === THE DEEP GOD STIRS ===")

        commands = await deep_god.think(event_summary)

        if commands is None:
            command_queue.extend(_god_failure_commands("deep"))
            _recent_logs.append({"time": tick_ts, "god": "deep", "action": "tick_error",
                                 "error": deep_god.last_error})
            commands = []

        kind_god.notify_deep_god_acted()
        kind_god.reset_action_count()
    else:
        commands = await kind_god.think(event_summary, player_context=player_context)

        if commands is None:
            command_queue.extend(_god_failure_commands("kind"))
            _recent_logs.append({"time": tick_ts, "god": "kind", "action": "tick_error",
                                 "error": kind_god.last_error})
            commands = []

    if commands:
        command_queue.extend(commands)
        cmd_summaries = []
        for c in commands:
            if c.get("type") == "build_schematic":
                cmd_summaries.append(f"build_schematic({c.get('blueprint_id')} @ {c.get('x')},{c.get('y')},{c.get('z')})")
            else:
                cmd_summaries.append(c.get("command", "?")[:80])
        logger.info(f"[tick] {acting_god} god acted: {len(commands)} commands queued")
        _recent_logs.append({"time": tick_ts, "god": acting_god, "action": "tick_acted",
                             "commands": cmd_summaries, "context": event_summary})
    else:
        logger.info(f"[tick] {acting_god} god was silent")
        _recent_logs.append({"time": tick_ts, "god": acting_god, "action": "tick_silent",
                             "context": event_summary})

    # The Herald speaks independently — not silenced by either god
    if herald_god.should_act(event_summary):
        logger.info("[tick] === THE HERALD SPEAKS ===")
        herald_commands = await herald_god.think(event_summary)

        if herald_commands is None:
            command_queue.extend(_god_failure_commands("herald"))
            _recent_logs.append({"time": tick_ts, "god": "herald", "action": "tick_error",
                                 "error": herald_god.last_error})
        elif herald_commands:
            command_queue.extend(herald_commands)
            logger.info(f"[tick] Queued {len(herald_commands)} Herald commands")
            herald_summaries = [c.get("command", "?")[:80] for c in herald_commands]
            _recent_logs.append({"time": tick_ts, "god": "herald", "action": "tick_spoke",
                                 "commands": herald_summaries})

    # Memory consolidation check (only on full ticks, not herald-only)
    global _ticks_since_consolidation
    _ticks_since_consolidation += 1
    if _ticks_since_consolidation >= MEMORY_CONSOLIDATION_INTERVAL_TICKS:
        _ticks_since_consolidation = 0
        logger.info("=== KIND GOD MEMORY CONSOLIDATION ===")
        try:
            await kind_god.memory.consolidate(kind_god._recent_activity)
        except Exception:
            logger.exception("Memory consolidation failed")
