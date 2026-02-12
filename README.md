# minecraft-god

A Paper MC server with two LLM deities watching over players. One kind, one ancient. Both unpredictable.

## The Concept

**Two gods. One world. One set of Rules.**

**The Kind God** watches over the surface. It genuinely cares about the players — but it is bound by Rules it cannot fully explain. Its aid comes out cryptic, sideways, or occasionally terrifying. It assigns quests, offers gifts, strikes lightning at dramatic moments, and slips into something vast and incomprehensible when the Rules press against it.

**The Deep God** dwells below. It is not evil — it is *territorial*. The caves, the ravines, the void beneath bedrock, the Nether — these are its domain. It does not hate humans. It barely registers them. But when they dig too deep, it corrects the situation with the indifference of geology.

The Kind God's Rules exist, in part, to keep the Deep God contained. Every divine intervention weakens the boundary. Every time players dig too deep, they enter territory the Kind God cannot protect them in. The tension between these two forces *is* the game.

## Architecture

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

The Paper plugin captures game events (chat, deaths, combat, block breaks, weather, player joins/leaves) and POSTs them to the Python backend. The backend batches events, routes them to the appropriate god based on trigger conditions, and the LLM responds with tool calls that get translated into Minecraft commands.

The gods can send messages (chat, title, actionbar), summon mobs, change weather, give/remove items, apply status effects, strike lightning, teleport players, assign quests, place blocks, and build structures from a library of schematics.

All commands go through an allowlist — the LLM can only produce Minecraft commands from a predefined safe set. The worst case from prompt injection is the god saying something weird or summoning some mobs, which is kind of on-brand.

## Tech Stack

- **Server**: Paper MC 1.21.11 (Java Edition)
- **Plugin**: Java, using Paper/Bukkit event API + `java.net.http.HttpClient`
- **Backend**: Python 3.11+, FastAPI, uvicorn
- **LLM**: Any OpenAI-compatible API (currently using GLM via z.ai)

## Setup

```bash
# Clone and set up Python environment
git clone https://github.com/aebrer/minecraft-god.git
cd minecraft-god
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env    # add your API key

# Download Paper MC jar to paper/
echo "eula=true" > paper/eula.txt
# Configure paper/server.properties (online-mode, whitelist, RCON, spawn-protection=0)

# Build the plugin
cd plugin && mvn package
cp target/minecraft-god-plugin.jar ../paper/plugins/

# Run
./scripts/start.sh
```

## Schematic Building System

The Kind God can browse, search, and build structures from a library of Minecraft schematics. The pipeline scrapes blueprints from public sources, converts them to Sponge Schematic v2 format (`.schem`), and generates a searchable catalog.

Schematics are not included in this repo (not redistributable). To populate your own:

```bash
cd scripts/schematics
../../venv/bin/python3 scrape_grabcraft.py fetch
../../venv/bin/python3 scrape_grabcraft.py convert
../../venv/bin/python3 scrape_grabcraft.py catalog
```

The plugin places blocks progressively bottom-to-top with lightning, particles, and a completion sound.

## Deep God Trigger Logic

The Deep God doesn't run on a regular tick. It activates when:

- Players mine below Y=0 (70% chance)
- Players enter the Nether (50%)
- Deep ore mining while underground (40%)
- Underground at night (15%)
- Random while underground (5%)
- Forced when the Kind God has acted too many times (every 6 interventions)

This is *why* the Kind God is reluctant to act — helping has a cost.

## License

MIT
