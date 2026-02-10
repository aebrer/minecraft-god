import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
_project_root = Path(__file__).parent.parent
load_dotenv(_project_root / ".env")

# LLM
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.z.ai/api/coding/paas/v4/")
GOD_MODEL = os.getenv("GOD_MODEL", "glm-4.7")

# God tick settings
GOD_TICK_INTERVAL = int(os.getenv("GOD_TICK_INTERVAL", "45"))
PRAYER_COOLDOWN = int(os.getenv("PRAYER_COOLDOWN", "15"))
MAX_TOOL_CALLS_PER_RESPONSE = 5

# Deep God trigger thresholds
KIND_GOD_ACTION_THRESHOLD = int(os.getenv("KIND_GOD_ACTION_THRESHOLD", "6"))
DEEP_GOD_DEEP_MINING_CHANCE = 0.7  # chance when player is below Y=0
DEEP_GOD_ORE_CHANCE = 0.4  # chance when diamond/ancient debris mined
DEEP_GOD_NETHER_CHANCE = 0.5  # chance when player is in nether
DEEP_GOD_NIGHT_UNDERGROUND_CHANCE = 0.15  # chance when night + below Y=30
DEEP_GOD_RANDOM_CHANCE = 0.05  # base random chance per tick when below Y=30

# Server
BACKEND_HOST = os.getenv("BACKEND_HOST", "127.0.0.1")
BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8000"))

# Kind God memory
MEMORY_DIR = _project_root / "data"
MEMORY_FILE = MEMORY_DIR / "kind_god_memory.json"
MEMORY_MAX_ENTRIES = int(os.getenv("MEMORY_MAX_ENTRIES", "15"))
MEMORY_CONSOLIDATION_INTERVAL_TICKS = int(os.getenv("MEMORY_CONSOLIDATION_INTERVAL_TICKS", "960"))

# Prayer keywords that trigger immediate Kind God response
PRAYER_KEYWORDS = {"god", "please", "help", "pray", "prayer", "mercy", "save", "lord"}

# Herald keywords — trigger the Herald, NOT the Kind God
HERALD_KEYWORDS = {"herald", "bard", "guide"}
