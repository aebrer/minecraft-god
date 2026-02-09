#!/bin/bash
# Gracefully stop the Python backend and Bedrock Dedicated Server
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Minecraft God: Stopping ==="

# Stop BDS
if [ -f "$PROJECT_DIR/.bds.pid" ]; then
    BDS_PID=$(cat "$PROJECT_DIR/.bds.pid")
    if kill -0 "$BDS_PID" 2>/dev/null; then
        echo "Stopping BDS (PID: $BDS_PID)..."
        kill "$BDS_PID"
        # Wait for graceful shutdown
        for i in $(seq 1 10); do
            if ! kill -0 "$BDS_PID" 2>/dev/null; then
                break
            fi
            sleep 1
        done
        if kill -0 "$BDS_PID" 2>/dev/null; then
            echo "  Force killing BDS..."
            kill -9 "$BDS_PID"
        fi
        echo "  BDS stopped"
    else
        echo "  BDS not running"
    fi
    rm -f "$PROJECT_DIR/.bds.pid"
else
    echo "  No BDS PID file found"
fi

# Stop Python backend
if [ -f "$PROJECT_DIR/.backend.pid" ]; then
    BACKEND_PID=$(cat "$PROJECT_DIR/.backend.pid")
    if kill -0 "$BACKEND_PID" 2>/dev/null; then
        echo "Stopping backend (PID: $BACKEND_PID)..."
        kill "$BACKEND_PID"
        sleep 2
        if kill -0 "$BACKEND_PID" 2>/dev/null; then
            kill -9 "$BACKEND_PID"
        fi
        echo "  Backend stopped"
    else
        echo "  Backend not running"
    fi
    rm -f "$PROJECT_DIR/.backend.pid"
else
    echo "  No backend PID file found"
fi

echo ""
echo "God rests."
