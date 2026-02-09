#!/bin/bash
# Start the Python backend and Bedrock Dedicated Server
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BDS_DIR="$PROJECT_DIR/bds"
LOG_DIR="$PROJECT_DIR/logs"

mkdir -p "$LOG_DIR"

echo "=== Minecraft God: Starting ==="

# Check for .env
if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "ERROR: .env file not found. Copy .env.example to .env and set your API key."
    exit 1
fi

# Check for BDS
if [ ! -f "$BDS_DIR/bedrock_server" ]; then
    echo "ERROR: BDS not installed. Run install_bds.sh first."
    exit 1
fi

# Check for venv
if [ ! -d "$PROJECT_DIR/venv" ]; then
    echo "ERROR: Python venv not found. Create it with:"
    echo "  python3 -m venv venv && source venv/bin/activate && pip install fastapi uvicorn openai python-dotenv"
    exit 1
fi

# Start the Python backend
echo "Starting Python backend..."
source "$PROJECT_DIR/venv/bin/activate"
nohup uvicorn server.main:app --host 0.0.0.0 --port 8000 \
    > "$LOG_DIR/backend.log" 2>&1 &
echo $! > "$PROJECT_DIR/.backend.pid"
echo "  Backend started (PID: $(cat "$PROJECT_DIR/.backend.pid"))"
echo "  Log: $LOG_DIR/backend.log"

# Wait a moment for backend to start
sleep 2

# Start BDS
echo "Starting Bedrock Dedicated Server..."
cd "$BDS_DIR"
LD_LIBRARY_PATH=. nohup ./bedrock_server \
    > "$LOG_DIR/bds.log" 2>&1 &
echo $! > "$PROJECT_DIR/.bds.pid"
cd "$PROJECT_DIR"
echo "  BDS started (PID: $(cat "$PROJECT_DIR/.bds.pid"))"
echo "  Log: $LOG_DIR/bds.log"

echo ""
echo "=== God Is Watching ==="
echo "Backend: http://localhost:8000/status"
echo "Minecraft: port 19132 (UDP)"
echo ""
echo "To stop: ./scripts/stop.sh"
