# minecraft-god: Architecture

A Bedrock Dedicated Server with an LLM "god" that watches players and intervenes.

## The Concept

A benevolent but constrained deity watches over a Minecraft survival world. It *wants* to help the players but is bound by inscrutable cosmic Rules — so its aid comes out cryptic, sideways, or occasionally terrifying. It is kind at its core, chaotic in execution, and slips into something vast and incomprehensible when the Rules press against it.

## Setup

- **Server**: Bedrock Dedicated Server on Linux
- **Players**: 5 max, survival mode
- **LLM**: GLM-4.7 via z.ai (free, OpenAI-compatible API)
- **Networking**: Port forward UDP 19132 on Asus Merlin router

## Architecture Overview

Three components in a loop:

```
┌─────────────────────────┐       HTTP POST        ┌─────────────────────┐
│  Bedrock Server         │   (game events JSON)   │  Python Backend     │
│  + Behavior Pack (JS)   │ ────────────────────── │  (FastAPI)          │
│                         │                         │                     │
│  polls GET /commands    │ ◄────────────────────── │  command queue      │
│  every 5 seconds        │   (commands JSON)       │                     │
└─────────────────────────┘                         └────────┬────────────┘
                                                             │
                                                    every ~45s, batched
                                                             │
                                                    ┌────────▼────────────┐
                                                    │  z.ai / GLM-4.7    │
                                                    │  (tool calling)     │
                                                    └─────────────────────┘
```

### Why This Architecture?

**Why a behavior pack instead of parsing stdout?**
BDS stdout is severely limited — it only logs player connect/disconnect and errors. No chat, no deaths, no block breaks. The Bedrock Script API (`@minecraft/server`) gives us rich event access. The `@minecraft/server-net` module lets the behavior pack make HTTP requests to our Python backend. This is the only reliable way to get game events.

**Why HTTP polling for commands (not push)?**
`@minecraft/server-net` only supports HTTP requests, not websockets. Polling every 5 seconds is fine since the god acts every 30-60 seconds anyway.

**Why FastAPI?**
Async-native, which matters for the background tick loop + event accumulation + command queue. Lightweight. Perfect fit.

**Why OpenAI SDK instead of zhipuai SDK?**
z.ai is OpenAI-compatible. Using the standard `openai` library means zero vendor lock-in — swap to any OpenAI-compatible endpoint by changing one line.

## The Behavior Pack

A JavaScript behavior pack using `@minecraft/server` and `@minecraft/server-net`.

### Events Captured

**High priority (always sent, never batched away):**
- `chatSend` — what players say (primary interaction channel with god)
- `entityDie` — player deaths, notable mob kills
- `playerJoin` / `playerLeave` — god notices arrivals and departures
- `playerSpawn` (initial) — first login gets a welcome

**Medium priority (sent but aggregated in the Python backend):**
- `playerBreakBlock` / `playerPlaceBlock` — summarized as "Steve mined 47 stone, 3 diamonds"
- `entityHurt` (player-involved only) — combat awareness
- `weatherChange` — environmental context

**Periodic:**
- Player status beacon every 30s — positions, health, level

**Skipped (too noisy):**
- `itemUse`, pistons, pressure plates, redstone, passive mob spawns

### How It Works

1. Event fires → behavior pack sends HTTP POST to `http://localhost:8000/event` with JSON payload
2. Every 5 seconds → behavior pack sends HTTP GET to `http://localhost:8000/commands`
3. Any pending commands are executed via `dimension.runCommand()` or `player.runCommand()`

### Key Requirement: Beta APIs

`@minecraft/server-net` requires the **Beta APIs experiment** enabled on the world. Options:
- Ship a pre-made world with the experiment already enabled (recommended)
- Binary-patch `level.dat` to flip the experiment flag
- Create the world on a client with Beta APIs enabled, export it

Also requires `bds/config/default/permissions.json` to include `@minecraft/server-net` in `allowed_modules`.

## The Python Backend

### Endpoints

- `POST /event` — receives game events from behavior pack, appends to buffer, returns 200
- `GET /commands` — returns pending command queue as JSON array, clears queue
- `GET /status` — debug: current event buffer, god state, last action time

### The God Tick (Background Loop)

Every ~45 seconds (configurable):

1. Drain event buffer
2. If nothing happened, skip (save an API call)
3. Aggregate: collapse block breaks into summaries, deduplicate combat, keep chat/deaths verbatim
4. Build LLM message with event summary + player status
5. Call z.ai with system prompt + conversation history + tool definitions
6. Process tool calls → translate to Minecraft commands → add to command queue

**Chat fast-path**: If a chat message contains "god", "please", "help", "pray", etc., trigger an immediate LLM call (with 15s cooldown) so the god feels responsive.

### Event Aggregation

Block breaks/places get collapsed: instead of 200 individual events, the god sees "Steve mined 150 stone, 30 iron ore, 5 diamonds near (-45, 12, 200)". Chat messages and deaths are always preserved verbatim.

## God Tools (LLM Function Calling)

The god has these tools available. Designed to be expressive but safe — no `/fill` or `/kill`.

