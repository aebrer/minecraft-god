#!/bin/bash
# Download and install Bedrock Dedicated Server for Linux
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BDS_DIR="$PROJECT_DIR/bds"

echo "=== Minecraft God: BDS Installer ==="

if [ -d "$BDS_DIR" ] && [ -f "$BDS_DIR/bedrock_server" ]; then
    echo "BDS already installed at $BDS_DIR"
    echo "Delete the bds/ directory to reinstall."
    exit 0
fi

# Get download URL from Minecraft.net
# The URL changes with each version, so we scrape the latest
echo "Fetching latest BDS download URL..."
DOWNLOAD_PAGE="https://www.minecraft.net/en-us/download/server/bedrock"
DOWNLOAD_URL=$(curl -s "$DOWNLOAD_PAGE" | grep -oP 'https://minecraft\.azureedge\.net/bin-linux/[^"]+\.zip' | head -1)

if [ -z "$DOWNLOAD_URL" ]; then
    echo "ERROR: Could not find BDS download URL."
    echo "Please download manually from: $DOWNLOAD_PAGE"
    echo "Extract to: $BDS_DIR"
    exit 1
fi

echo "Downloading: $DOWNLOAD_URL"
TEMP_ZIP="$(mktemp /tmp/bds-XXXXXX.zip)"
curl -L -o "$TEMP_ZIP" "$DOWNLOAD_URL"

echo "Extracting to $BDS_DIR..."
mkdir -p "$BDS_DIR"
unzip -o "$TEMP_ZIP" -d "$BDS_DIR"
rm "$TEMP_ZIP"

chmod +x "$BDS_DIR/bedrock_server"

echo ""
echo "BDS installed successfully!"
echo "Run configure_bds.sh next to set up server properties and the behavior pack."
