"""Translate LLM tool calls into Minecraft commands.

All commands go through an allowlist. If something isn't in the list, it gets dropped.
Tool call arguments are validated through pydantic models with helpful error messages.
Player names are validated against the server whitelist.
"""

import json
import logging
import re

from pydantic import BaseModel, Field, ValidationError, field_validator
from typing import Literal

from server.config import WHITELIST_FILE
from server.schematics import search_schematics, build_schematic_command

logger = logging.getLogger("minecraft-god")

# Allowlisted command prefixes — anything not starting with one of these is dropped
ALLOWED_COMMANDS = {
    "summon", "say", "tellraw", "weather", "effect", "tp", "teleport",
    "give", "clear", "playsound", "time", "difficulty", "setblock", "fill",
}

# Chat prefixes for each god — used by tellraw for attributed messages
GOD_CHAT_STYLE = {
    "kind_god": {"name": "The Kind God", "color": "gold"},
    "deep_god": {"name": "???", "color": "dark_red"},
    "herald": {"name": "The Herald", "color": "green"},
    "dig_god": {"name": "The God of Digging", "color": "dark_aqua"},
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

# Regex for valid player names (Java Edition: alphanumeric + underscores, 3-16 chars)
_PLAYER_NAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,16}$")
# Regex for valid coordinates (numbers, ~, ^, -, .)
_COORD_RE = re.compile(r"^[~^0-9. -]+$")
# Regex for valid item names
_ITEM_NAME_RE = re.compile(r"^[a-z0-9_]+$")
# Regex for valid sound IDs
_SOUND_RE = re.compile(r"^[a-z0-9_.:/-]+$")


# ─── Whitelist loading ────────────────────────────────────────────────────────

_whitelist_cache: set[str] | None = None
_whitelist_mtime: float = 0


def get_whitelist_names() -> set[str]:
    """Load player names from the Paper server whitelist, cached until file changes."""
    global _whitelist_cache, _whitelist_mtime
    try:
        stat = WHITELIST_FILE.stat()
        if _whitelist_cache is not None and stat.st_mtime == _whitelist_mtime:
            return _whitelist_cache
        data = json.loads(WHITELIST_FILE.read_text())
        _whitelist_cache = {entry["name"] for entry in data if "name" in entry}
        _whitelist_mtime = stat.st_mtime
        logger.info(f"Loaded whitelist: {sorted(_whitelist_cache)}")
        return _whitelist_cache
    except (OSError, json.JSONDecodeError, KeyError):
        logger.warning("Failed to load whitelist — player name validation disabled")
        return set()


def _check_player_target(target: str, whitelist: set[str]) -> None:
    """Validate a player name or target selector against the whitelist.

    Raises ValueError with a helpful message if invalid.
    Target selectors (@a, @s, @p) are always allowed.
    """
    if target in ALLOWED_SELECTORS:
        return
    if not _PLAYER_NAME_RE.match(target):
        raise ValueError(
            f"Invalid player target '{target}'. "
            f"Use a player name or one of: {', '.join(sorted(ALLOWED_SELECTORS))}")
    if whitelist:
        whitelist_lower = {n.lower(): n for n in whitelist}
        if target.lower() not in whitelist_lower:
            raise ValueError(
                f"Player '{target}' is not on the whitelist. "
                f"Whitelisted players: {', '.join(sorted(whitelist))}")


# ─── Pydantic models for tool call arguments ─────────────────────────────────


class SendMessageParams(BaseModel):
    message: str = Field(min_length=1)
    target_player: str | None = None


class SummonMobParams(BaseModel):
    mob_type: str
    near_player: str | None = None
    location: str = "~ ~ ~"
    count: int = 1

    @field_validator('mob_type', mode='before')
    @classmethod
    def normalize_mob(cls, v):
        return str(v).lower().replace("minecraft:", "")

    @field_validator('mob_type')
    @classmethod
    def validate_mob(cls, v):
        if v not in VALID_MOBS:
            raise ValueError(
                f"Invalid mob type '{v}'. "
                f"Valid types: {', '.join(sorted(VALID_MOBS))}")
        return v

    @field_validator('location')
    @classmethod
    def validate_location(cls, v):
        if not _COORD_RE.match(v):
            raise ValueError(
                f"Invalid location '{v}'. "
                f"Use coordinates like '~ ~ ~' or '100 64 -200'.")
        return v

    @field_validator('count', mode='before')
    @classmethod
    def clamp_count(cls, v):
        try:
            return max(1, min(int(v) if v is not None else 1, 5))
        except (TypeError, ValueError):
            return 1


