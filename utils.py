import logging
import re

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import CONFETTI_EFFECT_ID
from database import Database
from texts import (
    CHECK_BUTTON,
    EMOJI_CHECK,
    EMOJI_SUBSCRIBE,
    FILES_CAPTION,
    SUBSCRIBE_BUTTON,
)

log = logging.getLogger(__name__)

_TG_EMOJI_RE = re.compile(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", re.S)

_MEMBER_STATUSES = {
    ChatMemberStatus.CREATOR,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.MEMBER,
}

# file_type -> (метод бота, имя параметра с файлом)
_SENDERS = {
    "document": ("send_document", "document"),
    "photo": ("send_photo", "photo"),
    "video": ("send_video", "video"),
    "audio": ("send_audio", "audio"),
    "animation": ("send_animation", "animation"),
    "voice": ("send_voice", "voice"),
}


def _without_premium(kwargs: dict) -> dict:
    """Копия параметров без премиум-эмодзи, иконок на кнопках и эффекта сообщения."""
    kw = dict(kwargs)
    for key in ("text", "caption"):
        if isinstance(kw.get(key), str):
            kw[key] = _TG_EMOJI_RE.sub(r"\1", kw[key])
    markup = kw.get("reply_markup")
    if isinstance(markup, InlineKeyboardMarkup):
        kw["reply_markup"] = InlineKeyboardMarkup(
            inline_keyboard=[
                [button.model_copy(update={"icon_custom_emoji_id": None}) for button in row]
                for row in markup.inline_keyboard
            ]
        )
    kw.pop("message_effect_id", None)
    return kw


async def safe_call(method, **kwargs):
    """Вызывает метод отправки; если Telegram отклонил премиум-фичи — повторяет без них."""
    try:
        return await method(**kwargs)
    except TelegramBadRequest as exc:
        fallback = _without_premium(kwargs)
        if fallback == kwargs:
            raise
        log.warning("Telegram rejected premium features (%s), retrying without them", exc.message)
        return await method(**fallback)


def subscribe_keyboard(channels) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=SUBSCRIBE_BUTTON,
            url=channel["invite_link"],
            icon_custom_emoji_id=EMOJI_SUBSCRIBE,
        )
        for channel in channels
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append(
        [
            InlineKeyboardButton(
                text=CHECK_BUTTON,
                callback_data="check_sub",
                icon_custom_emoji_id=EMOJI_CHECK,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def get_unsubscribed(bot: Bot, db: Database, user_id: int) -> list:
    """Список привязанных каналов, на которые пользователь не подписан и не подал заявку."""
    missing = []
    for channel in db.get_bound_channels():
        if db.has_join_request(user_id, channel["chat_id"]):
            continue
        try:
            member = await bot.get_chat_member(channel["chat_id"], user_id)
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            # Бот потерял доступ к каналу — не блокируем пользователей из-за этого.
            log.warning("Can't check channel %s: %s", channel["chat_id"], exc)
            continue
        if member.status in _MEMBER_STATUSES:
            continue
        if member.status == ChatMemberStatus.RESTRICTED and getattr(member, "is_member", False):
            continue
        missing.append(channel)
    return missing


def extract_file(message: Message) -> dict | None:
    """Достаёт из сообщения файл в том виде, в котором его прислали."""
    if message.document:
        return {"type": "document", "file_id": message.document.file_id,
                "name": message.document.file_name or "Документ"}
    if message.photo:
        return {"type": "photo", "file_id": message.photo[-1].file_id, "name": "Фото"}
    if message.video:
        return {"type": "video", "file_id": message.video.file_id,
                "name": message.video.file_name or "Видео"}
    if message.audio:
        return {"type": "audio", "file_id": message.audio.file_id,
                "name": message.audio.file_name or message.audio.title or "Аудио"}
    if message.animation:
        return {"type": "animation", "file_id": message.animation.file_id,
                "name": message.animation.file_name or "GIF"}
    if message.voice:
        return {"type": "voice", "file_id": message.voice.file_id, "name": "Голосовое"}
    return None


async def send_link_files(bot: Bot, db: Database, chat_id: int, code: str) -> bool:
    """Отправляет файлы ссылки как есть. На первом сообщении — подпись и эффект конфетти."""
    files = db.get_link_files(code)
    if not files:
        return False
    for index, file in enumerate(files):
        method_name, field = _SENDERS[file["file_type"]]
        kwargs = {"chat_id": chat_id, field: file["file_id"]}
        if index == 0:
            kwargs["caption"] = FILES_CAPTION
            kwargs["message_effect_id"] = CONFETTI_EFFECT_ID
        await safe_call(getattr(bot, method_name), **kwargs)
    db.add_delivery(code)
    return True


async def deep_link(bot: Bot, code: str) -> str:
    me = await bot.me()
    return f"https://t.me/{me.username}?start={code}"
