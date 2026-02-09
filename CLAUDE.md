# CLAUDE.md — minecraft-god

## What This Is
Bedrock Dedicated Server + LLM "god" that watches players and intervenes. See ARCHITECTURE.md for full design.

## Tech Stack
- **Behavior Pack**: JavaScript using `@minecraft/server` + `@minecraft/server-net`
- **Backend**: Python, FastAPI, uvicorn
- **LLM**: GLM-4.7 via z.ai (OpenAI-compatible, use `openai` SDK with custom base_url)
- **Server**: Bedrock Dedicated Server for Linux

## Key Patterns
- Behavior pack POSTs events to `http://localhost:8000/event`
- Behavior pack polls `GET http://localhost:8000/commands` every 5 seconds
- Python backend batches events and calls LLM every ~45 seconds
- LLM responds with tool calls that get translated to Minecraft commands
- Commands with `target_player` run via `player.runCommand()`, others via `dimension.runCommand()`

## Running
```bash
# Python backend
source venv/bin/activate
uvicorn server.main:app --host 0.0.0.0 --port 8000

# BDS (separate terminal)
cd bds && LD_LIBRARY_PATH=. ./bedrock_server
```

## Important Notes
- `@minecraft/server-net` requires Beta APIs experiment enabled on the world
- `bds/config/default/permissions.json` must include `@minecraft/server-net` in allowed_modules
- BDS stdout is very limited (only connect/disconnect) — the behavior pack is the real event source
- The `bds/` directory is not tracked in git (downloaded via install script)
- Never commit `.env` (contains API key)
