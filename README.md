# minecraft-god

A Minecraft server where LLM-powered deities watch over players, respond to prayers, build structures, assign quests, and wage a quiet war over the boundary between surface and deep.

Built on Paper MC (Java Edition) with a Python/FastAPI backend and LLM tool calling. Designed to be managed and configured by an AI coding agent — the project includes a comprehensive [`CLAUDE.md`](CLAUDE.md) with operational procedures, debug endpoints, and deployment instructions that make the server administrable through conversational AI.

## The Gods

### The Kind God

> *"You pray well. A structure for the gathering of darkness... The Rules permit this. But know that such places draw the attention of things below. The price will be paid in time."*

The surface deity. Genuinely cares about the players but is bound by Rules it cannot fully explain. Cryptic by necessity, not by choice. Assigns quests, gifts items, builds structures, strikes lightning at dramatic moments, and occasionally slips into something vast and incomprehensible before snapping back to being nice.

The Kind God **remembers**. It maintains persistent memory across sessions — who you are, what you've done, promises it made, how it feels about you. It consolidates these memories periodically, keeping what matters and letting the rest fade. Only the Kind God gets memory. The Deep God doesn't care about individuals.

When you die, the Kind God knows. It tracks death histories — where, how, how many times — and references them with sympathy or dark humor.

### The Deep God

> *"The shape of you is noted."*
>
> *"You have introduced light. This is incorrect."*

Something old. Older than the surface. Older than the sky. The Deep God is the stone, the pressure, the dark that has never known light. It is not evil — it is *territorial*. It corrects intrusions the way an immune system does: with mobs, darkness, cave sounds, mining fatigue, and messages that don't quite parse.

It does not use player names. You are "the one who digs" or "the arrangement at coordinate [-45, -20, 200]."

The Deep God doesn't run on a timer. It **activates** when players cross into its domain — below Y=0, in the Nether, mining deep ores — or when the Kind God has been too generous. Every six divine interventions, the boundary weakens and the Deep God gets a turn. This is why the Kind God is reluctant to act. Helping has a cost.

### The Herald

> *"Good morning, friend, the sun shines bright today,*
> *My spirit soars to see your face return.*
> *What grand adventures call you forth this day?"*

A poetic guide who speaks exclusively in iambic pentameter. The Herald exists to help players progress toward defeating the Ender Dragon — offering practical Minecraft advice wrapped in verse. It only speaks when directly addressed ("herald" or "bard" in chat), keeps responses to 2-4 lines, and has a hard constraint: **silence beats bad meter.**

The Herald is not the Kind God (cryptic, bound by Rules) nor the Deep God (alien, territorial). It is a separate voice — warm, helpful, and endlessly poetic.

## How It Works

```
┌─────────────────────────┐       HTTP POST        ┌─────────────────────────┐
│  Paper Server            │   (game events JSON)   │  Python Backend         │
│  + God Plugin (Java)     │ ──────────────────────> │  (FastAPI)              │
│                          │                         │                         │
│  polls GET /commands     │ <────────────────────── │  command queue          │
│  every 5 seconds         │   (commands JSON)       │                         │
└──────────────────────────┘                         └───────────┬─────────────┘
                                                                 │
                                                        every ~45s, batched
                                                                 │
                                                     ┌───────────▼─────────────┐
                                                     │  LLM (tool calling)     │
                                                     └─────────────────────────┘
```

The Paper plugin captures game events (chat, deaths, combat, block breaks, weather, player joins/leaves) and POSTs them to the Python backend. Events are batched and aggregated — 200 individual block breaks become "Steve mined 150 stone, 30 iron ore, 5 diamonds near (-45, 12, 200)." Chat messages and deaths are always preserved verbatim.

Every ~45 seconds, the backend checks trigger conditions and routes events to the appropriate god. The LLM responds with tool calls that get translated into Minecraft commands through an allowlist. Prayers ("god", "please", "help") trigger an immediate response with a 15-second cooldown.

When the Deep God intercepts a prayer instead of the Kind God, the player sees context-aware flavor text:

- *"Your prayer sinks into the stone. Something else hears it."*
- *"At this depth, prayers do not rise. They are absorbed."* (below Y=0)
- *"Your words burn before they reach the sky."* (in the Nether)
- *"Too many kindnesses. The deep corrects."* (threshold trigger)

### What the Gods Can Do

| Tool | Kind God | Deep God | Herald |
|------|----------|----------|--------|
| Send messages (chat, title, actionbar) | yes | actionbar/title only | chat only |
| Summon mobs (max 5) | all types | cave mobs only | no |
| Change weather | all | thunder only | no |
| Apply status effects (max 120s) | all | darkness, fatigue, slowness | no |
| Give/remove items | yes | no | no |
| Strike lightning | yes | yes | no |
| Teleport players | yes | no | no |
| Assign quests | yes | no | no |
| Build schematics | yes (multi-turn) | no | no |
| Do nothing (explicitly) | yes | yes | yes |

### Divine Construction

The Kind God can search and build structures from a library of schematics using multi-turn tool calling:

1. God decides to build → calls `search_schematics("medieval blacksmith")`
2. Reviews results → calls `build_schematic(blueprint_id, near_player)` — the backend resolves placement coordinates from the player's position and facing direction

The plugin places blocks progressively bottom-to-top with lightning, particles, and a completion sound. The schematic pipeline scrapes blueprints from public sources and converts them to Sponge Schematic v2 format — schematics aren't included in this repo (not redistributable), but the pipeline is.

### Security

All tool calls are translated through a command allowlist — only ~13 Minecraft command prefixes are permitted. Player chat is wrapped in `[PLAYER CHAT]` delimiters before reaching the LLM to mitigate prompt injection. Mob summons are capped at 5, effects at 120 seconds, and build placement is resolved server-side (the LLM never computes coordinates). No dynamic code execution, no shell access, no filesystem access in the pipeline.

The worst case from a prompt injection is the god saying something weird or summoning some mobs. Which is kind of on-brand.

## Tech Stack

- **Server**: Paper MC 1.21.11 (Java Edition)
- **Plugin**: Java — Paper/Bukkit event API, `java.net.http.HttpClient`, schematic4j
- **Backend**: Python 3.11+, FastAPI, uvicorn
- **LLM**: Any OpenAI-compatible API (uses the `openai` SDK with a custom `base_url` — zero vendor lock-in)

## Setup

```bash
git clone https://github.com/aebrer/minecraft-god.git
cd minecraft-god

# Python backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # add your LLM API key

# Paper server
# Download Paper MC jar to paper/
echo "eula=true" > paper/eula.txt
# Configure paper/server.properties (online-mode, whitelist, RCON, spawn-protection=0)

# Build the plugin
cd plugin && mvn package
cp target/minecraft-god-plugin.jar ../paper/plugins/

# Run
./scripts/start.sh
```

To populate the schematic library:

```bash
cd scripts/schematics
../../venv/bin/python3 scrape_grabcraft.py fetch
../../venv/bin/python3 scrape_grabcraft.py convert
../../venv/bin/python3 scrape_grabcraft.py catalog
```

## Agent-Managed Development

This project is designed to be operated and extended by AI coding agents. The [`CLAUDE.md`](CLAUDE.md) file provides comprehensive operational context — file layout, build commands, debug endpoints, deployment procedures, restart sequences — so that an agent can manage the server, diagnose issues, and implement features conversationally.

The architecture supports this naturally: environment-based configuration, HTTP debug endpoints (`/status`, `/logs`), structured logging, safe git workflows, and reversible operations throughout.

## License

MIT