class ChangeWeatherParams(BaseModel):
    weather_type: Literal["clear", "rain", "thunder"]
    duration: int = 6000

    @field_validator('weather_type', mode='before')
    @classmethod
    def normalize_weather(cls, v):
        return str(v).lower() if isinstance(v, str) else v

    @field_validator('duration', mode='before')
    @classmethod
    def clamp_duration(cls, v):
        try:
            return max(1, min(int(v) if v is not None else 6000, 24000))
        except (TypeError, ValueError):
            return 6000


class GiveEffectParams(BaseModel):
    target_player: str = "@a"
    effect: str
    duration: int = 30
    amplifier: int = 0

    @field_validator('effect', mode='before')
    @classmethod
    def normalize_effect(cls, v):
        return str(v).lower() if isinstance(v, str) else v

    @field_validator('effect')
    @classmethod
    def validate_effect(cls, v):
        if v not in VALID_EFFECTS:
            raise ValueError(
                f"Invalid effect '{v}'. "
                f"Valid effects: {', '.join(sorted(VALID_EFFECTS))}")
        return v

    @field_validator('duration', mode='before')
    @classmethod
    def clamp_duration(cls, v):
        try:
            return max(1, min(int(v) if v is not None else 30, 120))
        except (TypeError, ValueError):
            return 30

    @field_validator('amplifier', mode='before')
    @classmethod
    def clamp_amplifier(cls, v):
        try:
            return max(0, min(int(v) if v is not None else 0, 3))
        except (TypeError, ValueError):
            return 0


class SetTimeParams(BaseModel):
    time: Literal["day", "noon", "sunset", "night", "midnight", "sunrise"]

    @field_validator('time', mode='before')
    @classmethod
    def normalize_time(cls, v):
        return str(v).lower() if isinstance(v, str) else v


class GiveItemParams(BaseModel):
    player: str = "@a"
    item: str
    count: int = 1

    @field_validator('item', mode='before')
    @classmethod
    def normalize_item(cls, v):
        return str(v).lower().replace("minecraft:", "") if isinstance(v, str) else v

    @field_validator('item')
    @classmethod
    def validate_item(cls, v):
        if not _ITEM_NAME_RE.match(v):
            raise ValueError(
                f"Invalid item name '{v}'. "
                f"Item names must be lowercase alphanumeric with underscores.")
        if v in BLOCKED_ITEMS:
            raise ValueError(
                f"'{v}' is a restricted item and cannot be given to players.")
        return v

    @field_validator('count', mode='before')
    @classmethod
    def clamp_count(cls, v):
        try:
            return max(1, min(int(v) if v is not None else 1, 64))
        except (TypeError, ValueError):
            return 1


class ClearItemParams(BaseModel):
    player: str = "@a"
    item: str | None = None

    @field_validator('item', mode='before')
    @classmethod
    def normalize_item(cls, v):
        if not v or v == "":
            return None
        return str(v).lower().replace("minecraft:", "")

    @field_validator('item')
    @classmethod
    def validate_item(cls, v):
        if v is not None and not _ITEM_NAME_RE.match(v):
            raise ValueError(
                f"Invalid item name '{v}'. "
                f"Item names must be lowercase alphanumeric with underscores.")
        return v


class StrikeLightningParams(BaseModel):
    near_player: str
    offset: str = "~ ~ ~"

    @field_validator('offset')
    @classmethod
    def validate_offset(cls, v):
        if not _COORD_RE.match(v):
            raise ValueError(
                f"Invalid offset '{v}'. "
                f"Use coordinates like '~ ~ ~' or '~3 ~ ~'.")
        return v


