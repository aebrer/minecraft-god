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
  schematics.py   - schematic catalog: search/build for divine construction
  kind_god.py     - Kind God: prompt, tools, fresh context per call, multi-turn tool use
  deep_god.py     - Deep God: prompt, restricted tools, trigger logic
  herald_god.py   - Herald: poetic messenger in iambic pentameter
  memory.py       - Kind God persistent memory (consolidation across sessions)
  deaths.py       - DeathMemorial: persistent death records
  main.py         - FastAPI app, endpoints, dual-deity tick loop

plugin/
  pom.xml                                    - Maven build file
  src/main/java/.../MinecraftGodPlugin.java  - event listeners, HTTP client, command polling, schematic placer
  src/main/resources/plugin.yml              - plugin descriptor

paper/                    - Paper server runtime (not tracked in git)
  paper-1.21.11-69.jar    - Paper server jar
  plugins/                - built plugin goes here
  server.properties       - server config (spawn-protection=0)

backups/                  - rolling world backups (not tracked in git)

scripts/
  start.sh          - launch both Paper + backend
  stop.sh           - graceful shutdown
  backup_world.sh   - world backup (stops Paper, tars world, restarts, prunes old)
  schematics/       - GrabCraft → .schem data pipeline
    scrape_grabcraft.py  - scraper + converter + catalog generator
    blockmap_raw.csv     - block name mappings (GrabCraft → minecraft:id)
    announce_restart.py  - RCON-based countdown for server restarts
    data/                - raw blueprint cache (~500MB, gitignored)
    schematics/          - converted .schem files + catalog.json
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

# Debug / Admin endpoints:
curl http://localhost:8000/status          # god state, player positions, action counts
curl http://localhost:8000/logs            # recent 50 god decisions + commands (ring buffer)
curl -X POST http://localhost:8000/commands -H 'Content-Type: application/json' -d '[{"type":"build_schematic","blueprint_id":"medieval-blacksmith","x":10,"y":64,"z":10,"rotation":0}]'

# Logs via journalctl:
journalctl --user -u minecraft-god-backend --since "5 min ago" --no-pager   # backend logs
journalctl --user -u minecraft-god-paper --since "5 min ago" --no-pager     # Paper server logs

# Filter backend logs (cut noise):
journalctl --user -u minecraft-god-backend --since "5 min ago" --no-pager | grep -v -E "(Unsupported upgrade|No supported WebSocket|GET /commands|POST /event)"

# Filter for god decisions only:
journalctl --user -u minecraft-god-backend --since "10 min ago" --no-pager | grep -E "(Kind God|Deep God|Prayer|search|schematic|do_nothing|acted|Queued|Herald)"

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
- **This is a PUBLIC repository.** All commits, issues, PRs, and comments are visible to the world. Before any GitHub action (commit, push, issue creation, PR creation, commenting), double-check that the content does not contain:
  - Player gamertags or real names
  - Server domains, IP addresses, or machine hostnames
  - RCON passwords, API keys, or any credentials
  - Paths containing usernames (e.g. `/home/username/`)
  - Any other personally identifiable information
- **Always confirm with the human operator** before pushing commits, creating/editing issues, creating/editing PRs, or any other action visible on GitHub. The cost of pausing to ask is low; the cost of leaking private info to a public repo is high.

## Important Notes
- The `paper/` directory runtime files are not tracked in git (jar, world data, configs)
- The `plugin/target/` build artifacts are not tracked in git
- Never commit `.env` (contains API key)
- Player chat is wrapped in `[PLAYER CHAT]` delimiters before reaching the LLM (prompt injection mitigation)
- `commands.py` enforces a command allowlist — only whitelisted Minecraft commands can be executed
- Commands use Java Edition syntax: `minecraft:` namespaces, `effect give`, JSON text components
- RCON is enabled on port 25575 for remote console access
- Whitelist is enforced — add players with `whitelist add <username>` via RCON or console

## Schematic Building System (Issue #15)
- 2,139 blueprints across 30 categories scraped from GrabCraft
- Pipeline: `scripts/schematics/scrape_grabcraft.py` (index → fetch → convert → catalog)
- Sponge Schematic v2 (.schem) format, read by schematic4j in the Java plugin
- Kind God has multi-turn tool use (max 4 turns): search_schematics → build_schematic (+ nudge if re-searching, + error retry)
- Plugin places blocks progressively bottom-to-top with lightning, particles, and completion sound
- `scripts/schematics/data/` is gitignored (raw blueprint cache, ~500MB)
- To expand: `cd scripts/schematics && ../../../venv/bin/python3 scrape_grabcraft.py fetch --category <name> && ../../../venv/bin/python3 scrape_grabcraft.py convert && ../../../venv/bin/python3 scrape_grabcraft.py catalog`

## Deep God Trigger Logic
- Player-specific: when a prayer triggers a tick, only the praying player's position is checked
- Triggers: below Y=0 (70%), Nether (50%), deep ore mining while underground (40%), underground at night (15%), random while underground (5%)
- Forced trigger when Kind God action_count >= threshold (6)
- On regular (non-prayer) ticks, all player positions are checked

## Server Restart Procedure
1. Run `scripts/schematics/announce_restart.py` for RCON countdown (30s/15s/5s/3/2/1)
2. `systemctl --user restart minecraft-god-paper` (for Paper/plugin changes)
3. `systemctl --user restart minecraft-god-backend` (for backend Python changes)
- Backend restarts are safe without announcement (just a few seconds of god silence)
- Paper restarts kick all players — always announce first

## World Backups
- Automated via systemd timer: `minecraft-god-backup.timer` fires at **02:00** and **14:00** daily
- Script: `scripts/backup_world.sh` — stops Paper, tars `paper/world/`, restarts Paper, prunes old backups
- Stored in `backups/` as `god-world-YYYY-MM-DD-HHMM.tar.gz` (~40-50MB each)
- Rolling window: keeps last **6 backups** (3 days), oldest are pruned automatically
- Paper is stopped during backup (~5 seconds of downtime) for a consistent snapshot
- Check status: `systemctl --user list-timers | grep backup`
- Manual backup: `systemctl --user start minecraft-god-backup.service`
- Restore: stop Paper, extract backup into `paper/`, restart Paper

