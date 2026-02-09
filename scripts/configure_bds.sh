#!/bin/bash
# Configure BDS: server.properties, behavior pack, permissions
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BDS_DIR="$PROJECT_DIR/bds"
PACK_SRC="$PROJECT_DIR/behavior_pack"

echo "=== Minecraft God: BDS Configuration ==="

if [ ! -d "$BDS_DIR" ]; then
    echo "ERROR: BDS not installed. Run install_bds.sh first."
    exit 1
fi

# --- server.properties ---
echo "Configuring server.properties..."
PROPS="$BDS_DIR/server.properties"

# Backup original
if [ ! -f "$PROPS.orig" ]; then
    cp "$PROPS" "$PROPS.orig"
fi

# Set key properties (sed in-place)
sed -i 's/^server-name=.*/server-name=God Is Watching/' "$PROPS"
sed -i 's/^gamemode=.*/gamemode=survival/' "$PROPS"
sed -i 's/^difficulty=.*/difficulty=normal/' "$PROPS"
sed -i 's/^max-players=.*/max-players=5/' "$PROPS"
sed -i 's/^allow-cheats=.*/allow-cheats=true/' "$PROPS"
sed -i 's/^server-port=.*/server-port=19132/' "$PROPS"
sed -i 's/^level-name=.*/level-name=God World/' "$PROPS"
sed -i 's/^allow-list=.*/allow-list=true/' "$PROPS"
sed -i 's/^content-log-file-enabled=.*/content-log-file-enabled=true/' "$PROPS"
sed -i 's/^content-log-console-output-enabled=.*/content-log-console-output-enabled=true/' "$PROPS"

echo "  server.properties configured"

# --- allowlist.json ---
echo "Configuring allowlist..."
cat > "$BDS_DIR/allowlist.json" << 'ALLOWEOF'
[
    {
        "name": "aebrer",
        "ignoresPlayerLimit": true
    }
]
ALLOWEOF
echo "  allowlist configured (aebrer)"

# --- Install behavior pack ---
echo "Installing behavior pack..."
PACK_DEST="$BDS_DIR/behavior_packs/minecraft_god"
rm -rf "$PACK_DEST"
cp -r "$PACK_SRC" "$PACK_DEST"
echo "  Behavior pack installed"

# --- Enable behavior pack on the world ---
echo "Enabling behavior pack on world..."
WORLD_DIR="$BDS_DIR/worlds/God World"
mkdir -p "$WORLD_DIR"

cat > "$WORLD_DIR/world_behavior_packs.json" << 'PACKEOF'
[
    {
        "pack_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "version": [1, 0, 0]
    }
]
PACKEOF
echo "  world_behavior_packs.json created"

# --- permissions.json for server-net module ---
echo "Configuring script permissions..."
PERMS_DIR="$BDS_DIR/config/default"
mkdir -p "$PERMS_DIR"

cat > "$PERMS_DIR/permissions.json" << 'PERMEOF'
{
    "allowed_modules": [
        "@minecraft/server-gametest",
        "@minecraft/server",
        "@minecraft/server-ui",
        "@minecraft/server-admin",
        "@minecraft/server-net"
    ]
}
PERMEOF
echo "  permissions.json configured (includes @minecraft/server-net)"

echo ""
echo "Configuration complete!"
echo ""
echo "IMPORTANT: You still need to enable the Beta APIs experiment on the world."
echo "Options:"
echo "  1. Create the world on a Minecraft client with Beta APIs enabled, export it"
echo "  2. Connect to the server, go to Settings > Experiments > Beta APIs"
echo ""
echo "Run start.sh to launch the server."
