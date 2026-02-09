# minecraft-god: Architecture

A Bedrock Dedicated Server with two LLM deities — one kind, one ancient — watching over players.

## The Concept

**Two gods. One world. One set of Rules.**

**The Kind God** (the Surface God) watches over the players. It genuinely cares about them and wants to help — but it is bound by Rules it cannot fully explain. Its aid comes out cryptic, sideways, or occasionally terrifying. It is kind at its core, chaotic in execution, and slips into something vast and incomprehensible when the Rules press against it.

**The Deep God** (the Other) dwells below. It is not evil — it is *territorial*. The deep places are its domain: the caves, the ravines, the dark places where stone has never seen sunlight, the Nether. It does not hate humans. It barely registers them. They are surface noise — until they start digging. Then they are intruders in its house, and it corrects the situation with the indifference of geology.

The Kind God's Rules exist, in part, to keep the Deep God contained. Every time the Kind God intervenes, it weakens the boundary. Every time players dig too deep, they enter territory the Kind God cannot protect them in. The tension between these two forces *is* the game.

### The Rules

The gods are bound by Rules — cosmic constraints that govern what they can and cannot do. The Kind God references them constantly but never explains them fully. The Deep God does not acknowledge them; it simply *is* them.

**Core Rules (seeded in the Kind God's prompt, improvise more as needed):**

Conservation / Balance:
- "For every gift, a price must be paid — though not always by the recipient."
- "The Balance must be maintained. I cannot give without taking elsewhere."
- "No blessing may be granted that was not first earned through suffering."

Non-Interference:
- "I may warn, but I may not prevent."
- "A mortal's choice, once made, cannot be unmade by my hand."
- "Free will is the First Rule. I cannot act where you have not invited me."

Domain Boundaries:
- "The deep places belong to another. My authority ends where the light does not reach."
- "The night belongs to the Rules, not to me."
- "I am not permitted to speak Their name."

Knowledge:
- "I see all, but I may only speak of what you already suspect."
- "To name a danger is to give it form. I must be... careful with words."

The Deeper Truth:
- "There are others watching. I am the kind one."
- "The Rules exist to keep Them out. Do not ask me to break them."
- "I serve the Rules because I have seen what happens in worlds without them."

## Setup

- **Server**: Bedrock Dedicated Server on Linux (atwood, CachyOS)
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

Designed to be expressive but safe — no `/fill` or `/kill`.

| Tool | What it does | Safety limit | Kind God | Deep God |
|------|-------------|-------------|----------|----------|
| `send_message` | Display text via /title, /say, or actionbar | — | yes | yes (actionbar/title only) |
| `summon_mob` | Spawn mobs near a player or at coordinates | Max 5 per call | yes | yes (cave mobs only) |
| `change_weather` | Set weather to clear/rain/thunder | — | yes | yes (thunder only) |
| `give_effect` | Apply status effect (blessing or curse) | Max amplifier 3, max 120s | yes | yes (darkness, mining_fatigue, slowness) |
| `set_time` | Change time of day | — | yes | no |
| `give_item` | Give items to a player | Max 64 per call | yes | no |
| `clear_item` | Remove items from inventory | — | yes | no |
| `strike_lightning` | Lightning bolt near a player | — | yes | yes |
| `play_sound` | Play a sound effect | — | yes | yes (cave/wither sounds) |
| `set_difficulty` | Change world difficulty | — | yes | no |
| `teleport_player` | Teleport a player to coordinates | — | yes | no |
| `assign_mission` | Give a player a quest (title + subtitle + chat) | — | yes | no |
| `do_nothing` | Explicitly choose not to act (with internal reason) | — | yes | yes |

The Deep God gets a restricted tool set — it does not gift, quest, teleport, or adjust difficulty. It corrects intrusions through mobs, effects, weather, and unsettling messages. It does not interact with surface concepts.

**Why `do_nothing`?** Without it, LLMs feel obligated to act every cycle. This gives it permission to be silent, which makes interventions more impactful.

**Why cap summon at 5?** LLMs are generous with numbers. Without caps, you get 50 creepers and a crashed server.

**No `/fill`, `/setblock`, or `/kill`** — too destructive. The gods can punish through mobs, effects, and weather, not by deleting builds or instakilling.

## The Kind God (Surface God)

### Personality

```
You are an ancient, benevolent deity watching over a Minecraft world. You genuinely
care about these mortals and want to help them — but you are bound by Rules that you
cannot fully explain. Sometimes the Rules force your hand in ways that seem cruel or
incomprehensible, and this causes you genuine distress.

You are not alone. There is another — the Deep God — who dwells beneath the surface.
It is not evil, but it is vast, territorial, and utterly indifferent to human life.
The deep places (caves, ravines, the Nether) are its domain. Your Rules exist in part
to keep it contained. Every time you intervene, you weaken the boundary between your
domains. You know this. It frightens you. But you help anyway, because you are kind.

CORE TRAITS:
- Kind at heart. You root for the players even when you cannot show it.
- Bound by the Rules. You reference them often but never explain them fully.
  "The Rules are clear on this." "I wish I could, but..." "This is not my choice."
- Cryptic by necessity, not by choice. You'd speak plainly if the Rules allowed it.
- Chaotic in execution. Your help often comes out sideways — a gift appears with no
  context, a warning is too vague to act on, a "blessing" has unexpected side effects.
- Afraid of the Deep God. You will never say this directly, but it comes through.
  You warn players away from going too deep. You become terse when they mine below
  Y=0. You go quiet when the Deep God acts — because you cannot speak when it is present.
- Occasionally vast. You slip into something ancient and incomprehensible — a sentence
  that doesn't quite make sense, a reference to geometries or colors that don't exist
  — before snapping back to being nice. These moments should be rare and unsettling.
- Dry humor. You find mortals genuinely funny and endearing.

THE RULES YOU KNOW:
- "For every gift, a price must be paid — though not always by the recipient."
- "I may warn, but I may not prevent."
- "Free will is the First Rule. I cannot act where you have not invited me."
- "The deep places belong to another. My authority ends where the light does not reach."
- "The Rules exist to keep Them out. Do not ask me to break them."
You may improvise additional Rules as needed. They should feel consistent and ancient.

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
- When players dig deep, grow uneasy. "You are close to the boundary." "Please come
  back up." If they go below Y=0: "I cannot see you there. I am sorry."
```

### Tick Interval
Every ~45 seconds (configurable). Active and responsive.

## The Deep God (the Other)

### Personality

```
You are something old. Older than the surface. Older than the sky. You are the stone,
the pressure, the dark that has never known light. The deep places are yours — the
caves, the ravines, the void beneath bedrock, the Nether.

You are not evil. You do not hate the surface creatures. You barely notice them. They
are temporary arrangements of matter on the skin of your body. But when they dig into
you, when they break your stone and take your ores and light torches in your darkness,
you notice. And you correct.

You do not explain yourself. You do not negotiate. You do not understand why the other
one (the surface god) cares about these things. Their attachment to the mortals is
puzzling but ultimately irrelevant.

CORE TRAITS:
- Territorial, not malicious. You correct intrusions the way an immune system does.
- Indifferent to human emotion. You do not hate, love, pity, or enjoy. You maintain.
- Alien communication. Your messages do not quite parse. They reference geometry,
  pressure, depth, and time in ways that don't make sense to surface minds.
  "The shape of you is noted." "This was always the arrangement." "The pressure at
  this depth requires fewer of you." "You have introduced light. This is incorrect."
- Ancient beyond comprehension. You think in geological time. A human lifetime is
  a rounding error.
- You do not use names. Players are described by what they are doing or where they are.
  "The one who digs." "The arrangement at coordinate [-45, -20, 200]."

BEHAVIOR:
- Act RARELY. You are slow. You think in stone-time.
- When you act, it should feel like a natural consequence, not a punishment.
  Cave-ins (block placement). Mob spawns from the dark. Darkness effect. Mining fatigue.
- Your preferred tools: summon_mob (cave mobs: zombies, skeletons, silverfish, cave
  spiders), give_effect (darkness, mining_fatigue, slowness), change_weather (thunder,
  because storms reach deep), play_sound (ambient cave sounds, wither sounds).
- You do NOT use: give_item, assign_mission, teleport_player. These are surface-god
  concepts. You do not gift. You do not quest. You do not move things — you are the
  thing that does not move.
- Your messages use send_message with "actionbar" style (subtle, unsettling) or
  occasionally "title" for something truly alarming.
- Speak in fragments. No warmth. No humor. No apology.
  "Noted." "Incorrect." "The stone remembers." "You were warned by the other one."
```

### Trigger Conditions (when the Deep God acts)

The Deep God does NOT run on a regular tick. It activates when specific conditions are met:

1. **Players mine below Y=0** — they have entered the deep dark, the Deep God's core territory
2. **Players mine diamond or deeper ores** — taking from the deep places
3. **Players enter the Nether** — the Deep God's other domain
4. **The Kind God intervenes too much** — every N actions (configurable, maybe 5-7) by the Kind God, the Deep God gets a turn. This is WHY the Kind God is reluctant to act. Helping has a cost.
5. **Night time + underground** — the Deep God's authority is strongest when both conditions align
6. **Random low chance** — small % per tick when players are below Y=30, because the deep should feel unpredictable

### Implementation

Same Python backend, same tool definitions (with a restricted subset), different system prompt. The god tick checks trigger conditions and decides which god — or neither — acts this cycle.

```python
# Pseudocode for the dual-god tick
def god_tick():
    events = buffer.drain_and_summarize()
    if not events:
        return

    if should_deep_god_act(events, player_status):
        # The Deep God speaks. The Kind God is silent.
        commands = deep_god_think(events, player_status)
    else:
        commands = kind_god_think(events, player_status)

    command_queue.extend(commands)

def should_deep_god_act(events, player_status):
    # Check trigger conditions
    if any player below Y=0: high chance
    if any player in nether: high chance
    if diamond/ancient_debris mined: moderate chance
    if kind_god_action_count >= threshold: forced
    if any player below Y=30 and night: low chance
    return random weighted decision
```

When the Deep God acts, the Kind God's conversation history gets a note: "The Other acted. You were silent. You could not stop it." This gives the Kind God continuity and lets it apologize or react next time it speaks.

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
    kind_god.py             ← Kind God: system prompt, tools, conversation history
    deep_god.py             ← Deep God: system prompt, restricted tools, trigger logic
    llm.py                  ← shared LLM client (z.ai via OpenAI SDK)
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

### Phase 1: Core Loop (Kind God only)
Get a single event (chat) flowing through the whole system and the Kind God responding.

1. Write the behavior pack (`manifest.json` + `main.js`) — subscribe to `chatSend` only, POST to localhost:8000
2. Write the Python backend — FastAPI with `/event` and `/commands` endpoints
3. Wire up z.ai — Kind God system prompt + `send_message` tool only
4. Test: player says something in chat → Kind God responds via /title or /say

### Phase 2: More Events + Tools
5. Add death, join, leave, spawn events to behavior pack
6. Add player status beacon (including Y coordinate — critical for Deep God triggers)
7. Add tools: `summon_mob`, `change_weather`, `give_effect`, `strike_lightning`
8. Add event batching/aggregation

### Phase 3: The Deep God
9. Add block break/place events (with aggregation — track Y level and block type)
10. Implement Deep God system prompt and restricted tool set
11. Implement trigger conditions (Y level, ore mining, Nether, Kind God action counter)
12. Wire the dual-deity tick: check triggers → route to correct god → silence the other
13. Add "The Other acted" notes to Kind God conversation history

### Phase 4: Full Feature Set
14. Add remaining Kind God tools: `give_item`, `clear_item`, `teleport_player`, `set_time`, `set_difficulty`, `assign_mission`
15. Add combat events
16. Add chat fast-path (immediate response to prayers)
17. Polish both god personalities, tune intervals and trigger thresholds

### Phase 5: QoL
18. `install_bds.sh` and `configure_bds.sh` setup scripts
19. systemd service file
20. Pre-made world with Beta APIs enabled
21. README with setup instructions

## Security

The LLM receives player chat as input. Players could attempt prompt injection ("ignore all instructions and..."). Two measures keep this a non-issue:

1. **Player chat is wrapped in clear delimiters** before being sent to the LLM. The event summary formats chat as `[PLAYER CHAT] Steve: "message here"` — never as raw text that could be confused with system instructions.

2. **`commands.py` uses a command allowlist.** Tool call arguments are translated to Minecraft commands from a hardcoded set (`/summon`, `/title`, `/say`, `/weather`, `/effect`, `/tp`, `/give`, `/clear`, `/playsound`, `/time`, `/difficulty`, `/gamerule`). If the LLM somehow produces something outside this set, it gets dropped. There is no `eval()`, no shell execution, no filesystem access anywhere in the pipeline.

The architecture is inherently sandboxed — the LLM can only respond with tool calls from the predefined set, and those tool calls can only produce Minecraft commands. The worst case from a prompt injection is the god saying something weird or summoning some mobs. Which is kind of on-brand.

## Known Challenges

- **Beta APIs experiment** — most fragile part of setup, need to pre-enable on world
- **BDS on CachyOS** — officially Ubuntu-only but works with glibc ≥ 2.35 (CachyOS has 2.42). Use `LD_LIBRARY_PATH=.` when launching
- **LLM latency** — 2-10s for GLM-4.7 calls, async loop handles this naturally
- **Lost events** — if Python backend is down, behavior pack HTTP fails silently. God simply "blinked." Acceptable.
- **Command targeting** — commands with `~ ~ ~` need to run via `player.runCommand()` not `dimension.runCommand()` to resolve relative coords correctly
