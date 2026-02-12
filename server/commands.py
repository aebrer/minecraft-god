"""Translate LLM tool calls into Minecraft commands.

All commands go through an allowlist. If something isn't in the list, it gets dropped.
"""

import json
import logging
import re

from server.schematics import browse_schematics, search_schematics, inspect_schematic, build_schematic_command

logger = logging.getLogger("minecraft-god")

# Allowlisted command prefixes — anything not starting with one of these is dropped
ALLOWED_COMMANDS = {
    "summon", "title", "say", "tellraw", "weather", "effect", "tp", "teleport",
    "give", "clear", "playsound", "time", "difficulty", "setblock", "fill",
}

# Chat prefixes for each god — used by tellraw for attributed messages
GOD_CHAT_STYLE = {
    "kind_god": {"name": "The Kind God", "color": "gold"},
    "deep_god": {"name": "???", "color": "dark_red"},
    "herald": {"name": "The Herald", "color": "green"},
}

# Max volume for fill commands (prevents massive world edits)
MAX_FILL_VOLUME = 500

# Dangerous items that must never be given to players
BLOCKED_ITEMS = {
    "command_block", "repeating_command_block", "chain_command_block",
    "command_block_minecart", "barrier", "structure_block", "structure_void",
    "light", "allow", "deny", "border_block", "jigsaw",
    "bedrock", "end_portal_frame", "end_portal",
}

# Valid target selectors — only @a (all players) and @s (self) are allowed
# @e (all entities) and @r (random) are too broad
ALLOWED_SELECTORS = {"@a", "@s", "@p"}

# Valid mob types the gods can summon
VALID_MOBS = {
    # Hostile
    "zombie", "skeleton", "creeper", "spider", "cave_spider", "silverfish",
    "enderman", "witch", "phantom", "slime", "magma_cube", "blaze",
    "wither_skeleton", "piglin_brute", "hoglin", "ghast",
    # Neutral
    "wolf", "iron_golem", "bee", "piglin",
    # Passive
    "cow", "sheep", "pig", "chicken", "rabbit", "horse", "cat", "fox",
    "axolotl", "frog", "turtle",
    # Special
    "lightning_bolt", "villager", "wandering_trader",
}

# Valid status effects
VALID_EFFECTS = {
    "speed", "slowness", "haste", "mining_fatigue", "strength",
    "instant_health", "instant_damage", "jump_boost", "nausea",
    "regeneration", "resistance", "fire_resistance", "water_breathing",
    "invisibility", "blindness", "night_vision", "hunger", "weakness",
    "poison", "levitation", "slow_falling", "darkness", "absorption",
    "saturation", "glowing",
}


def translate_tool_calls(tool_calls: list, source: str = "kind_god") -> list[dict]:
    """Convert LLM tool calls into a list of command dicts.

    Each command dict has:
        - "command": the Minecraft command string (without leading /)
        - "target_player": optional player name for relative commands

    source: which god is speaking ("kind_god", "deep_god", "herald")
    """
    commands = []
    for tc in tool_calls:
        try:
            name = tc.function.name
            args = json.loads(tc.function.arguments)
            logger.info(f"[{source}] tool call: {name}({json.dumps(args, separators=(',', ':'))})")
            result = _translate_one(tc, source)
            if result is None:
                continue
            if isinstance(result, list):
                commands.extend(result)
            else:
                commands.append(result)
        except Exception:
            logger.exception(f"Failed to translate tool call: {tc.function.name}")

    for cmd in commands:
        if cmd.get("type") == "build_schematic":
            logger.info(f"[{source}] => build_schematic: {cmd.get('blueprint_id')} at "
                        f"{cmd.get('x')},{cmd.get('y')},{cmd.get('z')} rot={cmd.get('rotation')}")
        else:
            cmd_str = cmd.get("command", "?")
            target = cmd.get("target_player")
            logger.info(f"[{source}] => cmd: {cmd_str[:120]}" + (f" (target: {target})" if target else ""))

    return commands


