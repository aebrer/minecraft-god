#!/usr/bin/env python3
"""
Minemev → .schem pipeline for minecraft-god.

Downloads .litematic files from minemev.com (technical Minecraft schematics),
converts them to Sponge Schematic v2 (.schem) format, and merges them into
the shared catalog index JSON.

Usage:
    # Fetch all schematics (or filter by tag)
    python scrape_minemev.py fetch [--tag mob-farming] [--limit 50]

    # Convert fetched .litematic files to .schem
    python scrape_minemev.py convert [--limit 50]

    # Merge Minemev entries into the shared catalog.json
    python scrape_minemev.py catalog

    # Full pipeline: fetch → convert → catalog
    python scrape_minemev.py all [--tag mob-farming] [--limit 50]
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from litemapy import Schematic as LitematicSchematic

# Reuse the .schem writer from the GrabCraft pipeline
from scrape_grabcraft import write_schem, SCHEM_DIR, CATALOG_FILE

# -- Paths ------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / "data"
MINEMEV_DIR = DATA_DIR / "minemev"          # downloaded .litematic files
MINEMEV_INDEX = DATA_DIR / "minemev_index.json"  # post metadata cache

MINEMEV_API = "https://minemev.com/api"

# Rate limiting — start gentle, back off on errors
BASE_DELAY = 0.5       # seconds between requests
MAX_DELAY = 30.0       # max backoff
BACKOFF_FACTOR = 2.0   # multiply delay on consecutive errors
INDEX_SAVE_INTERVAL = 25  # save index every N fetches (crash recovery)


# -- Quality Filtering ------------------------------------------------------

def _has_latin_chars(text: str) -> bool:
    """Check if text contains any Latin alphabet characters."""
    return bool(re.search(r'[a-zA-Z]', text))


def _is_quality_entry(post: dict) -> bool:
    """Filter out entries with bad/missing metadata.

    Skips entries that:
    - Have no name or a name with zero Latin characters (pure CJK with no tags)
    - Have no tags AND no Latin characters in name (can't categorize or search)
    - Are from vendors we can't download from
    """
    name = post.get("post_name", "").strip()
    tags = post.get("tags", [])
    vendor = post.get("vendor", "")

    # Must have a name
    if not name:
        return False

    # Must be a vendor we know how to download from
    if vendor not in ("minemev", "redenmc", "choculaterie", "LitematicaGen"):
        return False

    # If name has no Latin characters, require at least one tag with Latin chars
    # (so the god can find and describe it)
    if not _has_latin_chars(name):
        if not any(_has_latin_chars(t) for t in tags):
            return False

    return True


# -- API Helpers ------------------------------------------------------------

def _curl_json(url: str, timeout: int = 30) -> dict | list | None:
    """Fetch JSON from a URL using curl (urllib gets 403'd by Cloudflare)."""
    try:
        result = subprocess.run(
            ["curl", "-s", "--fail", "-H", "Accept: application/json", url],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except Exception as e:
        print(f"  curl error: {e}")
        return None


def _curl_download(url: str, output_path: Path) -> bool:
    """Download a binary file using curl."""
    try:
        # URL-encode spaces (some vendors have spaces in filenames)
        safe_url = url.replace(" ", "%20")
        result = subprocess.run(
            ["curl", "-s", "--fail", "-L", "-o", str(output_path), safe_url],
            capture_output=True, text=True, timeout=120,
        )
        return result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0
    except Exception as e:
        print(f"  download error: {e}")
        return False


# -- Fetch ------------------------------------------------------------------

def cmd_fetch(args):
    """Fetch schematic metadata and .litematic files from Minemev."""
    MINEMEV_DIR.mkdir(parents=True, exist_ok=True)

    # Load existing index (for crash recovery)
    if MINEMEV_INDEX.exists():
        with open(MINEMEV_INDEX) as f:
            index = json.load(f)
    else:
        index = {}

    # Track which UUIDs we already have (by UUID stored in index)
    existing_uuids = {meta["uuid"] for meta in index.values() if "uuid" in meta}
    existing_files = {p.stem for p in MINEMEV_DIR.glob("*.litematic")}
    print(f"Already have {len(existing_files)} .litematic files cached, {len(index)} indexed")

    # Paginate through the search API
    page = 1
    total_pages = None
    fetched = 0
    errors = 0
    skipped = 0
    filtered = 0
    consecutive_errors = 0
    current_delay = BASE_DELAY

    while True:
        url = f"{MINEMEV_API}/search?page={page}&sort=downloads"
        if args.tag:
            url += f"&tag={args.tag}"

        data = _curl_json(url)
        if not data or "posts" not in data:
            consecutive_errors += 1
            current_delay = min(current_delay * BACKOFF_FACTOR, MAX_DELAY)
            print(f"  Failed to fetch page {page} (backoff: {current_delay:.1f}s)")
            if consecutive_errors >= 5:
                print(f"  Too many consecutive errors, stopping.")
                break
            time.sleep(current_delay)
            continue

        # Reset backoff on success
        consecutive_errors = 0
        current_delay = BASE_DELAY

        if total_pages is None:
            total_pages = data["total_pages"]
            total_items = data["total_items"]
            print(f"Found {total_items} schematics across {total_pages} pages")

        page_posts = data["posts"]
        if not page_posts:
            break

        for post in page_posts:
            uuid = post["uuid"]
            vendor = post["vendor"]
            name = post["post_name"]
            tags = post.get("tags", [])

            # If filtering by tag, verify the tag is actually present
            if args.tag and args.tag not in tags:
                continue

            # Skip if we already have this UUID
            if uuid in existing_uuids:
                skipped += 1
                continue

            # Quality filter
            if not _is_quality_entry(post):
                filtered += 1
                continue

            # Create a slug from the name
            slug = _make_slug(name, uuid)

            if slug in existing_files:
                skipped += 1
                existing_uuids.add(uuid)
                continue

            if args.limit and fetched >= args.limit:
                break

            print(f"[{fetched+1}] (p{page}) {name[:70]} ({vendor}/{uuid[:8]})...", end=" ", flush=True)

            # Get file listing
            time.sleep(current_delay)
            files_data = _curl_json(f"{MINEMEV_API}/files/{vendor}/{uuid}/")
            if not files_data:
                consecutive_errors += 1
                current_delay = min(current_delay * BACKOFF_FACTOR, MAX_DELAY)
                print(f"SKIP (files API failed, backoff: {current_delay:.1f}s)")
                errors += 1
                continue

            consecutive_errors = 0
            current_delay = BASE_DELAY

            # Find the first .litematic file
            lite_file = None
            for f in files_data:
                if f.get("file_type") == "litematic" and f.get("file"):
                    lite_file = f
                    break

            if not lite_file:
                print("SKIP (no .litematic)")
                skipped += 1
                existing_uuids.add(uuid)  # don't retry
                continue

            # Download it
            time.sleep(current_delay)
            output_path = MINEMEV_DIR / f"{slug}.litematic"
            if _curl_download(lite_file["file"], output_path):
                # Save metadata to index
                index[slug] = {
                    "uuid": uuid,
                    "vendor": vendor,
                    "name": name,
                    "tags": tags,
                    "downloads": post.get("downloads", 0),
                    "versions": post.get("versions", []),
                    "file_size": lite_file.get("file_size", 0),
                    "slug": slug,
                }
                existing_uuids.add(uuid)
                existing_files.add(slug)
                size_kb = lite_file.get("file_size", 0) / 1024
                print(f"OK ({size_kb:.1f}KB)")
                fetched += 1

                # Periodic index save for crash recovery
                if fetched % INDEX_SAVE_INTERVAL == 0:
                    with open(MINEMEV_INDEX, "w") as f_idx:
                        json.dump(index, f_idx, indent=2)
                    print(f"  [checkpoint: {len(index)} indexed]")
            else:
                consecutive_errors += 1
                current_delay = min(current_delay * BACKOFF_FACTOR, MAX_DELAY)
                print(f"DOWNLOAD FAILED (backoff: {current_delay:.1f}s)")
                errors += 1
                # Clean up partial download
                if output_path.exists():
                    output_path.unlink()

        if args.limit and fetched >= args.limit:
            print(f"\nReached limit of {args.limit}")
            break

        page += 1
        if total_pages and page > total_pages:
            break

        time.sleep(current_delay)

    # Final index save
    with open(MINEMEV_INDEX, "w") as f:
        json.dump(index, f, indent=2)

    print(f"\nDone! Fetched {fetched}, skipped {skipped}, filtered {filtered}, errors {errors}")
    print(f"Total indexed: {len(index)}")


def _make_slug(name: str, uuid: str) -> str:
    """Create a filesystem-safe slug from a schematic name."""
    # Lowercase, replace non-alphanumeric with hyphens
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    # Truncate and add short UUID suffix to avoid collisions
    slug = slug[:60]
    short_uuid = uuid[:8]
    return f"mv-{slug}-{short_uuid}"


# -- Convert ----------------------------------------------------------------

def cmd_convert(args):
    """Convert fetched .litematic files to .schem format."""
    if not MINEMEV_INDEX.exists():
        print("No Minemev index found. Run 'fetch' first.")
        return

    with open(MINEMEV_INDEX) as f:
        index = json.load(f)

    SCHEM_DIR.mkdir(parents=True, exist_ok=True)

    lite_files = sorted(MINEMEV_DIR.glob("*.litematic"))
    if args.limit:
        lite_files = lite_files[:args.limit]

    converted = 0
    errors = 0
    skipped = 0
    total = len(lite_files)

    for i, lite_path in enumerate(lite_files):
        slug = lite_path.stem
        schem_path = SCHEM_DIR / f"{slug}.schem"

        if schem_path.exists() and not args.force:
            skipped += 1
            continue

        meta = index.get(slug, {})
        name = meta.get("name", slug)
        print(f"[{i+1}/{total}] Converting {name[:60]}...", end=" ", flush=True)

        try:
            blocks, dims = _read_litematic(lite_path)
            if not blocks:
                print("SKIP (empty)")
                skipped += 1
                continue

            write_schem(blocks, dims, name, schem_path)
            print(f"OK ({len(blocks)} blocks, {dims[0]}x{dims[1]}x{dims[2]})")
            converted += 1

        except Exception as e:
            print(f"ERROR: {e}")
            errors += 1

    print(f"\nConverted {converted}, skipped {skipped}, errors {errors}")


def _read_litematic(path: Path) -> tuple[list[dict], tuple[int, int, int]]:
    """Read a .litematic file and return (blocks, dims) in .schem format.

    Returns:
        blocks: list of {x, y, z, block_state} dicts (0-based coordinates)
        dims: (width, height, length) tuple
    """
    schem = LitematicSchematic.load(str(path))

    all_blocks = []
    # Track min/max across all regions to compute global bounds
    min_x = min_y = min_z = float('inf')
    max_x = max_y = max_z = float('-inf')

    for region_name, region in schem.regions.items():
        for x in region.xrange():
            for y in region.yrange():
                for z in region.zrange():
                    block = region[x, y, z]
                    if block.id == 'minecraft:air':
                        continue

                    # Build block state string
                    props = dict(block.properties())
                    block_state = block.id
                    if props:
                        # Filter out default/unnecessary properties
                        filtered = {k: v for k, v in props.items()
                                    if v not in (None, '')}
                        if filtered:
                            props_str = ",".join(
                                f"{k}={v}" for k, v in sorted(filtered.items())
                            )
                            block_state = f"{block.id}[{props_str}]"

                    all_blocks.append({
                        "x": x, "y": y, "z": z,
                        "block_state": block_state,
                    })

                    min_x = min(min_x, x)
                    min_y = min(min_y, y)
                    min_z = min(min_z, z)
                    max_x = max(max_x, x)
                    max_y = max(max_y, y)
                    max_z = max(max_z, z)

    if not all_blocks:
        return [], (0, 0, 0)

    # Normalize coordinates to 0-based
    for block in all_blocks:
        block["x"] -= min_x
        block["y"] -= min_y
        block["z"] -= min_z

    width = max_x - min_x + 1
    height = max_y - min_y + 1
    length = max_z - min_z + 1

    return all_blocks, (width, height, length)


# -- Catalog ----------------------------------------------------------------

# Player-friendly category mapping.
# Priority-ordered: first match wins. Each rule is (tag_matches, category_name).
# tag_matches is a set — if ANY tag in the set appears on the schematic, it matches.
CATEGORY_MAP = [
    # Mob-specific farms
    ({"mob-farming", "raid-farm"}, "mob-farms"),
    ({"iron-golem"}, "mob-farms"),
    ({"wither-skeleton", "drowned", "magma-cube"}, "mob-farms"),

    # XP farms (gold farms are primarily XP sources)
    ({"xp-farm"}, "xp-farms"),
    ({"gold", "bartering"}, "xp-farms"),

    # Crop and resource farms
    ({"crop-farming"}, "crop-farms"),
    ({"sugar-cane", "bamboo", "cactus", "wheat", "kelp", "mushroom",
      "nether-wart", "honey", "flowers", "coral", "moss"}, "crop-farms"),
    ({"block-farming", "resource-farming"}, "resource-farms"),
    ({"cobblestone", "obsidian", "basalt", "snow", "concrete"}, "resource-farms"),

    # Wood/tree farms
    ({"oak-wood", "birch-wood", "spruce-wood", "dark-oak-wood", "jungle-wood",
      "acacia-wood", "mangrove-wood", "crimson-wood", "warped-wood",
      "wood", "shroomlight"}, "tree-farms"),

    # Storage and sorting
    ({"storage-tech", "storage-system", "storage-input", "storage-peripherals",
      "chest-hall", "encoded-tech"}, "storage-systems"),
    ({"bulk-storage"}, "storage-systems"),
    ({"item-sorting", "item-transport", "unstackeable-sorting"}, "item-sorting"),

    # Redstone and slimestone
    ({"slimestone"}, "redstone"),
    ({"redstone", "redstone-contraption", "piston-bolt"}, "redstone"),

    # TNT machines
    ({"tnt-tech", "world-eater", "quarry", "bedrock-breaker"}, "tnt-machines"),

    # Auto crafting and processing
    ({"auto-crafting", "mass-crafting", "furnace-array", "brewing"}, "auto-crafting"),

    # Villager stuff
    ({"villager-breeder", "trading-hall"}, "villager-systems"),

    # Shulker/box handling
    ({"shulker", "box-loader", "box-sorter", "box-processor",
      "box-unloader", "box-display"}, "shulker-systems"),
]


def _pick_category(tags: list[str], name: str = "") -> str:
    """Map Minemev tags to a player-friendly catalog category.

    Falls back to keyword matching on the name if no tags match.
    """
    tag_set = set(tags)
    for tag_match, category in CATEGORY_MAP:
        if tag_set & tag_match:
            return category

    # Fallback: try matching on name keywords for untagged entries
    name_lower = name.lower()
    name_fallbacks = [
        (["iron farm", "iron golem", "mob farm", "creeper farm", "raid farm",
          "wither farm", "skeleton farm", "zombie farm"], "mob-farms"),
        (["xp farm", "gold farm", "enderman farm"], "xp-farms"),
        (["sugar cane", "bamboo", "wheat farm", "crop farm", "melon",
          "pumpkin", "cactus", "mushroom"], "crop-farms"),
        (["tree farm", "wood farm", "oak", "spruce", "birch", "dark oak"], "tree-farms"),
        (["cobblestone", "stone farm", "obsidian", "concrete"], "resource-farms"),
        (["storage", "sorter", "sorting"], "storage-systems"),
        (["world eater", "tnt", "tunnel bore", "quarry", "bedrock break"], "tnt-machines"),
        (["furnace", "smelter", "auto craft", "crafter"], "auto-crafting"),
        (["villager", "breeder", "trading"], "villager-systems"),
        (["redstone", "piston", "slimestone"], "redstone"),
    ]
    for keywords, category in name_fallbacks:
        if any(kw in name_lower for kw in keywords):
            return category

    return "technical-other"


def cmd_catalog(args):
    """Merge Minemev entries into the shared catalog.json."""
    if not MINEMEV_INDEX.exists():
        print("No Minemev index found. Run 'fetch' first.")
        return

    with open(MINEMEV_INDEX) as f:
        index = json.load(f)

    # Load existing catalog
    if CATALOG_FILE.exists():
        with open(CATALOG_FILE) as f:
            catalog = json.load(f)
    else:
        catalog = {"categories": {}}

    # Remove any existing Minemev entries (by slug prefix) to avoid duplicates
    for cat_data in catalog["categories"].values():
        cat_data["blueprints"] = [
            bp for bp in cat_data["blueprints"]
            if not bp["id"].startswith("mv-")
        ]

    added = 0
    for slug, meta in index.items():
        schem_path = SCHEM_DIR / f"{slug}.schem"
        if not schem_path.exists():
            continue

        tags = meta.get("tags", [])
        name = meta.get("name", slug)
        category = _pick_category(tags, name)

        if category not in catalog["categories"]:
            catalog["categories"][category] = {"blueprints": []}

        catalog["categories"][category]["blueprints"].append({
            "id": slug,
            "name": name,
            "description": ", ".join(tags) if tags else name,
            "dimensions": _get_schem_dims(schem_path),
            "block_count": 0,  # populated during convert if needed
            "tags": tags,
            "author": f"minemev/{meta.get('vendor', 'unknown')}",
            "skill_level": 0,
            "file": f"{slug}.schem",
            "source": "minemev",
            "downloads": meta.get("downloads", 0),
        })
        added += 1

    # Update counts and sort
    for cat_data in catalog["categories"].values():
        cat_data["blueprints"].sort(key=lambda b: b["name"])
        cat_data["count"] = len(cat_data["blueprints"])

    # Remove empty categories
    catalog["categories"] = {
        k: v for k, v in catalog["categories"].items()
        if v["count"] > 0
    }

    with open(CATALOG_FILE, "w") as f:
        json.dump(catalog, f, indent=2)

    total = sum(c["count"] for c in catalog["categories"].values())
    print(f"Catalog: {total} blueprints across {len(catalog['categories'])} categories")
    print(f"Added {added} Minemev entries")
    print(f"Saved to {CATALOG_FILE}")

    for cat_name, cat_data in sorted(
        catalog["categories"].items(), key=lambda x: -x[1]["count"]
    ):
        mv_count = sum(1 for bp in cat_data["blueprints"] if bp["id"].startswith("mv-"))
        if mv_count:
            print(f"  {cat_name}: {cat_data['count']} ({mv_count} from minemev)")
        else:
            print(f"  {cat_name}: {cat_data['count']}")


def _get_schem_dims(schem_path: Path) -> dict:
    """Read dimensions from a .schem file header."""
    try:
        import nbtlib
        f = nbtlib.load(str(schem_path))
        root = f if "Width" in f else f.get("Schematic", f)
        return {
            "w": int(root.get("Width", 0)),
            "h": int(root.get("Height", 0)),
            "d": int(root.get("Length", 0)),
        }
    except Exception:
        return {"w": 0, "h": 0, "d": 0}


# -- CLI --------------------------------------------------------------------

def cmd_all(args):
    """Run full pipeline: fetch → convert → catalog."""
    print("=== Step 1: Fetch ===")
    cmd_fetch(args)
    print("\n=== Step 2: Convert ===")
    cmd_convert(args)
    print("\n=== Step 3: Catalog ===")
    cmd_catalog(args)


def main():
    parser = argparse.ArgumentParser(description="Minemev → .schem pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # fetch
    fetch_p = subparsers.add_parser("fetch", help="Fetch .litematic files from Minemev")
    fetch_p.add_argument("--tag", help="Filter by Minemev tag (e.g. mob-farming)")
    fetch_p.add_argument("--limit", type=int, help="Max schematics to fetch")

    # convert
    conv_p = subparsers.add_parser("convert", help="Convert .litematic to .schem")
    conv_p.add_argument("--limit", type=int, help="Max to convert")
    conv_p.add_argument("--force", action="store_true", help="Re-convert existing")

    # catalog
    subparsers.add_parser("catalog", help="Merge into shared catalog.json")

    # all
    all_p = subparsers.add_parser("all", help="Run full pipeline")
    all_p.add_argument("--tag", help="Filter by Minemev tag")
    all_p.add_argument("--limit", type=int, help="Max schematics to fetch")
    all_p.add_argument("--force", action="store_true", help="Re-convert existing")

    args = parser.parse_args()

    commands = {
        "fetch": cmd_fetch,
        "convert": cmd_convert,
        "catalog": cmd_catalog,
        "all": cmd_all,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
