"""Translate LLM tool calls into Minecraft commands.

All commands go through an allowlist. If something isn't in the list, it gets dropped.
"""

import json
import logging
import re

from server.schematics import search_schematics, build_schematic_command

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


def translate_tool_calls(tool_calls: list, source: str = "kind_god",
                         player_context: dict | None = None) -> tuple[list[dict], dict[str, str]]:
    """Convert LLM tool calls into a list of command dicts.

    Returns (commands, errors) where:
        - commands: list of command dicts (with "command" or "type" key)
        - errors: dict mapping tool_call_id -> error message for failed calls

    source: which god is speaking ("kind_god", "deep_god", "herald")
    player_context: dict mapping player names (lowercase) to their position/facing data,
        used for resolving build_schematic placement. Each entry has:
        {"x": int, "y": int, "z": int, "facing": str}
    """
    commands = []
    errors = {}
    for tc in tool_calls:
        try:
            name = tc.function.name
            args = json.loads(tc.function.arguments)
            logger.info(f"[{source}] tool call: {name}({json.dumps(args, separators=(',', ':'))})")
            result = _translate_one(tc, source, player_context=player_context)
            if result is None:
                # Build an error message based on what failed
                error = _describe_failure(name, args)
                if error:
                    errors[tc.id] = error
                continue
            if isinstance(result, list):
                commands.extend(result)
            else:
                commands.append(result)
        except Exception:
            logger.exception(f"Failed to translate tool call: {tc.function.name}")
            errors[tc.id] = f"Internal error processing {tc.function.name}"

    for cmd in commands:
        if cmd.get("type") == "build_schematic":
            logger.info(f"[{source}] => build_schematic: {cmd.get('blueprint_id')} at "
                        f"{cmd.get('x')},{cmd.get('y')},{cmd.get('z')} rot={cmd.get('rotation')}")
        else:
            cmd_str = cmd.get("command", "?")
            target = cmd.get("target_player")
            logger.info(f"[{source}] => cmd: {cmd_str[:120]}" + (f" (target: {target})" if target else ""))

    return commands, errors


def _describe_failure(name: str, args: dict) -> str | None:
    """Return a human-readable error for a tool call rejected by validation.

    Called when _translate_one() returns None. This only handles local validation
    failures (bad arguments, unknown IDs, blocked items) — NOT network/timeout
    errors, which are handled upstream by the prayer queue retry mechanism.
    """
    if name == "do_nothing":
        return None  # intentional no-op
    if name == "build_schematic":
        bp_id = args.get("blueprint_id", "")
        from server.schematics import _load_catalog
        catalog = _load_catalog()
        # Check if blueprint exists
        found = False
        for cat_data in catalog["categories"].values():
            for bp in cat_data.get("blueprints", []):
                if bp["id"] == bp_id:
                    found = True
                    break
            if found:
                break
        if not found:
            return (f"ERROR: Blueprint '{bp_id}' not found. The ID may be misspelled. "
                    f"Copy the exact ID from the search results and try again.")
        near_player = args.get("near_player", "")
        if not near_player:
            return "ERROR: build_schematic requires 'near_player' — specify which player to build near."
        return None  # build_schematic args look valid, failure was elsewhere
    elif name == "summon_mob":
        mob = args.get("mob_type", "").lower().replace("minecraft:", "")
        if mob not in VALID_MOBS:
            return f"ERROR: Invalid mob type '{mob}'."
    elif name == "give_effect":
        effect = args.get("effect", "").lower()
        if effect not in VALID_EFFECTS:
            return f"ERROR: Invalid effect '{effect}'."
    elif name == "give_item":
        item = args.get("item", "").lower().replace("minecraft:", "")
        if item in BLOCKED_ITEMS:
            return f"ERROR: '{item}' is a restricted item and cannot be given."
    # Catch-all for any other tool call that was rejected
    return f"ERROR: {name} failed. Check the arguments and try again."


def _translate_one(tool_call, source: str = "kind_god",
                   player_context: dict | None = None) -> dict | list[dict] | None:
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
    elif name == "build_schematic":
        return _build_schematic(args, player_context=player_context)
    elif name == "undo_last_build":
        return {"type": "undo_last_build"}
    else:
        logger.warning(f"Unknown tool call: {name}")
        return None


