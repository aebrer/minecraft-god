import logging

import httpx
from openai import AsyncOpenAI

from server.config import ZHIPU_API_KEY, LLM_BASE_URL

logger = logging.getLogger("minecraft-god")

client = AsyncOpenAI(
    api_key=ZHIPU_API_KEY,
    base_url=LLM_BASE_URL,
    timeout=httpx.Timeout(120.0, connect=10.0),  # 2 min total, 10s connect
)
