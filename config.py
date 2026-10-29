import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_env_file(path: Path) -> None:
    """Простейший загрузчик .env (без внешних зависимостей)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(BASE_DIR / ".env")

# Токен от @BotFather. Задаётся переменной окружения BOT_TOKEN или в файле .env.
# Не вписывай токен прямо сюда, если репозиторий публичный.
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# ID админов через запятую
ADMIN_IDS = {
    int(x) for x in os.getenv("ADMIN_IDS", "157680917").replace(" ", "").split(",") if x
}

DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "bot.db"))

# Если хостинг требует открытый порт (Render, Koyeb и т.п.) — бот поднимет
# простой HTTP-сервер для health-check на этом порту.
PORT = os.getenv("PORT")

# Прокси для запросов к Telegram (нужен, например, на бесплатном PythonAnywhere:
# PROXY=http://proxy.server:3128). Если не задан — берётся HTTPS_PROXY, если он есть.
PROXY = os.getenv("PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")

# Максимум файлов на одну реф-ссылку
MAX_FILES = 3

# ID эффекта сообщения «конфетти» 🎉
CONFETTI_EFFECT_ID = "5046509860389126442"

# Каналы, которые добавятся при первом запуске. Их нужно «привязать»:
# добавить бота админом в канал (подробнее — в README).
DEFAULT_CHANNEL_LINKS = [
    "https://t.me/+Dt2WoRNTqVUwZGNk",
    "https://t.me/+MAObpyzeymlhMmJk",
]
