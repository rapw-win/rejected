"""События каналов: бота добавили/убрали из админов, заявки на вступление."""
import html
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from aiogram.types import ChatJoinRequest, ChatMemberUpdated, InlineKeyboardButton, InlineKeyboardMarkup

from config import ADMIN_IDS
from database import Database
from handlers.states import AddChannel

log = logging.getLogger(__name__)

router = Router()


async def _clear_bind_state(storage: BaseStorage, bot: Bot, admin_id: int) -> None:
    state = FSMContext(storage=storage, key=StorageKey(bot_id=bot.id, chat_id=admin_id, user_id=admin_id))
    if await state.get_state() == AddChannel.bind.state:
        await state.clear()


@router.my_chat_member(F.chat.type.in_({"channel", "supergroup", "group"}))
async def bot_status_changed(event: ChatMemberUpdated, bot: Bot, db: Database, fsm_storage: BaseStorage):
    chat = event.chat
    title = html.escape(chat.title or "")
    new_status = event.new_chat_member.status
    old_status = event.old_chat_member.status
    existing = db.get_channel_by_chat(chat.id)

    if new_status == ChatMemberStatus.ADMINISTRATOR:
        if existing:
            db.update_channel_title(chat.id, chat.title)
            if old_status != ChatMemberStatus.ADMINISTRATOR:
                for admin_id in ADMIN_IDS:
                    await bot.send_message(admin_id, f"<b>✅ Бот снова админ в «{title}», проверка подписки работает.</b>")
            return

        admin_id = event.from_user.id
        if admin_id not in ADMIN_IDS:
            log.info("Bot was added to %s (%s) by non-admin %s, ignoring", chat.title, chat.id, admin_id)
            return

        pending = db.get_unbound_channels()
        if len(pending) == 1:
            db.bind_channel(pending[0]["id"], chat.id, chat.title)
            await _clear_bind_state(fsm_storage, bot, admin_id)
            await bot.send_message(
                admin_id,
                f"<b>✅ Канал «{title}» привязан!</b>\n\n"
                f"Кнопка: {pending[0]['invite_link']}\n"
                "Теперь бот проверяет подписку на этот канал.",
            )
            return

        rows = [
            [InlineKeyboardButton(text=f"🔗 {ch['invite_link']}", callback_data=f"adm:bind:{ch['id']}:{chat.id}")]
            for ch in pending
        ]
        rows.append([InlineKeyboardButton(text="✨ Создать новую ссылку", callback_data=f"adm:bindnew:{chat.id}:0")])
        rows.append([InlineKeyboardButton(text="📨 Создать ссылку с заявками", callback_data=f"adm:bindnew:{chat.id}:1")])
        rows.append([InlineKeyboardButton(text="✖️ Не добавлять", callback_data="adm:bindskip")])
        text = f"<b>🤖 Бот стал админом в «{title}»</b>\n\n"
        if pending:
            text += "К какой ссылке-кнопке привязать этот канал?"
        else:
            text += "Добавить этот канал в обязательную подписку? Бот создаст ссылку-приглашение сам."
        await bot.send_message(admin_id, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        return

    if existing and old_status == ChatMemberStatus.ADMINISTRATOR:
        for admin_id in ADMIN_IDS:
            await bot.send_message(
                admin_id,
                f"<b>⚠️ Бот больше не админ в «{title}».</b>\n\n"
                "Проверка подписки на этот канал пропускается, пока бота снова не сделают админом.",
            )


@router.chat_join_request()
async def join_request(request: ChatJoinRequest, db: Database):
    # Заявку не одобряем — просто засчитываем её как подписку
    db.add_join_request(request.from_user.id, request.chat.id)
