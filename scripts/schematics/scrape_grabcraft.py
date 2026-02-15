#!/usr/bin/env python3
"""
GrabCraft → .schem pipeline for minecraft-god.

Scrapes GrabCraft blueprints, maps legacy block names to modern minecraft: IDs,
and outputs Sponge Schematic v2 (.schem) files + a catalog index JSON.

Usage:
    # Scrape sitemap and build URL index
    python scrape_grabcraft.py index

    # Fetch all blueprints (or a subset by category)
    python scrape_grabcraft.py fetch [--category buildings/churches] [--limit 50]

    # Convert fetched blueprints to .schem files
    python scrape_grabcraft.py convert [--limit 50]

    # Generate catalog index JSON
    python scrape_grabcraft.py catalog

    # Full pipeline: index → fetch → convert → catalog
    python scrape_grabcraft.py all [--category buildings/churches] [--limit 50]
"""

import argparse
import csv
import gzip
import io
import json
import os
import re
import struct
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
import nbtlib

# -- Paths ------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / "data"
SCHEM_DIR = SCRIPT_DIR / "schematics"
BLOCKMAP_FILE = SCRIPT_DIR / "blockmap_raw.csv"
INDEX_FILE = DATA_DIR / "url_index.json"
BLUEPRINTS_DIR = DATA_DIR / "blueprints"
CATALOG_FILE = SCHEM_DIR / "catalog.json"
UNMAPPED_LOG = DATA_DIR / "unmapped_blocks.json"

# Minecraft 1.21 data version
DATA_VERSION = 3953

GRABCRAFT_BASE = "https://www.grabcraft.com"
SITEMAP_URL = f"{GRABCRAFT_BASE}/sitemap.xml"

# Rate limiting
REQUEST_DELAY = 0.5  # seconds between requests


# -- Block Mapping ----------------------------------------------------------