def _translate_one(tool_call, source: str = "kind_god") -> dict | list[dict] | None:
    name = tool_call.function.name
    args = json.loads(tool_call.function.arguments)

    if name == "do_nothing":
        reason = args.get("reason", "no reason given")
        logger.info(f"God chose to do nothing: {reason}")
        return None

    if name == "send_message":
        return _send_message(args, source)
    elif name == "summon_mob":
        return _summon_mob(args)
    elif name == "change_weather":
        return _change_weather(args)
    elif name == "give_effect":
        return _give_effect(args)
    elif name == "set_time":
        return _set_time(args)
    elif name == "give_item":
        return _give_item(args)
    elif name == "clear_item":
        return _clear_item(args)
    elif name == "strike_lightning":
        return _strike_lightning(args)
    elif name == "play_sound":
        return _play_sound(args)
    elif name == "set_difficulty":
        return _set_difficulty(args)
    elif name == "teleport_player":
        return _teleport_player(args)
    elif name == "assign_mission":
        return _assign_mission(args, source)
    elif name == "place_block":
        return _place_block(args)
    elif name == "fill_blocks":
        return _fill_blocks(args)
    elif name == "build_schematic":
        return _build_schematic(args)
    else:
        logger.warning(f"Unknown tool call: {name}")
        return None


# Regex for valid player names (Xbox gamertags: alphanumeric + spaces, 1-15 chars)
_PLAYER_NAME_RE = re.compile(r"^[a-zA-Z0-9_ ]{1,32}$")
# Regex for valid coordinates (numbers, ~, ^, -, .)
_COORD_RE = re.compile(r"^[~^0-9. -]+$")


def _validate_player_target(target: str) -> bool:
    """Validate a player name or target selector."""
    if target in ALLOWED_SELECTORS:
        return True
    if _PLAYER_NAME_RE.match(target):
        return True
    logger.warning(f"Blocked invalid player target: {target}")
    return False


def _validate_command(cmd_str: str) -> bool:
    """Check that a command starts with an allowed prefix."""
    first_word = cmd_str.strip().split()[0].lower()
    return first_word in ALLOWED_COMMANDS


def _cmd(command: str, target_player: str | None = None) -> dict | None:
    """Create a validated command dict."""
    if not _validate_command(command):
        logger.warning(f"Blocked disallowed command: {command}")
        return None
    return {"command": command, "target_player": target_player}


def _send_message(args: dict, source: str = "kind_god") -> dict | list[dict] | None:
    message = args.get("message", "")
    style = args.get("style", "chat")
    target = args.get("target_player")

    # Sanitize message — no newlines, cap length
    message = message.replace("\n", " ").strip()[:200]

    if style == "title":
        text_json = json.dumps({"text": message})
        selector = target if target else "@a"
        if not _validate_player_target(selector):
            return None
        return _cmd(f"title {selector} title {text_json}")
    elif style == "actionbar":
        text_json = json.dumps({"text": message})
        selector = target if target else "@a"
        if not _validate_player_target(selector):
            return None
        return _cmd(f"title {selector} actionbar {text_json}")
    else:
        # Chat style — use tellraw with attributed god name
        god_style = GOD_CHAT_STYLE.get(source, {"name": "God", "color": "white"})
        if target and not _validate_player_target(target):
            return None

        if target:
            # Private message — send actual message to target, notification to everyone else
            # To the target player: "[whispered] <God> message"
            whisper_json = json.dumps([
                {"text": "[whispered] ", "color": "gray", "italic": True},
                {"text": f"<{god_style['name']}> ", "color": god_style["color"], "bold": True},
                {"text": message, "color": "white"},
            ])
            # To everyone else: "<God> *whispers to player*"
            notify_json = json.dumps([
                {"text": f"<{god_style['name']}> ", "color": god_style["color"], "bold": True},
                {"text": f"*whispers to {target}*", "color": "gray", "italic": True},
            ])
            cmds = []
            cmd = _cmd(f"tellraw {target} {whisper_json}")
            if cmd:
                cmds.append(cmd)
            cmd = _cmd(f"tellraw @a[name=!{target}] {notify_json}")
            if cmd:
                cmds.append(cmd)
            return cmds
        else:
            # Public message — to everyone
            tellraw_json = json.dumps([
                {"text": f"<{god_style['name']}> ", "color": god_style["color"], "bold": True},
                {"text": message, "color": "white"},
            ])
            return _cmd(f"tellraw @a {tellraw_json}")