class PlaySoundParams(BaseModel):
    sound: str
    target_player: str | None = None

    @field_validator('sound')
    @classmethod
    def validate_sound(cls, v):
        s = v if v.startswith("minecraft:") else f"minecraft:{v}"
        if not _SOUND_RE.match(s):
            raise ValueError(
                f"Invalid sound '{v}'. "
                f"Sound IDs must be lowercase with dots/underscores/colons.")
        return v


class SetDifficultyParams(BaseModel):
    difficulty: Literal["peaceful", "easy", "normal", "hard"]

    @field_validator('difficulty', mode='before')
    @classmethod
    def normalize_difficulty(cls, v):
        return str(v).lower() if isinstance(v, str) else v


class TeleportPlayerParams(BaseModel):
    player: str
    x: int
    y: int = 64
    z: int = 0


class AssignMissionParams(BaseModel):
    player: str
    mission_title: str
    mission_description: str = ""
    reward_hint: str = ""


class BuildSchematicParams(BaseModel):
    blueprint_id: str
    near_player: str | None = None
    in_front: bool = True
    direction: Literal["N", "S", "E", "W", "NE", "SE", "SW", "NW"] = "N"
    distance: Literal["near", "medium", "far"] = "near"
    rotation: Literal[0, 90, 180, 270] = 0

    @field_validator('direction', mode='before')
    @classmethod
    def normalize_direction(cls, v):
        valid = {"N", "S", "E", "W", "NE", "SE", "SW", "NW"}
        upper = str(v).upper() if isinstance(v, str) else v
        return upper if upper in valid else "N"

    @field_validator('rotation', mode='before')
    @classmethod
    def normalize_rotation(cls, v):
        try:
            return int(v) if v is not None else 0
        except (TypeError, ValueError):
            return 0


class DoNothingParams(BaseModel):
    reason: str = ""


# ─── Error formatting ─────────────────────────────────────────────────────────


def _format_validation_error(tool_name: str, error: ValidationError) -> str:
    """Format a pydantic ValidationError into a helpful LLM error message."""
    field_errors = []
    for e in error.errors():
        field = '.'.join(str(loc) for loc in e['loc'])
        msg = e['msg']
        # Strip pydantic's "Value error, " prefix for cleaner messages
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, "):]
        field_errors.append(f"  {field}: {msg}")
    return f"ERROR in {tool_name}:\n" + "\n".join(field_errors)


# ─── Main translation entry point ────────────────────────────────────────────


def translate_tool_calls(tool_calls: list, source: str = "kind_god",
                         player_context: dict | None = None,
                         requesting_player: str | None = None) -> tuple[list[dict], dict[str, str]]:
    """Convert LLM tool calls into a list of command dicts.

    Returns (commands, errors) where:
        - commands: list of command dicts (with "command" or "type" key)
        - errors: dict mapping tool_call_id -> error message for failed calls

    source: which god is speaking ("kind_god", "deep_god", "herald", "dig_god")
    player_context: dict mapping player names (lowercase) to their position/facing data,
        used for resolving build_schematic placement. Each entry has:
        {"x": int, "y": int, "z": int, "facing": str}
    requesting_player: the player who triggered this action (e.g. the praying player).
        Used as the default near_player for build_schematic when the LLM omits it.
    """
    whitelist = get_whitelist_names()

    commands = []
    errors = {}
    for tc in tool_calls:
        name = getattr(tc.function, 'name', '?')
        try:
            args = json.loads(tc.function.arguments)
            logger.info(f"[{source}] tool call: {name}({json.dumps(args, separators=(',', ':'))})")
            result = _translate_one(name, args, source,
                                    player_context=player_context,
                                    requesting_player=requesting_player,
                                    whitelist=whitelist)
            if result is None:
                continue  # do_nothing
            if isinstance(result, list):
                commands.extend(result)
            else:
                commands.append(result)
        except ValidationError as e:
            logger.warning(f"[{source}] {name} validation failed: {e}")
            errors[tc.id] = _format_validation_error(name, e)
        except ValueError as e:
            logger.warning(f"[{source}] {name} rejected: {e}")
            errors[tc.id] = f"ERROR: {e}"
        except Exception:
            logger.exception(f"Failed to translate tool call: {name}")
            errors[tc.id] = f"Internal error processing {name}"

    for cmd in commands:
        if cmd.get("type") == "build_schematic":
            logger.info(f"[{source}] => build_schematic: {cmd.get('blueprint_id')} at "
                        f"{cmd.get('x')},{cmd.get('y')},{cmd.get('z')} rot={cmd.get('rotation')}")
        else:
            cmd_str = cmd.get("command", "?")
            target = cmd.get("target_player")
            logger.info(f"[{source}] => cmd: {cmd_str[:120]}" + (f" (target: {target})" if target else ""))

    return commands, errors


