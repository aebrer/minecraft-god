#!/usr/bin/env python3
"""Announce a pending server restart with a countdown."""

import os
import subprocess
import time
import sys


RCON_PORT = os.environ.get("RCON_PORT", "25575")
RCON_PASS = os.environ.get("RCON_PASS", "")

if not RCON_PASS:
    print("ERROR: RCON_PASS environment variable not set.")
    print("Usage: RCON_PASS=yourpassword python3 announce_restart.py")
    sys.exit(1)


def rcon(command: str):
    """Send a command via RCON using mcrcon or a raw socket."""
    # Use the backend's command queue via HTTP to send tellraw
    import httpx
    # Push command directly to the MC server console via the plugin's polling
    # Actually, simpler: just use the backend to queue a title command
    resp = httpx.post(
        "http://localhost:8000/event",
        json={"type": "chat", "player": "SYSTEM", "message": command},
    )


def announce():
    """Send countdown messages before restart."""
    import httpx

    def send_title(text: str, subtitle: str = "", color: str = "gold"):
        """Send a title to all players via direct MC command."""
        # Build tellraw for chat
        chat_cmd = f'tellraw @a {{"text":"[SERVER] {text}","color":"{color}","bold":true}}'
        # Build title
        title_cmd = f'title @a title {{"text":"{text}","color":"{color}","bold":true}}'
        subtitle_cmd = ""
        if subtitle:
            subtitle_cmd = f'title @a subtitle {{"text":"{subtitle}","color":"yellow"}}'

        # Queue via backend
        httpx.post(
            "http://localhost:8000/commands",
            json=[
                {"command": chat_cmd, "target_player": None},
                {"command": title_cmd, "target_player": None},
            ],
        )
        if subtitle_cmd:
            httpx.post(
                "http://localhost:8000/commands",
                json=[{"command": subtitle_cmd, "target_player": None}],
            )

    # Check if backend accepts command injection — if not, fall back to direct console
    # Actually, the backend only has GET /commands. Let's post commands differently.
    # The simplest approach: write commands directly via paper's console

    # Use screen/tmux or just write to server stdin? Let's check how the server runs.
    # Server runs as systemd service. Let's just use RCON directly via Python sockets.

    import socket
    import struct

    def rcon_command(cmd: str):
        """Send RCON command to Minecraft server."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", int(RCON_PORT)))

        # Login packet
        req_id = 1
        payload = struct.pack("<iii", 10 + len(RCON_PASS), req_id, 3) + RCON_PASS.encode() + b"\x00\x00"
        sock.send(payload)
        sock.recv(4096)  # login response

        # Command packet
        req_id = 2
        payload = struct.pack("<iii", 10 + len(cmd), req_id, 2) + cmd.encode() + b"\x00\x00"
        sock.send(payload)
        resp = sock.recv(4096)
        sock.close()
        return resp

    print("Announcing restart...")

    # 30 second countdown
    rcon_command('tellraw @a {"text":"","extra":[{"text":"[SERVER] ","color":"red","bold":true},{"text":"Server restarting in 30 seconds for plugin update (schematic building system!)","color":"yellow"}]}')
    rcon_command('title @a title {"text":"Server Restart","color":"red","bold":true}')
    rcon_command('title @a subtitle {"text":"30 seconds - plugin update","color":"yellow"}')
    rcon_command('playsound minecraft:block.note_block.bell master @a ~ ~ ~ 1 1')
    print("  30s warning sent")
    time.sleep(15)

    rcon_command('tellraw @a {"text":"","extra":[{"text":"[SERVER] ","color":"red","bold":true},{"text":"Restarting in 15 seconds...","color":"yellow"}]}')
    rcon_command('title @a title {"text":"15 seconds","color":"gold","bold":true}')
    rcon_command('playsound minecraft:block.note_block.bell master @a ~ ~ ~ 1 1')
    print("  15s warning sent")
    time.sleep(10)

    rcon_command('tellraw @a {"text":"","extra":[{"text":"[SERVER] ","color":"red","bold":true},{"text":"Restarting in 5...","color":"red"}]}')
    rcon_command('title @a title {"text":"5...","color":"red","bold":true}')
    rcon_command('playsound minecraft:block.note_block.bell master @a ~ ~ ~ 1 1.5')
    print("  5s warning sent")
    time.sleep(2)

    rcon_command('title @a title {"text":"3...","color":"red","bold":true}')
    rcon_command('playsound minecraft:block.note_block.bell master @a ~ ~ ~ 1 1.5')
    time.sleep(1)
    rcon_command('title @a title {"text":"2...","color":"red","bold":true}')
    rcon_command('playsound minecraft:block.note_block.bell master @a ~ ~ ~ 1 1.5')
    time.sleep(1)
    rcon_command('title @a title {"text":"1...","color":"red","bold":true}')
    rcon_command('playsound minecraft:block.note_block.bell master @a ~ ~ ~ 1 2')
    time.sleep(1)

    print("Countdown complete. Ready to restart.")


if __name__ == "__main__":
    announce()
