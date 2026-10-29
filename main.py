import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat
from aiohttp import web

from config import ADMIN_IDS, BOT_TOKEN, DB_PATH, DEFAULT_CHANNEL_LINKS, PORT, PROXY
from database import Database
from handlers import admin, broadcast, channels, user


async def start_health_server(port: int) -> None:
    """Мини HTTP-сервер для хостингов, которые проверяют, что приложение слушает порт."""
    app = web.Application()
    app.router.add_get("/", lambda request: web.Response(text="OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logging.info("Health server started on port %s", port)


async def set_commands(bot: Bot) -> None:
    await bot.set_my_commands([BotCommand(command="start", description="Получить файлы")])
    for admin_id in ADMIN_IDS:
        try:
            await bot.set_my_commands(
                [
                    BotCommand(command="start", description="Получить файлы"),
                    BotCommand(command="admin", description="Админ-панель"),
                ],
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except (TelegramBadRequest, TelegramForbiddenError):
            logging.warning("Admin %s hasn't started the bot yet", admin_id)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not BOT_TOKEN:
        sys.exit("BOT_TOKEN не задан: создай файл .env рядом с main.py или переменную окружения BOT_TOKEN")

    db = Database(DB_PATH)
    db.seed_channels(DEFAULT_CHANNEL_LINKS)

    bot = Bot(
        BOT_TOKEN,
        session=AiohttpSession(proxy=PROXY) if PROXY else None,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    dp = Dispatcher(storage=MemoryStorage(), db=db)
    # Порядок важен: админские обработчики раньше пользовательских
    dp.include_routers(admin.router, broadcast.router, channels.router, user.router)

    if PORT:
        await start_health_server(int(PORT))

    me = await bot.me()
    logging.info("Bot @%s started", me.username)
    await set_commands(bot)
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
