import os
from dotenv import load_dotenv

load_dotenv()

# ---------- Пути ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROMPTS_DIR = os.path.join(BASE_DIR, "prompts")
GUIDES_DIR = os.path.join(BASE_DIR, "guides")
REFERENCES_DIR = os.path.join(BASE_DIR, "references")

# ---------- Модель ----------
MODEL_NAME = "gemma-4-26b-a4b-it-mlx"

# ---------- Лимиты токенов ----------
DESCRIPTION_MAX_TOKENS = 16000
ROAST_MAX_TOKENS = 16000
HYPOTHESIS_MAX_TOKENS = 3000

# ---------- Таймаут ----------
API_TIMEOUT = 1800.0

# ---------- Максимум шагов ----------
MAX_STEPS = 10

# ---------- Маппинг типа пути -> файл гайда ----------
PATH_TYPE_TO_GUIDE = {
    "Конверсионный": "Conversion.txt",
    "Информирующий": "Informing.txt",
    "Сервисный": "Service.txt",
    "Удерживающий": "Retention.txt",
}

# ---------- Обязательные файлы для проверки при старте ----------
REQUIRED_FILES = [
    os.path.join(PROMPTS_DIR, "system_description.txt"),
    os.path.join(PROMPTS_DIR, "system_roast.txt"),
    os.path.join(PROMPTS_DIR, "user_roast.txt"),
    os.path.join(PROMPTS_DIR, "system_hypothesis.txt"),
    os.path.join(PROMPTS_DIR, "user_hypothesis.txt"),
]

# ---------- Дополнительный параметр для API ----------
EXTRA_BODY = {
    "thinking": {"type": "disabled"}
}

# ---------- Mobbin ----------
MOBBIN_API_KEY = os.getenv("MOBBIN_API_KEY", "")
MOBBIN_API_URL = "https://api.mobbin.com/v1/screens/search"