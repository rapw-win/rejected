"""Рассылка всем пользователям. Сообщение копируется как есть (copy_message), поэтому
сохраняется всё: форматирование, премиум-эмодзи, спойлеры, цитаты, ссылки, медиа."""
import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import ADMIN_IDS
from database import Database
from handlers.admin import back_kb, not_command, show
from handlers.states import Broadcast

log = logging.getLogger(__name__)

router = Router()
router.message.filter(F.chat.type == "private", F.from_user.id.in_(ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(ADMIN_IDS))

# ~20 сообщений в секунду — с запасом под лимит Telegram (30/сек)
SEND_DELAY = 0.05
PROGRESS_EVERY = 50

_running = {"task": None, "stop": False}

BROADCAST_TEXT = (
    "<b>📣 Рассылка</b>\n\n"
    "Отправь сообщение, которое получат все пользователи: текст, фото, видео, файл, GIF, "
    "голосовое, кружок, стикер.\n\n"
    "Сохранится всё как ты отправил: <b>жирный</b>, <i>курсив</i>, <u>подчёркнутый</u>, "
    "<s>зачёркнутый</s>, <tg-spoiler>спойлер</tg-spoiler>, <code>код</code>, цитаты, ссылки "
    "и премиум-эмодзи.\n\n"
    "<i>Альбомы (несколько фото в одном сообщении) не поддерживаются — отправь одним медиа.</i>"
)

BUTTONS_TEXT = (
    "<b>🔘 Кнопки под сообщением</b>\n\n"
    "Отправь кнопки, каждая с новой строки:\n"
    "<code>Текст кнопки | https://ссылка</code>\n\n"
    "Несколько кнопок в один ряд — через <code>;;</code>\n"
    "<code>Кнопка 1 | https://t.me/a ;; Кнопка 2 | https://t.me/b</code>"
)


def parse_buttons(text: str) -> InlineKeyboardMarkup | None:
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        row = []
        for part in line.split(";;"):
            if "|" not in part:
                return None
            title, url = (x.strip() for x in part.rsplit("|", 1))
            if not title or not url.startswith(("https://", "http://", "tg://")):
                return None
            row.append(InlineKeyboardButton(text=title, url=url))
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def control_kb(has_buttons: bool, total: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=f"🚀 Отправить ({total})", callback_data="bc:send")
    kb.button(text="✏️ Изменить кнопки" if has_buttons else "🔘 Добавить кнопки", callback_data="bc:buttons")
    if has_buttons:
        kb.button(text="🧹 Убрать кнопки", callback_data="bc:nobuttons")
    kb.button(text="✖️ Отмена", callback_data="adm:menu")
    kb.adjust(1)
    return kb.as_markup()


async def send_preview(bot: Bot, chat_id: int, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    markup = InlineKeyboardMarkup.model_validate(data["markup"]) if data.get("markup") else None
    try:
        await bot.copy_message(chat_id, data["from_chat"], data["message_id"], reply_markup=markup)
    except TelegramBadRequest as exc:
        await bot.send_message(chat_id, f"<b>❌ Не получилось показать превью:</b> {exc.message}")
        return
    await bot.send_message(
        chat_id,
        "<b>👆 Так будет выглядеть рассылка.</b> Отправляем?",
        reply_markup=control_kb(bool(markup), db.count_active_users()),
    )


@router.callback_query(F.data == "adm:bc")
async def cb_broadcast(call: CallbackQuery, state: FSMContext):
    if _running["task"] and not _running["task"].done():
        kb = InlineKeyboardBuilder()
        kb.button(text="⛔ Остановить рассылку", callback_data="bc:stop")
        kb.button(text="◀️ Назад", callback_data="adm:menu")
        kb.adjust(1)
        await show(call, "<b>⏳ Рассылка уже идёт.</b>", kb.as_markup())
        return
    await state.set_state(Broadcast.message)
    await state.update_data(markup=None)
    await show(call, BROADCAST_TEXT, back_kb("adm:menu", "✖️ Отмена"))


@router.message(Broadcast.message, not_command)
async def broadcast_message(message: Message, state: FSMContext, bot: Bot, db: Database):
    if message.media_group_id:
        if (await state.get_data()).get("album") != message.media_group_id:
            await state.update_data(album=message.media_group_id)
            await message.answer("<b>Альбомы не поддерживаются.</b> Отправь одно сообщение.")
        return
    await state.update_data(from_chat=message.chat.id, message_id=message.message_id)
    await send_preview(bot, message.chat.id, state, db)


@router.callback_query(F.data == "bc:buttons")
async def cb_buttons(call: CallbackQuery, state: FSMContext):
    if not (await state.get_data()).get("message_id"):
        await call.answer("Сначала отправь сообщение", show_alert=True)
        return
    await state.set_state(Broadcast.buttons)
    await show(call, BUTTONS_TEXT, back_kb("bc:preview", "◀️ Назад"))


@router.message(Broadcast.buttons, F.text, not_command)
async def broadcast_buttons(message: Message, state: FSMContext, bot: Bot, db: Database):
    markup = parse_buttons(message.text)
    if not markup:
        await message.answer("<b>Не понял формат.</b>\n\n" + BUTTONS_TEXT)
        return
    await state.update_data(markup=markup.model_dump(exclude_none=True))
    await state.set_state(Broadcast.message)
    await send_preview(bot, message.chat.id, state, db)


@router.callback_query(F.data == "bc:nobuttons")
async def cb_no_buttons(call: CallbackQuery, state: FSMContext, bot: Bot, db: Database):
    await state.update_data(markup=None)
    await call.answer("Кнопки убраны")
    await send_preview(bot, call.from_user.id, state, db)


@router.callback_query(F.data == "bc:preview")
async def cb_preview(call: CallbackQuery, state: FSMContext, bot: Bot, db: Database):
    await state.set_state(Broadcast.message)
    await call.answer()
    await send_preview(bot, call.from_user.id, state, db)


@router.callback_query(F.data == "bc:send")
async def cb_send(call: CallbackQuery, state: FSMContext, bot: Bot, db: Database):
    data = await state.get_data()
    if not data.get("message_id"):
        await call.answer("Сообщение для рассылки не найдено", show_alert=True)
        return
    if _running["task"] and not _running["task"].done():
        await call.answer("Рассылка уже идёт", show_alert=True)
        return
    await state.clear()
    await call.answer()
    markup = InlineKeyboardMarkup.model_validate(data["markup"]) if data.get("markup") else None
    _running["stop"] = False
    _running["task"] = asyncio.create_task(
        run_broadcast(bot, db, call.from_user.id, data["from_chat"], data["message_id"], markup)
    )


@router.callback_query(F.data == "bc:stop")
async def cb_stop(call: CallbackQuery):
    _running["stop"] = True
    await call.answer("Останавливаю…")


def _progress_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⛔ Остановить", callback_data="bc:stop")
    return kb.as_markup()


async def _copy(bot: Bot, user_id: int, from_chat: int, message_id: int, markup) -> None:
    try:
        await bot.copy_message(user_id, from_chat, message_id, reply_markup=markup)
    except TelegramRetryAfter as exc:
        await asyncio.sleep(exc.retry_after + 1)
        await bot.copy_message(user_id, from_chat, message_id, reply_markup=markup)


async def run_broadcast(bot: Bot, db: Database, admin_id: int, from_chat: int, message_id: int, markup) -> None:
    user_ids = [uid for uid in db.get_active_user_ids() if uid not in ADMIN_IDS]
    total = len(user_ids)
    sent = blocked = failed = 0
    progress = await bot.send_message(admin_id, f"<b>📣 Рассылка началась…</b> 0/{total}", reply_markup=_progress_kb())

    for index, user_id in enumerate(user_ids, 1):
        if _running["stop"]:
            break
        try:
            await _copy(bot, user_id, from_chat, message_id, markup)
            sent += 1
        except TelegramForbiddenError:
            db.set_blocked(user_id)
            blocked += 1
        except Exception as exc:  # noqa: BLE001 — одна ошибка не должна останавливать рассылку
            log.warning("Broadcast to %s failed: %s", user_id, exc)
            failed += 1
        if index % PROGRESS_EVERY == 0:
            try:
                await bot.edit_message_text(
                    f"<b>📣 Рассылка идёт…</b> {index}/{total}",
                    chat_id=admin_id, message_id=progress.message_id, reply_markup=_progress_kb(),
                )
            except TelegramBadRequest:
                pass
        await asyncio.sleep(SEND_DELAY)

    title = "⛔ Рассылка остановлена" if _running["stop"] else "✅ Рассылка завершена"
    report = (
        f"<b>{title}</b>\n\n"
        f"📬 Доставлено: <b>{sent}</b>\n"
        f"🚫 Заблокировали бота: <b>{blocked}</b>\n"
        f"⚠️ Ошибок: <b>{failed}</b>\n"
        f"👥 Всего в списке: <b>{total}</b>"
    )
    try:
        await bot.edit_message_text(
            report, chat_id=admin_id, message_id=progress.message_id,
            reply_markup=back_kb("adm:menu", "◀️ В меню"),
        )
    except TelegramBadRequest:
        await bot.send_message(admin_id, report, reply_markup=back_kb("adm:menu", "◀️ В меню"))