class BlockMapper:
    """Maps GrabCraft legacy block names to modern minecraft: block states."""

    def __init__(self, blockmap_path: Path):
        self.mappings: dict[str, str] = {}  # grabcraft_name -> minecraft:id[state]
        self.unmapped: dict[str, int] = {}  # track unmapped blocks + counts
        self._load_blockmap(blockmap_path)

    def _load_blockmap(self, path: Path):
        """Load the GrabcraftLitematic blockmap.csv."""
        if not path.exists():
            print(f"Warning: blockmap not found at {path}")
            return

        with open(path, "r") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                parts = line.split("\t")
                # Filter: need at least grabcraft_name and minecraft:id
                grabcraft_name = parts[0].strip()
                if not grabcraft_name or len(parts) < 2:
                    continue
                mc_id = parts[1].strip()
                if not mc_id:
                    continue

                # Parse block state properties (pairs in columns 3,4  5,6  etc.)
                state_props = {}
                i = 2
                while i + 1 < len(parts):
                    key = parts[i].strip()
                    val = parts[i + 1].strip()
                    if key and val:
                        state_props[key] = val
                    i += 2

                # Build the full block state string
                if state_props:
                    props_str = ",".join(
                        f"{k}={v}" for k, v in sorted(state_props.items())
                    )
                    full_id = f"{mc_id}[{props_str}]"
                else:
                    full_id = mc_id

                self.mappings[grabcraft_name] = full_id

        print(f"Loaded {len(self.mappings)} block mappings")

    def _auto_map(self, grabcraft_name: str) -> str:
        """Auto-map a GrabCraft block name to a minecraft: ID with block states.

        Handles common patterns like:
        - "Oak Wood Plank" -> "minecraft:oak_planks"
        - "Spruce Wood Slab (Upper)" -> "minecraft:spruce_slab[type=top]"
        - "Oak Wood Stairs (East, Upside-down)" -> "minecraft:oak_stairs[facing=east,half=top]"
        """
        name = grabcraft_name
        states = {}

        # Extract and parse parenthetical state info
        paren_match = re.match(r'^(.+?)\s*\((.+)\)$', name)
        if paren_match:
            name = paren_match.group(1)
            state_str = paren_match.group(2)
            parts = [s.strip() for s in state_str.split(",")]

            for part in parts:
                pl = part.lower()
                # Slab half
                if pl in ("upper", "top"):
                    states["type"] = "top"
                elif pl in ("bottom", "lower"):
                    states["type"] = "bottom"
                elif pl == "double":
                    states["type"] = "double"
                # Stair half
                elif pl == "upside-down":
                    states["half"] = "top"
                elif pl == "normal":
                    states["half"] = "bottom"
                # Facing directions
                elif pl in ("north", "south", "east", "west"):
                    states["facing"] = pl
                elif pl.startswith("facing "):
                    states["facing"] = pl.replace("facing ", "")
                # Door/trapdoor
                elif pl in ("open", "opened"):
                    states["open"] = "true"
                elif pl in ("closed",):
                    states["open"] = "false"
                elif pl in ("powered",):
                    states["powered"] = "true"
                elif pl in ("unpowered",):
                    states["powered"] = "false"
                # Axis
                elif pl.startswith("facing north/south"):
                    states["axis"] = "z"
                elif pl.startswith("facing east/west"):
                    states["axis"] = "x"
                elif pl.startswith("facing up/down"):
                    states["axis"] = "y"
                # Lit
                elif pl == "lit":
                    states["lit"] = "true"
                elif pl in ("not lit", "unlit"):
                    states["lit"] = "false"
                # Active
                elif pl in ("active", "not active"):
                    pass  # Rail powered state, skip for now

        name = name.lower().strip()
        name = name.replace(" ", "_")

        # Common renames
        replacements = {
            "_wood_plank": "_planks",
            "_wood_slab": "_slab",
            "_wood_stairs": "_stairs",
            "_wood_fence": "_fence",
            "_wood_door": "_door",
            "wood_": "",
            "wall-mounted_": "",
            "stained_clay": "terracotta",
            "stained_hardened_clay": "terracotta",
            "hardened_clay": "terracotta",
            "stone_brick_": "stone_bricks_",
            "mossy_stone_brick_": "mossy_stone_bricks_",
        }
        for old, new in replacements.items():
            name = name.replace(old, new)

        mc_id = f"minecraft:{name}"
        if states:
            props = ",".join(f"{k}={v}" for k, v in sorted(states.items()))
            return f"{mc_id}[{props}]"
        return mc_id

    def map_block(self, grabcraft_name: str) -> str:
        """Map a GrabCraft block name to a minecraft: block state string."""
        # Strip leading/trailing whitespace (GrabCraft data quirk)
        name = grabcraft_name.strip()

        if name in self.mappings:
            return self.mappings[name]

        # Handle truncated slab names like "(Jungle Wood, Bottom)"
        # These are missing the "Slab" prefix in GrabCraft's data
        slab_match = re.match(r'^\((.+?),\s*(Bottom|Upper|Top|Double)\)$', name)
        if slab_match:
            wood_type = slab_match.group(1).strip().lower().replace(" ", "_")
            half = slab_match.group(2).lower()
            # "wood" -> remove "wood" suffix pattern
            wood_type = re.sub(r'_wood$', '', wood_type)
            slab_type = "top" if half in ("upper", "top") else "bottom"
            return f"minecraft:{wood_type}_slab[type={slab_type}]"

        # Try auto-mapping
        mapped = self._auto_map(name)
        self.unmapped[name] = self.unmapped.get(name, 0) + 1
        return mapped

    def save_unmapped(self, path: Path):
        """Save unmapped blocks to a JSON file for review."""
        if self.unmapped:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Sort by count descending
            sorted_unmapped = dict(
                sorted(self.unmapped.items(), key=lambda x: -x[1])
            )
            with open(path, "w") as f:
                json.dump(sorted_unmapped, f, indent=2)
            print(f"Saved {len(self.unmapped)} unmapped block types to {path}")


# -- Sponge Schematic Writer ------------------------------------------------

def _encode_varint(value: int) -> bytes:
    """Encode an integer as a varint (variable-length integer)."""
    result = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            byte |= 0x80
        result.append(byte)
        if not value:
            break
    return bytes(result)


def write_schem(
    blocks: list[dict],
    dims: tuple[int, int, int],
    name: str,
    output_path: Path,
):
    """Write blocks to a Sponge Schematic v2 (.schem) file.

    Args:
        blocks: List of dicts with keys: x, y, z, block_state (minecraft:id[props])
        dims: (width, height, length) = (x, y, z) dimensions
        name: Schematic name
        output_path: Path to write .schem file
    """
    width, height, length = dims

    # Build palette and block data
    palette = {}  # block_state_str -> palette_index
    palette_index = 0

    # Pre-fill air as index 0
    palette["minecraft:air"] = 0
    palette_index = 1

    # Build a 3D grid initialized to air (index 0)
    grid = [0] * (width * height * length)

    for block in blocks:
        x, y, z = block["x"], block["y"], block["z"]
        state = block["block_state"]

        if x < 0 or x >= width or y < 0 or y >= height or z < 0 or z >= length:
            continue

        if state not in palette:
            palette[state] = palette_index
            palette_index += 1

        # Index = x + z * Width + y * Width * Length
        idx = x + z * width + y * width * length
        grid[idx] = palette[state]

    # Encode block data as varints
    block_data = bytearray()
    for idx in grid:
        block_data.extend(_encode_varint(idx))

    # Build NBT structure
    schematic = nbtlib.Compound({
        "Version": nbtlib.Int(2),
        "DataVersion": nbtlib.Int(DATA_VERSION),
        "Width": nbtlib.Short(width),
        "Height": nbtlib.Short(height),
        "Length": nbtlib.Short(length),
        "PaletteMax": nbtlib.Int(len(palette)),
        "Palette": nbtlib.Compound({
            state: nbtlib.Int(idx) for state, idx in palette.items()
        }),
        "BlockData": nbtlib.ByteArray(block_data),
        "Metadata": nbtlib.Compound({
            "Name": nbtlib.String(name),
        }),
    })

    root = nbtlib.File(schematic, gzipped=True, root_name="Schematic")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    root.save(output_path)