# ─── Dispatcher ───────────────────────────────────────────────────────────────


def _translate_one(name: str, args: dict, source: str = "kind_god",
                   player_context: dict | None = None,
                   requesting_player: str | None = None,
                   whitelist: set[str] | None = None) -> dict | list[dict] | None:
    """Translate a single tool call. Raises ValidationError or ValueError on failure."""
    wl = whitelist or set()

    if name == "do_nothing":
        params = DoNothingParams(**args)
        logger.info(f"God chose to do nothing: {params.reason}")
        return None

    if name == "send_message":
        params = SendMessageParams(**args)
        if params.target_player is not None:
            _check_player_target(params.target_player, wl)
        return _send_message(params, source)
    elif name == "summon_mob":
        params = SummonMobParams(**args)
        if params.near_player is not None:
            _check_player_target(params.near_player, wl)
        return _summon_mob(params)
    elif name == "change_weather":
        params = ChangeWeatherParams(**args)
        return _change_weather(params)
    elif name == "give_effect":
        params = GiveEffectParams(**args)
        _check_player_target(params.target_player, wl)
        return _give_effect(params)
    elif name == "set_time":
        params = SetTimeParams(**args)
        return _set_time(params)
    elif name == "give_item":
        params = GiveItemParams(**args)
        _check_player_target(params.player, wl)
        return _give_item(params)
    elif name == "clear_item":
        params = ClearItemParams(**args)
        _check_player_target(params.player, wl)
        return _clear_item(params)
    elif name == "strike_lightning":
        params = StrikeLightningParams(**args)
        _check_player_target(params.near_player, wl)
        return _strike_lightning(params)
    elif name == "play_sound":
        params = PlaySoundParams(**args)
        if params.target_player is not None:
            _check_player_target(params.target_player, wl)
        return _play_sound(params)
    elif name == "set_difficulty":
        params = SetDifficultyParams(**args)
        return _set_difficulty(params)
    elif name == "teleport_player":
        params = TeleportPlayerParams(**args)
        _check_player_target(params.player, wl)
        return _teleport_player(params)
    elif name == "assign_mission":
        params = AssignMissionParams(**args)
        _check_player_target(params.player, wl)
        return _assign_mission(params, source)
    elif name == "build_schematic":
        params = BuildSchematicParams(**args)
        return _build_schematic(params, player_context=player_context,
                                requesting_player=requesting_player,
                                whitelist=wl)
    elif name == "undo_last_build":
        return {"type": "undo_last_build"}
    else:
        raise ValueError(f"Unknown tool '{name}'.")


# ─── Helpers ──────────────────────────────────────────────────────────────────


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


# Max chars per chat line — Minecraft chat is ~58 chars wide but the god name
# prefix takes ~20 chars, so we wrap message text at ~50 chars.  Only the first
# line gets the god name prefix; continuation lines are indented.
_CHAT_LINE_WIDTH = 50


