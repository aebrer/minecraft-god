# What Drew Needs To Provide

When you're ready to build, give Claude these things:

## 1. z.ai API Key
- Log into z.ai, grab your API key
- Claude will put it in `.env` (never committed to git)

## 2. Player Gamertags
- Xbox/Microsoft gamertags for everyone who'll be playing
- These go in the server allowlist so only your people can join

## 3. Port Forwarding (you do this manually)
- Log into your Asus Merlin router admin panel (usually 192.168.1.1)
- Go to **WAN > Virtual Server / Port Forwarding**
- Add a rule:
  - Protocol: **UDP**
  - External Port: **19132**
  - Internal IP: atwood's local IP (check with `ip addr` or look at DHCP client list in router)
  - Internal Port: **19132**
- Save and apply

## 4. Share Your Public IP With Friends
- Google "what is my ip" to find your public IP
- Friends add a server in Minecraft Bedrock using that IP, port 19132
- If your ISP rotates your IP, we can set up a free dynamic DNS later

## That's It
Everything else (downloading BDS, writing the behavior pack, Python backend, systemd service) Claude handles. Just start a session and say "let's build minecraft-god phase 1".