def _summon_mob(args: dict) -> list[dict] | None:
    mob_type = args.get("mob_type", "").lower().replace("minecraft:", "")
    if mob_type not in VALID_MOBS:
        logger.warning(f"Blocked invalid mob type: {mob_type}")
        return None

    near_player = args.get("near_player")
    location = args.get("location", "~ ~ ~")
    count = min(max(args.get("count", 1), 1), 5)  # clamp 1-5

    commands = []
    for _ in range(count):
        cmd = _cmd(f"summon minecraft:{mob_type} {location}", target_player=near_player)
        if cmd:
            commands.append(cmd)
    return commands


def _change_weather(args: dict) -> dict | None:
    weather = args.get("weather_type", "clear").lower()
    if weather not in ("clear", "rain", "thunder"):
        return None
    duration = min(max(args.get("duration", 6000), 1), 24000)
    return _cmd(f"weather {weather} {duration}")


def _give_effect(args: dict) -> dict | None:
    target = args.get("target_player", "@a")
    if not _validate_player_target(target):
        return None
    effect = args.get("effect", "").lower()
    if effect not in VALID_EFFECTS:
        logger.warning(f"Blocked invalid effect: {effect}")
        return None
    duration = min(max(args.get("duration", 30), 1), 120)
    amplifier = min(max(args.get("amplifier", 0), 0), 3)
    return _cmd(f"effect give {target} minecraft:{effect} {duration} {amplifier}")


def _set_time(args: dict) -> dict | None:
    time_val = args.get("time", "day").lower()
    valid_times = {"day", "noon", "sunset", "night", "midnight", "sunrise"}
    if time_val not in valid_times:
        return None
    return _cmd(f"time set {time_val}")


def _give_item(args: dict) -> dict | None:
    player = args.get("player", "@a")
    if not _validate_player_target(player):
        return None
    item = args.get("item", "").lower().replace("minecraft:", "")
    if item in BLOCKED_ITEMS:
        logger.warning(f"Blocked dangerous item: {item}")
        return None
    count = min(max(args.get("count", 1), 1), 64)
    return _cmd(f"give {player} minecraft:{item} {count}")


def _clear_item(args: dict) -> dict | None:
    player = args.get("player", "@a")
    if not _validate_player_target(player):
        return None
    item = args.get("item", "")
    if item:
        item = item.lower().replace("minecraft:", "")
        return _cmd(f"clear {player} minecraft:{item}")
    else:
        return _cmd(f"clear {player}")


def _strike_lightning(args: dict) -> dict | None:
    near_player = args.get("near_player")
    offset = args.get("offset", "~ ~ ~")
    return _cmd(f"summon minecraft:lightning_bolt {offset}", target_player=near_player)


def _play_sound(args: dict) -> dict | None:
    sound = args.get("sound", "")
    target = args.get("target_player", "@a")
    sound = sound if sound.startswith("minecraft:") else f"minecraft:{sound}"
    return _cmd(f"playsound {sound} master {target}")


def _set_difficulty(args: dict) -> dict | None:
    difficulty = args.get("difficulty", "normal").lower()
    if difficulty not in ("peaceful", "easy", "normal", "hard"):
        return None
    return _cmd(f"difficulty {difficulty}")


def _teleport_player(args: dict) -> dict | None:
    player = args.get("player", "")
    if not _validate_player_target(player):
        return None
    x = args.get("x", 0)
    y = args.get("y", 64)
    z = args.get("z", 0)
    return _cmd(f"tp {player} {x} {y} {z}")


def _assign_mission(args: dict, source: str = "kind_god") -> list[dict]:
    """Send a mission as title + subtitle + broadcast chat announcement."""
    player = args.get("player", "@a")
    title = args.get("mission_title", "A Task")
    description = args.get("mission_description", "")
    reward_hint = args.get("reward_hint", "")

    commands = []

    # Subtitle MUST be set before the title (title triggers the display)
    if description:
        sub_json = json.dumps({"text": description[:100]})
        cmd = _cmd(f"title {player} subtitle {sub_json}")
        if cmd:
            commands.append(cmd)

    # Title — triggers the on-screen display
    title_json = json.dumps({"text": title})
    cmd = _cmd(f"title {player} title {title_json}")
    if cmd:
        commands.append(cmd)

    # Broadcast quest announcement to ALL players via attributed tellraw
    style = GOD_CHAT_STYLE.get(source, GOD_CHAT_STYLE["kind_god"])
    announce_parts = [f"Quest for {player}: {title}"]
    if description:
        announce_parts.append(f" — {description[:100]}")
    if reward_hint:
        announce_parts.append(f" ({reward_hint})")
    announce_text = "".join(announce_parts)
    tellraw_json = json.dumps([
        {"text": f"<{style['name']}> ", "color": style["color"]},
        {"text": announce_text, "color": "yellow"},
    ])
    cmd = _cmd(f"tellraw @a {tellraw_json}")
    if cmd:
        commands.append(cmd)

    return commands