# -- GrabCraft Scraper -------------------------------------------------------

def scrape_sitemap() -> list[dict]:
    """Scrape GrabCraft sitemap.xml and return a list of blueprint entries."""
    print(f"Fetching sitemap from {SITEMAP_URL}...")
    client = httpx.Client(timeout=30, follow_redirects=True)
    resp = client.get(SITEMAP_URL)
    resp.raise_for_status()

    # Parse XML
    root = ET.fromstring(resp.text)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

    entries = []
    for url_elem in root.findall("sm:url", ns):
        loc = url_elem.find("sm:loc", ns)
        if loc is None:
            continue
        url = loc.text.strip()

        # Filter: only blueprint pages (format: /minecraft/name/category)
        path = url.replace(GRABCRAFT_BASE, "")
        parts = path.strip("/").split("/")
        if len(parts) != 3 or parts[0] != "minecraft":
            continue

        slug = parts[1]
        category = parts[2]
        entries.append({
            "url": url,
            "slug": slug,
            "category": category,
        })

    print(f"Found {len(entries)} blueprint URLs")
    client.close()
    return entries


def fetch_blueprint(url: str, client: httpx.Client) -> dict | None:
    """Fetch a single blueprint's metadata + render object data.

    Returns dict with: name, slug, category, dims, tags, block_count,
                       skill_level, author, render_object (raw JSON dict)
    """
    try:
        # Fetch the detail page
        page_url = url if "#" not in url else url[:url.find("#")]
        resp = client.get(page_url + "#general")
        resp.raise_for_status()
        html = resp.text

        # Find render object JS filename
        ro_match = re.search(r'(myRenderObject_\d+\.js)', html)
        if not ro_match:
            return None
        ro_filename = ro_match.group(1)
        ro_url = f"{GRABCRAFT_BASE}/js/RenderObject/{ro_filename}"

        # Extract name
        name_match = re.search(r'content-title[^>]*>([^<]+)<', html)
        name = name_match.group(1).strip() if name_match else "Unknown"

        # Extract metadata using CSS class selectors in the properties table
        def extract_by_class(css_class: str) -> str | None:
            pattern = rf'class="[^"]*{css_class}[^"]*"[^>]*>([^<]+)<'
            m = re.search(pattern, html)
            return m.group(1).strip() if m else None

        width = int(extract_by_class("dimension-x") or "0")
        height = int(extract_by_class("dimension-y") or "0")
        depth = int(extract_by_class("dimension-z") or "0")

        tags_str = extract_by_class("tags")
        tags = [t.strip() for t in tags_str.split(",")] if tags_str else []

        block_count = int(extract_by_class("block_count") or "0")

        skill_str = extract_by_class("skill_level")
        skill_level = int(skill_str) if skill_str and skill_str.isdigit() else 0

        # Extract author
        author_match = re.search(r'Author:&nbsp;([^<]+)', html)
        author = author_match.group(1).strip() if author_match else "Unknown"

        # Fetch render object data
        time.sleep(REQUEST_DELAY)
        ro_resp = client.get(ro_url)
        ro_resp.raise_for_status()
        ro_text = ro_resp.text

        # Parse: strip "var myRenderObject = " prefix
        json_start = ro_text.find("{")
        if json_start == -1:
            return None
        ro_json = json.loads(ro_text[json_start:])

        return {
            "name": name,
            "dims": [width, height, depth],
            "tags": tags,
            "block_count": block_count,
            "skill_level": skill_level,
            "author": author,
            "render_object": ro_json,
        }

    except Exception as e:
        print(f"  Error fetching {url}: {e}")
        return None


