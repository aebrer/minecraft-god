#!/bin/bash
# Gracefully stop the Python backend and Paper server
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Minecraft God: Stopping ==="

# Stop Paper
if [ -f "$PROJECT_DIR/.paper.pid" ]; then
    PAPER_PID=$(cat "$PROJECT_DIR/.paper.pid")
    if kill -0 "$PAPER_PID" 2>/dev/null; then
        echo "Stopping Paper (PID: $PAPER_PID)..."
        kill "$PAPER_PID"
        for i in $(seq 1 15); do
            if ! kill -0 "$PAPER_PID" 2>/dev/null; then
                break
            fi
            sleep 1
        done
        if kill -0 "$PAPER_PID" 2>/dev/null; then
            echo "  Force killing Paper..."
            kill -9 "$PAPER_PID"
        fi
        echo "  Paper stopped"
    else
        echo "  Paper not running"
    fi
    rm -f "$PROJECT_DIR/.paper.pid"
else
    echo "  No Paper PID file found"
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
