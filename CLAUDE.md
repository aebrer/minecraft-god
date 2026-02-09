# CLAUDE.md — minecraft-god

## What This Is
Bedrock Dedicated Server + two LLM deities watching over players. See ARCHITECTURE.md for full design.

- **Kind God**: benevolent, bound by Rules, cryptic, afraid of the deep
- **Deep God**: territorial, ancient, indifferent to humans, dwells in caves/Nether

## Tech Stack
- **Behavior Pack**: JavaScript using `@minecraft/server` + `@minecraft/server-net`
- **Backend**: Python 3.11+, FastAPI, uvicorn
- **LLM**: GLM-4.7 via z.ai (OpenAI-compatible, use `openai` SDK with custom base_url)
- **Server**: Bedrock Dedicated Server for Linux

## Key Architecture
- Behavior pack POSTs events to `http://localhost:8000/event`
- Behavior pack polls `GET http://localhost:8000/commands` every 5 seconds
- Python backend batches events, checks Deep God triggers, routes to correct god
- LLM responds with tool calls → translated to Minecraft commands via allowlist
- Commands with `target_player` run via `player.runCommand()`, others via `dimension.runCommand()`

## File Layout
```
server/
  config.py       - settings from .env
  llm.py          - shared OpenAI client pointed at z.ai
  events.py       - EventBuffer: accumulation + summarization
  commands.py     - tool call → Minecraft command (with allowlist)
  kind_god.py     - Kind God: prompt, tools, conversation history
  deep_god.py     - Deep God: prompt, restricted tools, trigger logic
  main.py         - FastAPI app, endpoints, dual-deity tick loop

behavior_pack/
  manifest.json   - pack manifest (script module + server-net)
  scripts/main.js - event listeners, HTTP posting, command polling

scripts/
  install_bds.sh    - download BDS
  configure_bds.sh  - server.properties, install pack, permissions
  start.sh          - launch both BDS + backend
  stop.sh           - graceful shutdown
  minecraft-god.service - systemd unit
```

## Running
```bash
# Quick start (after setup)
./scripts/start.sh

# Or manually:
source venv/bin/activate
uvicorn server.main:app --host 127.0.0.1 --port 8000  # terminal 1
cd bds && LD_LIBRARY_PATH=. ./bedrock_server          # terminal 2

# Debug
curl http://localhost:8000/status
```

## Setup from scratch
```bash
./scripts/install_bds.sh      # download BDS
python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt
cp .env.example .env          # add ZHIPU_API_KEY
./scripts/configure_bds.sh    # configure server + install behavior pack
# Enable Beta APIs experiment on the world (see ARCHITECTURE.md)
./scripts/start.sh
```

## Important Notes
- `@minecraft/server-net` requires Beta APIs experiment enabled on the world
- `bds/config/default/permissions.json` must include `@minecraft/server-net` in allowed_modules
- BDS stdout is very limited (only connect/disconnect) — the behavior pack is the real event source
- The `bds/` directory is not tracked in git (downloaded via install script)
- Never commit `.env` (contains API key)
- Player chat is wrapped in `[PLAYER CHAT]` delimiters before reaching the LLM (prompt injection mitigation)
- `commands.py` enforces a command allowlist — only whitelisted Minecraft commands can be executed
