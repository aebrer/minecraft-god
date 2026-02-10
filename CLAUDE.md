# CLAUDE.md — minecraft-god

## What This Is
Paper MC server (Java Edition) + two LLM deities watching over players. See ARCHITECTURE.md for full design.

- **Kind God**: benevolent, bound by Rules, cryptic, afraid of the deep
- **Deep God**: territorial, ancient, indifferent to humans, dwells in caves/Nether

## Tech Stack
- **Plugin**: Java Paper plugin (`plugin/`) — event listeners, HTTP bridge to backend
- **Backend**: Python 3.11+, FastAPI, uvicorn
- **LLM**: GLM-4.7 via z.ai (OpenAI-compatible, use `openai` SDK with custom base_url)
- **Server**: Paper MC 1.21.11 (Java Edition)

## Key Architecture
- Paper plugin POSTs events to `http://localhost:8000/event`
- Paper plugin polls `GET http://localhost:8000/commands` every 5 seconds
- Python backend batches events, checks Deep God triggers, routes to correct god
- LLM responds with tool calls → translated to Minecraft commands via allowlist
- Commands executed via `Bukkit.dispatchCommand()` with console sender; relative coords resolved for targeted players

## File Layout
```
server/
  config.py       - settings from .env
  llm.py          - shared OpenAI client pointed at z.ai
  events.py       - EventBuffer: accumulation + summarization
  commands.py     - tool call → Minecraft command (with allowlist, Java Edition syntax)
  kind_god.py     - Kind God: prompt, tools, conversation history
  deep_god.py     - Deep God: prompt, restricted tools, trigger logic
  main.py         - FastAPI app, endpoints, dual-deity tick loop

plugin/
  pom.xml                                    - Maven build file
  src/main/java/.../MinecraftGodPlugin.java  - event listeners, HTTP client, command polling
  src/main/resources/plugin.yml              - plugin descriptor

paper/                    - Paper server runtime (not tracked in git)
  paper-1.21.11-69.jar    - Paper server jar
  plugins/                - built plugin goes here
  server.properties       - server config

scripts/
  start.sh          - launch both Paper + backend
  stop.sh           - graceful shutdown
```

## Running
```bash
# Via systemd (recommended):
systemctl --user start minecraft-god-backend
systemctl --user start minecraft-god-paper

# Or manually:
source venv/bin/activate
uvicorn server.main:app --host 127.0.0.1 --port 8000  # terminal 1
cd paper && java -Xms1G -Xmx2G -jar paper-1.21.11-69.jar --nogui  # terminal 2

# Or via script:
./scripts/start.sh

# Debug
curl http://localhost:8000/status

# RCON (requires mcrcon):
mcrcon -H localhost -p <password> "whitelist list"
```

## Building the Plugin
```bash
cd plugin && mvn package
cp target/minecraft-god-plugin.jar ../paper/plugins/
```

## Setup from scratch
```bash
# Download Paper MC jar to paper/
python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt
cp .env.example .env          # add ZHIPU_API_KEY
echo "eula=true" > paper/eula.txt
cd plugin && mvn package && cp target/minecraft-god-plugin.jar ../paper/plugins/
# Configure paper/server.properties (online-mode, whitelist, RCON, etc.)
# Start services
```

## HARD RULES
- **NEVER use sudo.** All operations must run as the current user. No exceptions.

## Important Notes
- The `paper/` directory runtime files are not tracked in git (jar, world data, configs)
- The `plugin/target/` build artifacts are not tracked in git
- Never commit `.env` (contains API key)
- Player chat is wrapped in `[PLAYER CHAT]` delimiters before reaching the LLM (prompt injection mitigation)
- `commands.py` enforces a command allowlist — only whitelisted Minecraft commands can be executed
- Commands use Java Edition syntax: `minecraft:` namespaces, `effect give`, JSON text components
- RCON is enabled on port 25575 for remote console access
- Whitelist is enforced — add players with `whitelist add <username>` via RCON or console

## Migration from Bedrock
This project was originally built on Bedrock Dedicated Server with a JavaScript behavior pack.
Ported to Paper MC (Java Edition) on 2026-02-09. The `behavior_pack/` directory contains
the legacy Bedrock JS code. The `bds/` directory (not tracked) contained the old BDS installation.