def _wrap_message_lines(text: str, width: int = _CHAT_LINE_WIDTH) -> list[str]:
    """Split a message into chat-friendly lines.

    Splits on newlines first (preserving the god's paragraph structure),
    then word-wraps long lines within each paragraph.
    """
    lines = []
    for paragraph in text.split("\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        # Word-wrap long paragraphs
        while len(paragraph) > width:
            # Find last space within width
            split_at = paragraph.rfind(" ", 0, width)
            if split_at == -1:
                split_at = width  # no space found, hard-break
            lines.append(paragraph[:split_at].rstrip())
            paragraph = paragraph[split_at:].lstrip()
        if paragraph:
            lines.append(paragraph)
    return lines


# ─── Individual tool handlers ─────────────────────────────────────────────────


def _send_message(params: SendMessageParams, source: str = "kind_god") -> dict | list[dict] | None:
    god_style = GOD_CHAT_STYLE.get(source, {"name": "God", "color": "white"})

    lines = _wrap_message_lines(params.message)[:10]  # cap to prevent chat flooding
    if not lines:
        return None

    if params.target_player:
        # Private message — first line gets the god prefix, rest are continuation
        cmds = []
        for i, line in enumerate(lines):
            if i == 0:
                whisper_json = json.dumps([
                    {"text": "[whispered] ", "color": "gray", "italic": True},
                    {"text": f"<{god_style['name']}> ", "color": god_style["color"], "bold": True},
                    {"text": line, "color": "white"},
                ])
            else:
                whisper_json = json.dumps([
                    {"text": f"  {line}", "color": "white"},
                ])
            cmd = _cmd(f"tellraw {params.target_player} {whisper_json}")
            if cmd:
                cmds.append(cmd)
        # Notification to others (just once, not per-line)
        notify_json = json.dumps([
            {"text": f"<{god_style['name']}> ", "color": god_style["color"], "bold": True},
            {"text": f"*whispers to {params.target_player}*", "color": "gray", "italic": True},
        ])
        cmd = _cmd(f"tellraw @a[name=!{params.target_player}] {notify_json}")
        if cmd:
            cmds.append(cmd)
        return cmds
    else:
        # Public message — first line gets prefix, rest are continuation
        cmds = []
        for i, line in enumerate(lines):
            if i == 0:
                tellraw_json = json.dumps([
                    {"text": f"<{god_style['name']}> ", "color": god_style["color"], "bold": True},
                    {"text": line, "color": "white"},
                ])
            else:
                tellraw_json = json.dumps([
                    {"text": f"  {line}", "color": "white"},
                ])
            cmd = _cmd(f"tellraw @a {tellraw_json}")
            if cmd:
                cmds.append(cmd)
        return cmds


def _summon_mob(params: SummonMobParams) -> list[dict] | None:
    commands = []
    for _ in range(params.count):
        cmd = _cmd(f"summon minecraft:{params.mob_type} {params.location}",
                   target_player=params.near_player)
        if cmd:
            commands.append(cmd)
    return commands


def _change_weather(params: ChangeWeatherParams) -> dict | None:
    return _cmd(f"weather {params.weather_type} {params.duration}")


def _give_effect(params: GiveEffectParams) -> dict | None:
    return _cmd(
        f"effect give {params.target_player} minecraft:{params.effect} "
        f"{params.duration} {params.amplifier}")


def _set_time(params: SetTimeParams) -> dict | None:
    return _cmd(f"time set {params.time}")


def _give_item(params: GiveItemParams) -> dict | None:
    return _cmd(f"give {params.player} minecraft:{params.item} {params.count}")


def _clear_item(params: ClearItemParams) -> dict | None:
    if params.item:
        return _cmd(f"clear {params.player} minecraft:{params.item}")
    else:
        return _cmd(f"clear {params.player}")


def _strike_lightning(params: StrikeLightningParams) -> dict | None:
    return _cmd(f"summon minecraft:lightning_bolt {params.offset}",
                target_player=params.near_player)


def _play_sound(params: PlaySoundParams) -> dict | None:
    sound = params.sound if params.sound.startswith("minecraft:") else f"minecraft:{params.sound}"
    target = params.target_player or "@a"
    return _cmd(f"playsound {sound} master {target}")


def _set_difficulty(params: SetDifficultyParams) -> dict | None:
    return _cmd(f"difficulty {params.difficulty}")


def _teleport_player(params: TeleportPlayerParams) -> dict | None:
    return _cmd(f"tp {params.player} {params.x} {params.y} {params.z}")


def _assign_mission(params: AssignMissionParams, source: str = "kind_god") -> list[dict]:
    style = GOD_CHAT_STYLE.get(source, GOD_CHAT_STYLE["kind_god"])

    # Build quest announcement as tellraw
    parts = [
        {"text": f"<{style['name']}> ", "color": style["color"], "bold": True},
        {"text": f"Quest for {params.player}: ", "color": "yellow"},
        {"text": params.mission_title, "color": "gold", "bold": True},
    ]
    if params.mission_description:
        parts.append({"text": f" — {params.mission_description[:100]}", "color": "yellow"})
    if params.reward_hint:
        parts.append({"text": f" ({params.reward_hint})", "color": "gray", "italic": True})

    tellraw_json = json.dumps(parts)
    cmd = _cmd(f"tellraw @a {tellraw_json}")
    return [cmd] if cmd else []


# Direction offsets: (dx, dz) per unit distance
_DIRECTION_OFFSETS = {
    "N": (0, -1), "S": (0, 1), "E": (1, 0), "W": (-1, 0),
    "NE": (1, -1), "SE": (1, 1), "SW": (-1, 1), "NW": (-1, -1),
}

# Distance presets in blocks
_DISTANCE_BLOCKS = {"near": 10, "medium": 25, "far": 50}


def _build_schematic(params: BuildSchematicParams, player_context: dict | None = None,
                     requesting_player: str | None = None,
                     whitelist: set[str] | None = None) -> dict | None:
    near_player = params.near_player or requesting_player or ""

    if not near_player:
        raise ValueError(
            "build_schematic requires near_player. "
            "Specify which player to build near.")

    # Validate the player — but for build_schematic, if the LLM misspelled the
    # name and we have a requesting_player, fall back instead of hard-failing.
    try:
        _check_player_target(near_player, whitelist or set())
    except ValueError:
        if requesting_player and near_player != requesting_player:
            logger.info(f"build_schematic: '{near_player}' failed validation, "
                        f"falling back to requesting player '{requesting_player}'")
            near_player = requesting_player
            _check_player_target(near_player, whitelist or set())
        else:
            raise

    # Look up player position — fall back to requesting player if not found in context
    player_data = None
    if player_context:
        player_data = player_context.get(near_player.lower())
        if not player_data and requesting_player:
            fallback = player_context.get(requesting_player.lower())
            if fallback:
                logger.info(f"build_schematic: '{near_player}' not in player_context, "
                            f"falling back to requesting player '{requesting_player}'")
                player_data = fallback
                near_player = requesting_player

    if not player_data:
        raise ValueError(
            f"No position data for player '{near_player}'. "
            f"The player may be offline or position data is stale.")

    px, py, pz = player_data["x"], player_data["y"], player_data["z"]
    facing = player_data.get("facing", "N")

    # Resolve direction
    if params.in_front:
        compass = facing if facing in _DIRECTION_OFFSETS else "N"
    else:
        compass = params.direction
        if compass not in _DIRECTION_OFFSETS:
            compass = "N"

    # Resolve distance
    dist = _DISTANCE_BLOCKS.get(params.distance, 10)

    # Compute target coordinates
    dx, dz = _DIRECTION_OFFSETS[compass]
    if abs(dx) + abs(dz) == 2:
        x = int(px + dx * int(dist * 0.7))
        z = int(pz + dz * int(dist * 0.7))
    else:
        x = int(px + dx * dist)
        z = int(pz + dz * dist)
    y = py

    logger.info(f"build_schematic: resolved {near_player} "
                f"({'in_front' if params.in_front else compass} {params.distance}) "
                f"-> ({x}, {y}, {z})")

    result = build_schematic_command(params.blueprint_id, x, y, z, params.rotation)
    if result is None:
        raise ValueError(
            f"Blueprint '{params.blueprint_id}' not found. "
            f"Use search_schematics to find valid blueprint IDs, "
            f"then copy the exact ID from the search results.")
    return result


# ─── Schematic search (tool results, not commands) ───────────────────────────


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
