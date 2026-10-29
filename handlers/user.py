from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database import Database
from texts import BAD_REF_TEXT, NO_REF_TEXT, NOT_SUBSCRIBED_ALERT, SUBSCRIBE_TEXT
from utils import get_unsubscribed, safe_call, send_link_files, subscribe_keyboard

router = Router()
router.message.filter(F.chat.type == "private")


async def deliver_or_ask(bot: Bot, db: Database, user_id: int, code: str) -> None:
    missing = await get_unsubscribed(bot, db, user_id)
    if missing:
        await safe_call(
            bot.send_message,
            chat_id=user_id,
            text=SUBSCRIBE_TEXT,
            reply_markup=subscribe_keyboard(missing),
        )
        return
    if not await send_link_files(bot, db, user_id, code):
        await bot.send_message(user_id, BAD_REF_TEXT)


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, bot: Bot, db: Database, state: FSMContext):
    await state.clear()
    user = message.from_user
    db.upsert_user(user.id, user.username, user.full_name)

    code = (command.args or "").strip()
    if code:
        if not db.get_link(code):
            await message.answer(BAD_REF_TEXT)
            return
        db.set_last_ref(user.id, code)
        db.add_click(code)
    else:
        # Повторный /start — выдаём файлы последней ссылки, по которой перешёл пользователь
        code = db.get_last_ref(user.id)
        if not code or not db.get_link(code):
            await message.answer(NO_REF_TEXT)
            return

    await deliver_or_ask(bot, db, user.id, code)


@router.callback_query(F.data == "check_sub")
async def check_subscription(call: CallbackQuery, bot: Bot, db: Database):
    user_id = call.from_user.id
    missing = await get_unsubscribed(bot, db, user_id)
    if missing:
        await call.answer(NOT_SUBSCRIBED_ALERT, show_alert=True)
        try:
            await call.message.edit_reply_markup(reply_markup=subscribe_keyboard(missing))
        except TelegramBadRequest:
            pass  # клавиатура не изменилась
        return

    await call.answer()
    try:
        await call.message.delete()
    except TelegramBadRequest:
        pass

    code = db.get_last_ref(user_id)
    if not code or not await send_link_files(bot, db, user_id, code):
        await bot.send_message(user_id, BAD_REF_TEXT)
