#!/bin/bash
# Back up the Minecraft world. Stops BDS briefly for consistency (LevelDB).
# Keeps a rolling window of backups, deleting the oldest when over the limit.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
WORLD_DIR="$PROJECT_DIR/bds/worlds/God World"
BACKUP_DIR="$PROJECT_DIR/backups"
MAX_BACKUPS=6

mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y-%m-%d-%H%M)
BACKUP_FILE="$BACKUP_DIR/god-world-$TIMESTAMP.tar.gz"

echo "=== Minecraft God: World Backup ==="
echo "  Timestamp: $TIMESTAMP"

if [ ! -d "$WORLD_DIR" ]; then
    echo "ERROR: World directory not found: $WORLD_DIR"
    exit 1
fi

# Stop BDS for a consistent snapshot
echo "  Stopping BDS..."
systemctl --user stop minecraft-god-bds 2>/dev/null || true
sleep 2

# Create backup
echo "  Archiving world..."
tar -czf "$BACKUP_FILE" -C "$PROJECT_DIR/bds/worlds" "God World"

# Restart BDS
echo "  Restarting BDS..."
systemctl --user start minecraft-god-bds 2>/dev/null || true

# Get backup size
SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "  Backup created: $BACKUP_FILE ($SIZE)"

# Prune old backups (keep newest MAX_BACKUPS)
BACKUP_COUNT=$(ls -1 "$BACKUP_DIR"/god-world-*.tar.gz 2>/dev/null | wc -l)
if [ "$BACKUP_COUNT" -gt "$MAX_BACKUPS" ]; then
    DELETE_COUNT=$((BACKUP_COUNT - MAX_BACKUPS))
    echo "  Pruning $DELETE_COUNT old backup(s)..."
    ls -1t "$BACKUP_DIR"/god-world-*.tar.gz | tail -n "$DELETE_COUNT" | xargs rm -f
fi

echo "  Backups on disk: $(ls -1 "$BACKUP_DIR"/god-world-*.tar.gz 2>/dev/null | wc -l)/$MAX_BACKUPS"
echo "=== Backup complete ==="
