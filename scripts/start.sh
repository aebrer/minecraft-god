#!/bin/bash
# Start the Python backend and Paper server
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PAPER_DIR="$PROJECT_DIR/paper"
LOG_DIR="$PROJECT_DIR/logs"

mkdir -p "$LOG_DIR"

echo "=== Minecraft God: Starting ==="

# Check for .env
if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "ERROR: .env file not found. Copy .env.example to .env and set your API key."
    exit 1
fi

# Check for Paper
if [ ! -f "$PAPER_DIR/paper-1.21.11-69.jar" ]; then
    echo "ERROR: Paper server jar not found in $PAPER_DIR"
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
cd "$PROJECT_DIR"
nohup uvicorn server.main:app --host 127.0.0.1 --port 8000 \
    > "$LOG_DIR/backend.log" 2>&1 &
echo $! > "$PROJECT_DIR/.backend.pid"
echo "  Backend started (PID: $(cat "$PROJECT_DIR/.backend.pid"))"
echo "  Log: $LOG_DIR/backend.log"

# Wait a moment for backend to start
sleep 2

# Start Paper
echo "Starting Paper server..."
cd "$PAPER_DIR"
nohup java -Xms1G -Xmx2G -jar paper-1.21.11-69.jar --nogui \
    > "$LOG_DIR/paper.log" 2>&1 &
echo $! > "$PROJECT_DIR/.paper.pid"
cd "$PROJECT_DIR"
echo "  Paper started (PID: $(cat "$PROJECT_DIR/.paper.pid"))"
echo "  Log: $LOG_DIR/paper.log"

echo ""
echo "=== God Is Watching ==="
echo "Backend: http://localhost:8000/status"
echo "Minecraft: port 25565 (TCP)"
echo ""
echo "To stop: ./scripts/stop.sh"
