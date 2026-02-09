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
sed -i 's/^texturepack-required=.*/texturepack-required=true/' "$PROPS"

echo "  server.properties configured"

# --- allowlist.json ---
echo "Configuring allowlist..."
cat > "$BDS_DIR/allowlist.json" << 'ALLOWEOF'
[
    {"name": "aebrer", "ignoresPlayerLimit": true},
    {"name": "BUBBACHUBUBBA"},
    {"name": "Embrer1890"},
    {"name": "BldngARoad"},
    {"name": "MORBIDCARNAG3"},
    {"name": "gmfern"}
]
ALLOWEOF
echo "  allowlist configured (6 players)"

# --- Install behavior pack ---
echo "Installing behavior pack..."
PACK_DEST="$BDS_DIR/behavior_packs/minecraft_god"
rm -rf "$PACK_DEST"
cp -r "$PACK_SRC" "$PACK_DEST"
echo "  Behavior pack installed"

# --- Install Backrooms addon (if present) ---
BACKROOMS_ADDON="$PROJECT_DIR/addons/backrooms_addon_plus"
if [ -d "$BACKROOMS_ADDON/bp" ]; then
    echo "Installing Backrooms Addon+..."
    BACKROOMS_BP="$BDS_DIR/behavior_packs/backrooms_addon_plus"
    BACKROOMS_RP="$BDS_DIR/resource_packs/backrooms_addon_plus"
    rm -rf "$BACKROOMS_BP" "$BACKROOMS_RP"
    cp -r "$BACKROOMS_ADDON/bp" "$BACKROOMS_BP"
    cp -r "$BACKROOMS_ADDON/rp" "$BACKROOMS_RP"
    echo "  Backrooms Addon+ installed (BP + RP)"
    BACKROOMS_INSTALLED=true
else
    echo "  Backrooms addon not found at $BACKROOMS_ADDON, skipping"
    BACKROOMS_INSTALLED=false
fi

# --- Enable packs on the world ---
echo "Enabling packs on world..."
WORLD_DIR="$BDS_DIR/worlds/God World"
mkdir -p "$WORLD_DIR"

if [ "$BACKROOMS_INSTALLED" = true ]; then
    cat > "$WORLD_DIR/world_behavior_packs.json" << 'PACKEOF'
[
    {
        "pack_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "version": [1, 0, 0]
    },
    {
        "pack_id": "531dcc02-0edd-4b63-9d43-a23f66b51418",
        "version": [1, 0, 0]
    }
]
PACKEOF
    cat > "$WORLD_DIR/world_resource_packs.json" << 'PACKEOF'
[
    {
        "pack_id": "df53e954-ca86-483d-9fc6-53a02a75abbf",
        "version": [1, 0, 0]
    }
]
PACKEOF
    echo "  world packs configured (minecraft_god BP + backrooms BP + backrooms RP)"
else
    cat > "$WORLD_DIR/world_behavior_packs.json" << 'PACKEOF'
[
    {
        "pack_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "version": [1, 0, 0]
    }
]
PACKEOF
    echo "  world_behavior_packs.json created"
fi

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
echo "IMPORTANT: You still need to enable experiments on the world via level.dat NBT edit."
echo "Required experiments:"
echo "  - gametest (Beta APIs) — for @minecraft/server-net"
if [ "$BACKROOMS_INSTALLED" = true ]; then
echo "  - data_driven_items (Holiday Creator Features) — for Backrooms Addon+"
fi
echo ""
echo "See ARCHITECTURE.md for the Python NBT editing procedure."
echo ""
echo "Run start.sh to launch the server."
