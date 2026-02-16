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

from server.config import GOD_TICK_INTERVAL, MEMORY_CONSOLIDATION_INTERVAL_SECONDS, CONSOLIDATION_LOG_FILE
from server.events import EventBuffer
from server.kind_god import KindGod
from server.deep_god import DeepGod
from server.herald_god import HeraldGod
from server.deaths import DeathMemorial
from server.prayer_queue import DivineRequest, DivineRequestQueue, MAX_ATTEMPTS, classify_divine_request

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
# Ring buffer of recent god decisions and commands for debugging
_recent_logs: collections.deque = collections.deque(maxlen=50)
# Activity log for memory consolidation — human-readable timeline of all god/player activity.
# Persists to data/consolidation_log.json on shutdown and loads on startup.
_consolidation_log: list[str] = []
_CONSOLIDATION_LOG_MAX = 500
# Cooldown after failed consolidation to avoid hammering a failing LLM every tick
_consolidation_cooldown_until: float = 0
# Force flag — set by "remember" keyword to trigger immediate consolidation
_force_consolidation: bool = False


def _load_consolidation_log():
    """Load the activity log from disk if it exists."""
    if not CONSOLIDATION_LOG_FILE.exists():
        return
    try:
        data = json.loads(CONSOLIDATION_LOG_FILE.read_text())
        if isinstance(data, list):
            _consolidation_log.extend(data[:_CONSOLIDATION_LOG_MAX])
            logger.info(f"Loaded {len(_consolidation_log)} activity log entries from disk")
    except (json.JSONDecodeError, OSError):
        logger.warning("Activity log file corrupt — starting with empty log")


def _save_consolidation_log():
    """Save the activity log to disk."""
    CONSOLIDATION_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        CONSOLIDATION_LOG_FILE.write_text(json.dumps(_consolidation_log))
    except OSError:
        logger.exception("Failed to save activity log to disk")


def _log_activity(entry: str):
    """Append a timestamped entry to the consolidation activity log."""
    if len(_consolidation_log) >= _CONSOLIDATION_LOG_MAX:
        logger.warning(
            f"Consolidation log at capacity ({_CONSOLIDATION_LOG_MAX} entries) "
            "— dropping oldest entry. Consolidation may be failing."
        )
        _consolidation_log.pop(0)
    ts = time.strftime("%H:%M")
    _consolidation_log.append(f"[{ts}] {entry}")


def _summarize_commands(commands: list[dict]) -> str:
    """Produce a short summary of commands for the activity log."""
    parts = []
    for c in commands:
        if c.get("type") == "build_schematic":
            parts.append(f"build_schematic({c.get('blueprint_id')})")
        else:
            cmd = c.get("command", "?")
            # Extract the meaningful part of tellraw commands
            if "tellraw" in cmd and '"text"' in cmd:
                try:
                    text_json = cmd.split("tellraw")[1].strip()
                    # Skip the target selector, grab the JSON
                    json_start = text_json.index("[")
                    parsed = json.loads(text_json[json_start:])
                    msg = parsed[0].get("text", "?") if parsed else "?"
                    parts.append(f'said: "{msg[:80]}"')
                    continue
                except (ValueError, json.JSONDecodeError, IndexError, KeyError):
                    pass
            parts.append(cmd[:80])
    return "; ".join(parts) if parts else "silence"


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


def _trigger_remember(player: str):
    """Handle a 'remember' keyword — force immediate consolidation on next tick."""
    global _force_consolidation
    _force_consolidation = True
    command_queue.append(
        _make_tellraw(
            "The Kind God closes its eyes and reflects on all that has passed...",
            target=player, color="gold", italic=True,
        )
    )
    command_queue.append(
        {"command": f"playsound minecraft:block.amethyst_block.chime master {player}"}
    )
    logger.info(f"[remember] {player} triggered memory consolidation")


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
    _load_consolidation_log()
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
    # Flush state to disk on shutdown
    kind_god.memory._save()
    _save_consolidation_log()
    logger.info("Kind God memory and activity log saved")