def _validate_block(block: str) -> str | None:
    """Validate and clean a block type. Returns cleaned name or None if blocked."""
    block = block.lower().replace("minecraft:", "").strip()
    if not block or not re.match(r"^[a-z0-9_]+$", block):
        logger.warning(f"Blocked invalid block name: {block}")
        return None
    if block in BLOCKED_ITEMS:
        logger.warning(f"Blocked dangerous block type: {block}")
        return None
    return block


def _validate_coordinate(val) -> int | None:
    """Validate a single coordinate value (integer, clamped to reasonable range)."""
    try:
        n = int(val)
        if -30000 <= n <= 30000:
            return n
    except (TypeError, ValueError):
        pass
    return None


def _place_block(args: dict) -> dict | None:
    block = _validate_block(args.get("block", ""))
    if not block:
        return None
    x = _validate_coordinate(args.get("x"))
    y = _validate_coordinate(args.get("y"))
    z = _validate_coordinate(args.get("z"))
    if x is None or y is None or z is None:
        logger.warning(f"Blocked place_block with invalid coordinates: {args}")
        return None
    return _cmd(f"setblock {x} {y} {z} minecraft:{block}")


def _fill_blocks(args: dict) -> dict | None:
    block = _validate_block(args.get("block", ""))
    if not block:
        return None

    x1 = _validate_coordinate(args.get("x1"))
    y1 = _validate_coordinate(args.get("y1"))
    z1 = _validate_coordinate(args.get("z1"))
    x2 = _validate_coordinate(args.get("x2"))
    y2 = _validate_coordinate(args.get("y2"))
    z2 = _validate_coordinate(args.get("z2"))

    coords = [x1, y1, z1, x2, y2, z2]
    if any(c is None for c in coords):
        logger.warning(f"Blocked fill_blocks with invalid coordinates: {args}")
        return None

    # Calculate volume and enforce cap
    dx = abs(x2 - x1) + 1
    dy = abs(y2 - y1) + 1
    dz = abs(z2 - z1) + 1
    volume = dx * dy * dz

    if volume > MAX_FILL_VOLUME:
        logger.warning(f"Blocked fill_blocks: volume {volume} exceeds max {MAX_FILL_VOLUME}")
        return None

    mode = args.get("mode", "replace").lower()
    if mode not in ("replace", "hollow", "outline", "keep"):
        mode = "replace"

    return _cmd(f"fill {x1} {y1} {z1} {x2} {y2} {z2} minecraft:{block} {mode}")


def _build_schematic(args: dict) -> dict | None:
    blueprint_id = args.get("blueprint_id", "")
    x = _validate_coordinate(args.get("x"))
    y = _validate_coordinate(args.get("y"))
    z = _validate_coordinate(args.get("z"))
    if x is None or y is None or z is None:
        logger.warning(f"Blocked build_schematic with invalid coordinates: {args}")
        return None
    rotation = args.get("rotation", 0)
    return build_schematic_command(blueprint_id, x, y, z, rotation)


def get_schematic_tool_results(tool_calls: list) -> dict[str, str]:
    """Process browse/inspect schematic tool calls and return tool results.

    Returns a dict mapping tool_call_id -> result text.
    These are meant to be injected back into the conversation as tool results
    for a follow-up LLM call.
    """
    results = {}
    for tc in tool_calls:
        name = tc.function.name
        try:
            args = json.loads(tc.function.arguments)
        except (json.JSONDecodeError, AttributeError):
            continue

        if name == "search_schematics":
            query = args.get("query", "")
            results[tc.id] = search_schematics(query)
        elif name == "browse_schematics":
            category = args.get("category", "all")
            results[tc.id] = browse_schematics(category)
        elif name == "inspect_schematic":
            blueprint_id = args.get("blueprint_id", "")
            results[tc.id] = inspect_schematic(blueprint_id)

    return results