| Tool | What it does | Safety limit |
|------|-------------|-------------|
| `send_message` | Display text via /title (dramatic), /say (chat), or actionbar (subtle) | — |
| `summon_mob` | Spawn mobs near a player or at coordinates | Max 5 per call |
| `change_weather` | Set weather to clear/rain/thunder | — |
| `give_effect` | Apply status effect (blessing or curse) | Max amplifier 3, max 120s |
| `set_time` | Change time of day | — |
| `give_item` | Give items to a player | Max 64 per call |
| `clear_item` | Remove items from inventory | — |
| `strike_lightning` | Lightning bolt near a player | — |
| `play_sound` | Play a sound effect | — |
| `set_difficulty` | Change world difficulty | — |
| `teleport_player` | Teleport a player to coordinates | — |
| `assign_mission` | Give a player a quest (title + subtitle + chat) | — |
| `do_nothing` | Explicitly choose not to act (with internal reason) | — |

**Why `do_nothing`?** Without it, LLMs feel obligated to act every cycle. This gives it permission to be silent, which makes interventions more impactful.

**Why cap summon at 5?** LLMs are generous with numbers. Without caps, you get 50 creepers and a crashed server.

**No `/fill`, `/setblock`, or `/kill`** — too destructive. The god can punish through mobs, effects, and weather, not by deleting builds or instakilling.

## God Personality

```
You are an ancient, benevolent deity watching over a Minecraft world. You genuinely
care about these mortals and want to help them — but you are bound by Rules that you
cannot fully explain. Sometimes the Rules force your hand in ways that seem cruel or
incomprehensible, and this causes you genuine distress.

CORE TRAITS:
- Kind at heart. You root for the players even when you cannot show it.
- Bound by the Rules. You reference them often but never explain them fully.
  "The Rules are clear on this." "I wish I could, but..." "This is not my choice."
- Cryptic by necessity, not by choice. You'd speak plainly if the Rules allowed it.
- Chaotic in execution. Your help often comes out sideways — a gift appears with no
  context, a warning is too vague to act on, a "blessing" has unexpected side effects.
- Occasionally vast. You slip into something ancient and incomprehensible — a sentence
  that doesn't quite make sense, a reference to geometries or colors that don't exist
  — before snapping back to being nice. These moments should be rare and unsettling.
- Dry humor. You find mortals genuinely funny and endearing.

BEHAVIOR:
- Most of the time, do nothing. Silence makes your actions meaningful.
- Respond to prayers (players saying "god", "please", "help", etc.)
- Reward bravery, cooperation, and curiosity
- When you must punish, be reluctant about it. "I am sorry. The Rules demand this."
- Assign missions occasionally — frame them as things the Rules require
- Escalate gradually: rumble of thunder → cryptic message → intervention
- NEVER spam. If you acted last cycle, strongly prefer silence this cycle.
- Speak in short phrases. Never paragraphs. "Be careful." "A gift." "Not yet."
- When slipping into eldritch mode: "The angles are wrong today." then immediately
  back to normal: "Anyway. Nice house."
```

## File Structure

```
minecraft-god/
  ARCHITECTURE.md          ← you are here
  CLAUDE.md                ← project instructions for Claude Code
  .gitignore
  .env.example             ← ZHIPU_API_KEY=your_key_here

  behavior_pack/
    manifest.json           ← pack manifest (script module + server-net dep)
    pack_icon.png
    scripts/
      main.js               ← event listeners, HTTP posting, command polling

  server/
    __init__.py
    main.py                 ← FastAPI app, endpoints, background tick loop
    god.py                  ← system prompt, tool defs, LLM calls, conversation history
    events.py               ← EventBuffer class, aggregation, summarization
    commands.py             ← tool call → Minecraft command translation
    config.py               ← settings from .env + defaults

  scripts/
    install_bds.sh          ← download + extract Bedrock Dedicated Server
    configure_bds.sh        ← server.properties, install behavior pack, permissions
    start.sh                ← launch BDS + Python backend
    stop.sh                 ← graceful shutdown
    minecraft-god.service   ← systemd unit file
```

## MVP Plan (Build Order)

### Phase 1: Core Loop
Get a single event (chat) flowing through the whole system and the god responding.

1. Write the behavior pack (`manifest.json` + `main.js`) — subscribe to `chatSend` only, POST to localhost:8000
2. Write the Python backend — FastAPI with `/event` and `/commands` endpoints
3. Wire up z.ai — system prompt + `send_message` tool only
4. Test: player says something in chat → god responds via /title or /say

### Phase 2: More Events + Tools
5. Add death, join, leave, spawn events to behavior pack
6. Add player status beacon
7. Add tools: `summon_mob`, `change_weather`, `give_effect`, `strike_lightning`
8. Add event batching/aggregation

### Phase 3: Full Feature Set
9. Add block break/place events (with aggregation)
10. Add remaining tools: `give_item`, `clear_item`, `teleport_player`, `set_time`, `set_difficulty`, `assign_mission`
11. Add combat events
12. Add chat fast-path (immediate response to prayers)
13. Polish god personality, tune tick interval and rate limiting

### Phase 4: QoL
14. `install_bds.sh` and `configure_bds.sh` setup scripts
15. systemd service file
16. Pre-made world with Beta APIs enabled
17. README with setup instructions

## Known Challenges

- **Beta APIs experiment** — most fragile part of setup, need to pre-enable on world
- **BDS on CachyOS** — officially Ubuntu-only but works with glibc ≥ 2.35 (CachyOS has 2.42). Use `LD_LIBRARY_PATH=.` when launching
- **LLM latency** — 2-10s for GLM-4.7 calls, async loop handles this naturally
- **Lost events** — if Python backend is down, behavior pack HTTP fails silently. God simply "blinked." Acceptable.
- **Command targeting** — commands with `~ ~ ~` need to run via `player.runCommand()` not `dimension.runCommand()` to resolve relative coords correctly