# Regex for valid player names (Java Edition: alphanumeric + underscores, 3-16 chars)
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

    # Sanitize message — no newlines, cap length per style
    # Title: big on-screen text, only visible ~3 seconds — keep very short
    # Actionbar: small text above hotbar — moderate length
    # Chat: scrollable chat window — can be longer
    max_len = {"title": 40, "actionbar": 80}.get(style, 200)
    message = message.replace("\n", " ").strip()[:max_len]

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
    if not _COORD_RE.match(location):
        logger.warning(f"Blocked invalid summon location: {location}")
        return None
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
    if not re.match(r"^[a-z0-9_]+$", item):
        logger.warning(f"Blocked invalid item name: {item}")
        return None
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
        if not re.match(r"^[a-z0-9_]+$", item):
            logger.warning(f"Blocked invalid item name: {item}")
            return None
        return _cmd(f"clear {player} minecraft:{item}")
    else:
        return _cmd(f"clear {player}")


def _strike_lightning(args: dict) -> dict | None:
    near_player = args.get("near_player")
    offset = args.get("offset", "~ ~ ~")
    if not _COORD_RE.match(offset):
        logger.warning(f"Blocked invalid lightning offset: {offset}")
        return None
    return _cmd(f"summon minecraft:lightning_bolt {offset}", target_player=near_player)


_SOUND_RE = re.compile(r"^[a-z0-9_.:/-]+$")


def _play_sound(args: dict) -> dict | None:
    sound = args.get("sound", "")
    target = args.get("target_player", "@a")
    if not _validate_player_target(target):
        return None
    sound = sound if sound.startswith("minecraft:") else f"minecraft:{sound}"
    if not _SOUND_RE.match(sound):
        logger.warning(f"Blocked invalid sound: {sound}")
        return None
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
    if not _validate_player_target(player):
        return []
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


# Direction offsets: (dx, dz) per unit distance
_DIRECTION_OFFSETS = {
    "N": (0, -1), "S": (0, 1), "E": (1, 0), "W": (-1, 0),
    "NE": (1, -1), "SE": (1, 1), "SW": (-1, 1), "NW": (-1, -1),
}

# Distance presets in blocks
_DISTANCE_BLOCKS = {"near": 10, "medium": 25, "far": 50}

def _build_schematic(args: dict, player_context: dict | None = None) -> dict | None:
    blueprint_id = args.get("blueprint_id", "")
    near_player = args.get("near_player", "")
    in_front = args.get("in_front", True)  # default to in_front
    direction = args.get("direction", "N")
    distance_key = args.get("distance", "near")
    rotation = args.get("rotation", 0)

    if not near_player:
        logger.warning("build_schematic missing near_player")
        return None

    # Look up player position
    player_data = None
    if player_context:
        player_data = player_context.get(near_player.lower())

    if not player_data:
        logger.warning(f"build_schematic: no position data for player '{near_player}'")
        return None

    px, py, pz = player_data["x"], player_data["y"], player_data["z"]
    facing = player_data.get("facing", "N")

    # Resolve direction
    if in_front:
        # Use player's facing direction; fall back to N if facing is unrecognized
        compass = facing if facing in _DIRECTION_OFFSETS else "N"
    else:
        compass = direction.upper()
        if compass not in _DIRECTION_OFFSETS:
            logger.warning(f"build_schematic: invalid direction '{direction}'")
            compass = "N"

    # Resolve distance
    dist = _DISTANCE_BLOCKS.get(distance_key, 10)

    # Compute target coordinates
    dx, dz = _DIRECTION_OFFSETS[compass]
    # For diagonals, scale so total distance matches (dx and dz are +-1)
    if abs(dx) + abs(dz) == 2:
        # Diagonal: use 0.7 * dist on each axis (~same total distance)
        x = int(px + dx * int(dist * 0.7))
        z = int(pz + dz * int(dist * 0.7))
    else:
        x = int(px + dx * dist)
        z = int(pz + dz * dist)
    y = py  # ground level at player's Y

    logger.info(f"build_schematic: resolved {near_player} "
                f"({'in_front' if in_front else compass} {distance_key}) "
                f"-> ({x}, {y}, {z})")

    return build_schematic_command(blueprint_id, x, y, z, rotation)


def get_schematic_tool_results(tool_calls: list) -> dict[str, str]:
    """Process schematic search tool calls and return tool results.

    Returns a dict mapping tool_call_id -> result text.
    These are meant to be injected back into the conversation as tool results
    for a follow-up LLM call.
    """
    results = {}
    for tc in tool_calls:
        name = tc.function.name
        try:
            args = json.loads(tc.function.arguments)
        except (json.JSONDecodeError, AttributeError) as e:
            logger.warning(f"Failed to parse arguments for {name}: {e}")
            results[tc.id] = f"ERROR: Could not parse tool arguments. Please try again with valid JSON."
            continue

        if name == "search_schematics":
            query = args.get("query", "")
            results[tc.id] = search_schematics(query)

    return results