def parse_render_object(ro_json: dict) -> list[dict]:
    """Parse GrabCraft render object JSON into a list of block dicts.

    Returns list of: {x, y, z, name} where name is the GrabCraft block name.
    The coordinate system in GrabCraft is [y][x][z] with 1-based indexing.
    """
    blocks = []
    for y_key, xz_data in ro_json.items():
        for x_key, z_data in xz_data.items():
            for z_key, block_info in z_data.items():
                blocks.append({
                    "x": int(block_info["x"]) - 1,  # 1-based → 0-based
                    "y": int(block_info["y"]) - 1,
                    "z": int(block_info["z"]) - 1,
                    "name": block_info["name"],
                })
    return blocks


# -- CLI Commands ------------------------------------------------------------

def cmd_index(args):
    """Scrape sitemap and save URL index."""
    entries = scrape_sitemap()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(INDEX_FILE, "w") as f:
        json.dump(entries, f, indent=2)
    print(f"Saved {len(entries)} entries to {INDEX_FILE}")

    # Print category summary
    categories: dict[str, int] = {}
    for e in entries:
        categories[e["category"]] = categories.get(e["category"], 0) + 1
    print("\nCategories:")
    for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count}")


def cmd_fetch(args):
    """Fetch blueprint data from GrabCraft."""
    if not INDEX_FILE.exists():
        print("No URL index found. Run 'index' first.")
        return

    with open(INDEX_FILE) as f:
        entries = json.load(f)

    # Filter by category if specified
    if args.category:
        entries = [e for e in entries if args.category in e["category"]]
        print(f"Filtered to {len(entries)} entries matching '{args.category}'")

    # Limit
    if args.limit:
        entries = entries[:args.limit]

    BLUEPRINTS_DIR.mkdir(parents=True, exist_ok=True)

    # Check what we already have
    existing = {p.stem for p in BLUEPRINTS_DIR.glob("*.json")}
    to_fetch = [e for e in entries if e["slug"] not in existing]
    print(f"Need to fetch {len(to_fetch)} blueprints ({len(existing)} already cached)")

    client = httpx.Client(
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": "minecraft-god-schematic-pipeline/1.0"},
    )

    fetched = 0
    errors = 0
    for i, entry in enumerate(to_fetch):
        print(f"[{i+1}/{len(to_fetch)}] Fetching {entry['slug']}...", end=" ")
        bp = fetch_blueprint(entry["url"], client)
        if bp:
            bp["url"] = entry["url"]
            bp["slug"] = entry["slug"]
            bp["category"] = entry["category"]
            out_path = BLUEPRINTS_DIR / f"{entry['slug']}.json"
            with open(out_path, "w") as f:
                json.dump(bp, f)
            print(f"OK ({bp['block_count']} blocks, {bp['dims']})")
            fetched += 1
        else:
            print("FAILED")
            errors += 1

        time.sleep(REQUEST_DELAY)

    client.close()
    print(f"\nFetched {fetched}, errors {errors}, total cached {len(existing) + fetched}")


def cmd_convert(args):
    """Convert fetched blueprints to .schem files."""
    if not BLUEPRINTS_DIR.exists():
        print("No blueprints found. Run 'fetch' first.")
        return

    mapper = BlockMapper(BLOCKMAP_FILE)

    bp_files = sorted(BLUEPRINTS_DIR.glob("*.json"))
    if args.limit:
        bp_files = bp_files[:args.limit]

    SCHEM_DIR.mkdir(parents=True, exist_ok=True)

    converted = 0
    errors = 0
    for i, bp_path in enumerate(bp_files):
        slug = bp_path.stem
        schem_path = SCHEM_DIR / f"{slug}.schem"

        # Skip if already converted
        if schem_path.exists() and not args.force:
            continue

        print(f"[{i+1}/{len(bp_files)}] Converting {slug}...", end=" ")
        try:
            with open(bp_path) as f:
                bp = json.load(f)

            # Parse render object blocks
            raw_blocks = parse_render_object(bp["render_object"])

            # Map block names
            mapped_blocks = []
            for block in raw_blocks:
                block_state = mapper.map_block(block["name"])
                mapped_blocks.append({
                    "x": block["x"],
                    "y": block["y"],
                    "z": block["z"],
                    "block_state": block_state,
                })

            dims = tuple(bp["dims"])
            if dims[0] <= 0 or dims[1] <= 0 or dims[2] <= 0:
                print(f"SKIP (invalid dims {dims})")
                errors += 1
                continue

            write_schem(mapped_blocks, dims, bp["name"], schem_path)
            print(f"OK ({len(mapped_blocks)} blocks → {schem_path.name})")
            converted += 1

        except Exception as e:
            print(f"ERROR: {e}")
            errors += 1

    mapper.save_unmapped(UNMAPPED_LOG)
    print(f"\nConverted {converted}, errors {errors}")


