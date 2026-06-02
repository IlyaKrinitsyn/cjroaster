import json
import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# ---------- Пути ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.getenv("DATA_DIR", BASE_DIR)
PROMPTS_DIR = os.path.join(BASE_DIR, "prompts")
GUIDES_DIR = os.path.join(BASE_DIR, "guides")
REFERENCES_DIR = os.path.join(DATA_DIR, "references")

# ---------- LLM (OpenRouter / OpenAI / LM Studio) ----------
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
MODEL_NAME = os.getenv("MODEL_NAME", "openai/gpt-4o")

# ---------- Лимиты токенов ----------
DESCRIPTION_MAX_TOKENS = int(os.getenv("DESCRIPTION_MAX_TOKENS", "16000"))
ROAST_MAX_TOKENS = int(os.getenv("ROAST_MAX_TOKENS", "16000"))
HYPOTHESIS_MAX_TOKENS = int(os.getenv("HYPOTHESIS_MAX_TOKENS", "3000"))

# ---------- Таймаут ----------
API_TIMEOUT = float(os.getenv("API_TIMEOUT", "600"))

# ---------- Максимум шагов ----------
MAX_STEPS = int(os.getenv("MAX_STEPS", "10"))

# ---------- Маппинг типа пути -> файл гайда (имена в нижнем регистре для Linux) ----------
PATH_TYPE_TO_GUIDE = {
    "Конверсионный": "conversion.txt",
    "Информирующий": "informing.txt",
    "Сервисный": "service.txt",
    "Удерживающий": "retention.txt",
}

# ---------- Обязательные файлы для проверки при старте ----------
REQUIRED_FILES = [
    os.path.join(PROMPTS_DIR, "system_description.txt"),
    os.path.join(PROMPTS_DIR, "system_roast.txt"),
    os.path.join(PROMPTS_DIR, "user_roast.txt"),
    os.path.join(PROMPTS_DIR, "system_hypothesis.txt"),
    os.path.join(PROMPTS_DIR, "user_hypothesis.txt"),
]

# ---------- Дополнительный параметр для API (LM Studio и др.) ----------
_extra_raw = os.getenv("EXTRA_BODY_JSON", "").strip()
if _extra_raw:
    try:
        EXTRA_BODY = json.loads(_extra_raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"EXTRA_BODY_JSON is not valid JSON: {e}") from e
else:
    EXTRA_BODY = {}

# ---------- Mobbin ----------
MOBBIN_API_KEY = os.getenv("MOBBIN_API_KEY", "")
MOBBIN_API_URL = "https://api.mobbin.com/v1/screens/search"

# ---------- HTTP-сервер ----------
PORT = int(os.getenv("PORT", "8000"))


def _llm_extra_kwargs() -> dict:
    return {"extra_body": EXTRA_BODY} if EXTRA_BODY else {}


_llm_client: OpenAI | None = None


def get_llm_client() -> OpenAI:
    global _llm_client
    if _llm_client is None:
        if not LLM_API_KEY:
            raise RuntimeError(
                "LLM_API_KEY is not set. Copy .env.example to .env and set your OpenRouter (or other) API key."
            )
        _llm_client = OpenAI(
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
            timeout=API_TIMEOUT,
        )
    return _llm_client


class _LazyLLMClient:
    """Прокси: клиент создаётся при первом вызове LLM (manage_keys.py без ключа не падает)."""

    def __getattr__(self, name):
        return getattr(get_llm_client(), name)


client = _LazyLLMClient()
