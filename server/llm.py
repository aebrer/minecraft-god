import logging

from openai import AsyncOpenAI

from server.config import ZHIPU_API_KEY, LLM_BASE_URL

logger = logging.getLogger("minecraft-god")

client = AsyncOpenAI(
    api_key=ZHIPU_API_KEY,
    base_url=LLM_BASE_URL,
)