def cmd_catalog(args):
    """Generate catalog index JSON from converted schematics."""
    if not BLUEPRINTS_DIR.exists():
        print("No blueprints found. Run 'fetch' first.")
        return

    # Load existing catalog to preserve non-GrabCraft entries (e.g. Minemev)
    if CATALOG_FILE.exists():
        with open(CATALOG_FILE) as f:
            catalog = json.load(f)
        # Strip existing GrabCraft entries (no "source" field) — they'll be regenerated
        for cat_data in catalog["categories"].values():
            cat_data["blueprints"] = [
                bp for bp in cat_data["blueprints"]
                if bp.get("source")
            ]
    else:
        catalog = {"categories": {}}

    bp_files = sorted(BLUEPRINTS_DIR.glob("*.json"))
    entries_added = 0

    for bp_path in bp_files:
        slug = bp_path.stem
        schem_path = SCHEM_DIR / f"{slug}.schem"
        if not schem_path.exists():
            continue

        with open(bp_path) as f:
            bp = json.load(f)

        category = bp.get("category", "other")

        if category not in catalog["categories"]:
            catalog["categories"][category] = {"blueprints": []}

        catalog["categories"][category]["blueprints"].append({
            "id": slug,
            "name": bp["name"],
            "description": ", ".join(bp.get("tags", [])),
            "dimensions": {
                "w": bp["dims"][0],
                "h": bp["dims"][1],
                "d": bp["dims"][2],
            },
            "block_count": bp.get("block_count", 0),
            "tags": bp.get("tags", []),
            "author": bp.get("author", "Unknown"),
            "skill_level": bp.get("skill_level", 0),
            "file": f"{slug}.schem",
        })
        entries_added += 1

    # Sort categories, update counts, remove empties
    for cat in catalog["categories"].values():
        cat["blueprints"].sort(key=lambda b: b["name"])
        cat["count"] = len(cat["blueprints"])
    catalog["categories"] = {
        k: v for k, v in catalog["categories"].items()
        if v["count"] > 0
    }

    total = sum(c["count"] for c in catalog["categories"].values())
    preserved = total - entries_added

    SCHEM_DIR.mkdir(parents=True, exist_ok=True)
    with open(CATALOG_FILE, "w") as f:
        json.dump(catalog, f, indent=2)
    print(f"Catalog: {total} blueprints across {len(catalog['categories'])} categories")
    print(f"  GrabCraft: {entries_added} entries (regenerated)")
    if preserved:
        print(f"  Other sources: {preserved} entries (preserved)")
    print(f"Saved to {CATALOG_FILE}")

    # Print summary
    for cat_name, cat_data in sorted(
        catalog["categories"].items(), key=lambda x: -x[1]["count"]
    ):
        print(f"  {cat_name}: {cat_data['count']}")


def cmd_all(args):
    """Run full pipeline: index → fetch → convert → catalog."""
    print("=== Step 1: Index ===")
    cmd_index(args)
    print("\n=== Step 2: Fetch ===")
    cmd_fetch(args)
    print("\n=== Step 3: Convert ===")
    cmd_convert(args)
    print("\n=== Step 4: Catalog ===")
    cmd_catalog(args)


def main():
    parser = argparse.ArgumentParser(description="GrabCraft → .schem pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # index
    subparsers.add_parser("index", help="Scrape sitemap and build URL index")

    # fetch
    fetch_p = subparsers.add_parser("fetch", help="Fetch blueprint data")
    fetch_p.add_argument("--category", help="Filter by category slug")
    fetch_p.add_argument("--limit", type=int, help="Max blueprints to fetch")

    # convert
    conv_p = subparsers.add_parser("convert", help="Convert to .schem files")
    conv_p.add_argument("--limit", type=int, help="Max to convert")
    conv_p.add_argument("--force", action="store_true", help="Re-convert existing")

    # catalog
    subparsers.add_parser("catalog", help="Generate catalog index JSON")

    # all
    all_p = subparsers.add_parser("all", help="Run full pipeline")
    all_p.add_argument("--category", help="Filter by category slug")
    all_p.add_argument("--limit", type=int, help="Max blueprints to fetch")
    all_p.add_argument("--force", action="store_true", help="Re-convert existing")

    args = parser.parse_args()

    commands = {
        "index": cmd_index,
        "fetch": cmd_fetch,
        "convert": cmd_convert,
        "catalog": cmd_catalog,
        "all": cmd_all,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