app = FastAPI(title="minecraft-god", lifespan=lifespan)


@app.post("/event")
async def receive_event(event: GameEvent):
    """Receive a game event from the Paper plugin."""
    event_data = event.model_dump()
    event_buffer.add(event_data)

    # Log non-status events (status beacon fires every ~30s, too noisy)
    event_type = event_data.get("type", "?")
    if event_type != "player_status":
        logger.info(f"[event] {event_type}" + (
            f" from {event_data.get('player', '?')}" if event_data.get("player") else ""))

    # Record player deaths persistently + activity log
    if event_data.get("type") == "entity_die" and event_data.get("isPlayer"):
        death_memorial.record_death(event_data)
        player_name = event_data.get("playerName", "?")
        cause = event_data.get("cause", "unknown")
        killer = event_data.get("damagingEntity", "")
        loc = event_data.get("location", {})
        death_desc = f"{player_name} died ({cause}"
        if killer:
            death_desc += f", killed by {killer}"
        death_desc += f") at ({loc.get('x', '?')}, {loc.get('y', '?')}, {loc.get('z', '?')})"
        _log_activity(f"DEATH: {death_desc}")

    # Player joins/leaves
    if event_data.get("type") in ("player_join", "player_initial_spawn"):
        _log_activity(f"JOIN: {event_data.get('player', '?')} joined the world")
    if event_data.get("type") == "player_leave":
        _log_activity(f"LEAVE: {event_data.get('player', '?')} left the world")

    # Check chat for prayer, herald, or remember keywords
    if event_data.get("type") == "chat":
        player_name = event_data.get("player", event_data.get("sender", "?"))
        message = event_data.get("message", "")
        request_type = classify_divine_request(message)

        if request_type == "remember":
            # "remember" triggers immediate memory consolidation
            _log_activity(f'REMEMBER: {player_name}: "{message}"')
            _trigger_remember(player_name)
        elif request_type:
            # Prayer or herald — queue for divine response
            label = "PRAYER" if request_type == "prayer" else "HERALD REQUEST"
            _log_activity(f'{label}: {player_name}: "{message}"')

            # Use the inline snapshot from the chat event (built on the plugin's
            # main thread at the moment the player spoke — always fresh)
            player_snapshot = event_data.get("playerSnapshot") or {}
            recent_chat = event_buffer.get_recent_chat(limit=10)
            request = DivineRequest(
                player=player_name,
                message=message,
                request_type=request_type,
                timestamp=time.time(),
                player_snapshot=player_snapshot,
                recent_chat=recent_chat,
            )
            prayer_queue.enqueue(request)
        else:
            # Regular chat — log for consolidation context
            _log_activity(f'CHAT: {player_name}: "{message}"')

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
    secs_since = kind_god.memory.seconds_since_consolidation()
    return {
        "event_buffer_size": len(event_buffer._events),
        "divine_queue_size": prayer_queue.size,
        "command_queue_size": len(command_queue),
        "kind_god_action_count": kind_god.action_count,
        "consolidation_log_entries": len(_consolidation_log),
        "kind_god_memory_count": len(kind_god.memory.memories),
        "last_consolidation_ago": f"{secs_since:.0f}s" if secs_since != float("inf") else "never",
        "next_consolidation_in": f"{max(0, MEMORY_CONSOLIDATION_INTERVAL_SECONDS - secs_since):.0f}s",
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
    timer tick — shared state (command_queue, action counts) is not safe for
    concurrent access.
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
            logger.exception(
                f"Divine request processing failed for {request.player}'s "
                f"{request.request_type}: \"{request.message[:60]}\""
            )
            # Requeue so the player isn't silently ignored
            try:
                if not prayer_queue.requeue(request):
                    command_queue.extend(_prayer_abandoned_commands(request.player))
            except Exception:
                logger.exception("Failed to requeue after processing error")
                command_queue.extend(_prayer_abandoned_commands(request.player))


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
            if not prayer_queue.requeue(request):
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
                if not prayer_queue.requeue(request):
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
                if not prayer_queue.requeue(request):
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
        # Activity log — record god response to prayer/herald
        god_label = {"kind": "Kind God", "deep": "Deep God", "herald": "Herald"}[acting_god]
        _log_activity(f"{god_label} answered {request.player}'s {rt}: {_summarize_commands(commands)}")
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
            _log_activity(f"Herald also spoke about {request.player}'s prayer: {_summarize_commands(herald_commands)}")


async def _maybe_consolidate():
    """Run memory consolidation if activity has accumulated and enough wall-clock time has passed.

    Fires immediately if _force_consolidation is set (triggered by "remember" keyword).
    Runs even when no players are online — the Kind God reflects on
    accumulated activity regardless of current server state.
    """
    global _consolidation_cooldown_until, _force_consolidation

    if not _consolidation_log:
        _force_consolidation = False
        return  # nothing to reflect on

    now = time.time()

    if _force_consolidation:
        _force_consolidation = False
        logger.info("[remember] Forced consolidation triggered by player")
    else:
        if now < _consolidation_cooldown_until:
            return  # still in cooldown after a failure
        if kind_god.memory.seconds_since_consolidation() < MEMORY_CONSOLIDATION_INTERVAL_SECONDS:
            return  # not time yet

    snapshot_len = len(_consolidation_log)
    logger.info(f"=== KIND GOD MEMORY CONSOLIDATION ({snapshot_len} entries) ===")
    activity_snapshot = _consolidation_log.copy()
    try:
        await kind_god.memory.consolidate(activity_snapshot)
        # Remove only the entries we snapshot — new entries appended during
        # the LLM call are preserved for the next consolidation
        del _consolidation_log[:snapshot_len]
        _save_consolidation_log()
        _recent_logs.append({"time": time.strftime("%H:%M:%S"),
                             "action": "consolidation_complete",
                             "entries_processed": len(activity_snapshot),
                             "memories": len(kind_god.memory.memories)})
    except Exception as exc:
        logger.exception("Memory consolidation failed")
        # Cooldown: wait one full interval before retrying
        _consolidation_cooldown_until = now + MEMORY_CONSOLIDATION_INTERVAL_SECONDS
        _save_consolidation_log()
        _recent_logs.append({"time": time.strftime("%H:%M:%S"),
                             "action": "consolidation_error",
                             "error": f"{type(exc).__name__}: {exc}"})
        # Keep the log — will retry after cooldown


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
    separately by the prayer queue.  Skips LLM god calls when no players
    are online to avoid wasting API calls on weather-only ticks;
    buffered events are drained and discarded to prevent buildup.
    Memory consolidation runs on a wall-clock timer regardless of
    player presence — the Kind God reflects even when alone.
    """
    global command_queue

    # Memory consolidation — wall-clock timer, runs even with no players
    await _maybe_consolidate()

    player_status = event_buffer.get_player_status()
    if not player_status or not player_status.get("players"):
        # No one online — drain events so they don't pile up
        discarded = event_buffer.drain_and_summarize(
            death_memorial=death_memorial, filter_divine=True)
        if discarded:
            tick_ts = time.strftime("%H:%M:%S")
            logger.info(f"[tick] Skipped — no players online (discarded {len(discarded)} chars of events)")
            _recent_logs.append({"time": tick_ts, "action": "tick_idle_skip",
                                 "reason": "no_players_online",
                                 "discarded_chars": len(discarded)})
        return

    event_summary = event_buffer.drain_and_summarize(
        death_memorial=death_memorial, filter_divine=True)
    if not event_summary:
        return

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
        # Activity log — spontaneous god action
        god_label = "Kind God" if acting_god == "kind" else "Deep God"
        _log_activity(f"{god_label} acted spontaneously: {_summarize_commands(commands)}")
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
            _log_activity(f"Herald spoke spontaneously: {_summarize_commands(herald_commands)}")
