import asyncio
import html

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, MessageOriginChannel
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import ADMIN_IDS, MAX_FILES
from database import Database
from handlers.states import AddChannel, NewLink
from texts import ADD_CHANNEL_TEXT, ADMIN_MENU_TEXT, BIND_CHANNEL_TEXT, NEW_LINK_TEXT
from utils import deep_link, extract_file, send_link_files

router = Router()
router.message.filter(F.chat.type == "private", F.from_user.id.in_(ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(ADMIN_IDS))

LINKS_PER_PAGE = 8
_upload_lock = asyncio.Lock()


def not_command(message: Message) -> bool:
    # Команды (например /start с реф-кодом) не должны съедаться шагами мастера
    return not (message.text or "").startswith("/")


# ---------- клавиатуры ----------

def menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📎 Создать ссылку", callback_data="adm:new")
    kb.button(text="🔗 Мои ссылки", callback_data="adm:links:0")
    kb.button(text="📢 Каналы", callback_data="adm:channels")
    kb.button(text="📊 Статистика", callback_data="adm:stats")
    kb.adjust(1, 2, 1)
    return kb.as_markup()


def back_kb(callback_data: str = "adm:menu", text: str = "◀️ Назад") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=text, callback_data=callback_data)
    return kb.as_markup()


def upload_kb(has_files: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if has_files:
        kb.button(text="✅ Готово", callback_data="adm:newdone")
    kb.button(text="✖️ Отмена", callback_data="adm:menu")
    kb.adjust(1)
    return kb.as_markup()


async def show(call: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    """Редактирует сообщение панели, а если не вышло — присылает новое."""
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in exc.message:
            await call.message.answer(text, reply_markup=markup)
    await call.answer()


# ---------- меню ----------

@router.message(Command("admin"))
@router.message(CommandStart(), F.text.regexp(r"^/start(@\w+)?\s*$"))
async def cmd_admin(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(ADMIN_MENU_TEXT, reply_markup=menu_kb())


@router.callback_query(F.data == "adm:menu")
async def cb_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(call, ADMIN_MENU_TEXT, menu_kb())


@router.callback_query(F.data == "adm:stats")
async def cb_stats(call: CallbackQuery, db: Database):
    clicks, deliveries = db.links_totals()
    text = (
        "<b>📊 Статистика</b>\n\n"
        f"👤 Пользователей: <b>{db.count_users()}</b>\n"
        f"🔗 Ссылок: <b>{db.count_links()}</b>\n"
        f"👁 Переходов: <b>{clicks}</b>\n"
        f"📥 Выдач файлов: <b>{deliveries}</b>\n"
        f"📢 Каналов: <b>{len(db.get_bound_channels())}</b> из {len(db.get_channels())} привязано"
    )
    await show(call, text, back_kb())


# ---------- создание ссылки ----------

@router.callback_query(F.data == "adm:new")
async def cb_new_link(call: CallbackQuery, state: FSMContext):
    await state.set_state(NewLink.files)
    await state.update_data(files=[])
    await show(call, NEW_LINK_TEXT.format(max=MAX_FILES), upload_kb(False))


@router.message(NewLink.files, not_command)
async def new_link_file(message: Message, state: FSMContext, bot: Bot, db: Database):
    file = extract_file(message)
    if not file:
        await message.answer("<b>Это не файл.</b> Отправь документ, фото, видео или аудио.")
        return

    # Лок нужен, чтобы файлы, отправленные альбомом, не перетёрли друг друга
    async with _upload_lock:
        files = (await state.get_data()).get("files", [])
        if len(files) >= MAX_FILES:
            return
        files.append(file)
        await state.update_data(files=files)

    if len(files) >= MAX_FILES:
        await finish_link(message, state, bot, db)
        return
    await message.answer(
        f"<b>✅ Файл {len(files)}/{MAX_FILES} добавлен:</b> {html.escape(file['name'])}\n\n"
        "Отправь ещё или нажми «Готово».",
        reply_markup=upload_kb(True),
    )


@router.callback_query(F.data == "adm:newdone", NewLink.files)
async def cb_new_link_done(call: CallbackQuery, state: FSMContext, bot: Bot, db: Database):
    await call.answer()
    await finish_link(call.message, state, bot, db)


async def finish_link(message: Message, state: FSMContext, bot: Bot, db: Database) -> None:
    files = (await state.get_data()).get("files", [])
    await state.clear()
    if not files:
        await message.answer("<b>Нет файлов — ссылка не создана.</b>", reply_markup=menu_kb())
        return

    title = files[0]["name"][:40] + (f" +{len(files) - 1}" if len(files) > 1 else "")
    code = db.create_link(title, files)
    url = await deep_link(bot, code)
    kb = InlineKeyboardBuilder()
    kb.button(text="⚙️ Открыть ссылку", callback_data=f"adm:link:{code}")
    kb.button(text="◀️ В меню", callback_data="adm:menu")
    kb.adjust(1)
    await message.answer(
        f"<b>🎉 Ссылка создана!</b>\n\n<code>{url}</code>\n\nФайлов: {len(files)}",
        reply_markup=kb.as_markup(),
    )


# ---------- список ссылок ----------

@router.callback_query(F.data.startswith("adm:links:"))
async def cb_links(call: CallbackQuery, db: Database):
    page = int(call.data.split(":")[2])
    total = db.count_links()
    links = db.get_links(page * LINKS_PER_PAGE, LINKS_PER_PAGE)

    kb = InlineKeyboardBuilder()
    for link in links:
        kb.button(text=f"📁 {link['title']} · 👁 {link['clicks']}", callback_data=f"adm:link:{link['code']}")
    kb.adjust(1)
    nav = []
    if page > 0:
        nav.append(("⬅️", f"adm:links:{page - 1}"))
    if (page + 1) * LINKS_PER_PAGE < total:
        nav.append(("➡️", f"adm:links:{page + 1}"))
    if nav:
        nav_kb = InlineKeyboardBuilder()
        for text, data in nav:
            nav_kb.button(text=text, callback_data=data)
        kb.attach(nav_kb)
    kb.row(*back_kb().inline_keyboard[0])

    text = f"<b>🔗 Мои ссылки</b> ({total})" if total else "<b>🔗 Ссылок пока нет.</b>\n\nСоздай первую в меню."
    await show(call, text, kb.as_markup())


@router.callback_query(F.data.startswith("adm:link:"))
async def cb_link_card(call: CallbackQuery, bot: Bot, db: Database):
    code = call.data.split(":", 2)[2]
    link = db.get_link(code)
    if not link:
        await call.answer("Ссылка уже удалена", show_alert=True)
        return
    files = db.get_link_files(code)
    files_text = "\n".join(f"{i}. {html.escape(f['file_name'])}" for i, f in enumerate(files, 1))
    text = (
        "<b>🔗 Ссылка</b>\n"
        f"<code>{await deep_link(bot, code)}</code>\n\n"
        f"<b>📁 Файлы:</b>\n{files_text}\n\n"
        f"👁 Переходов: <b>{link['clicks']}</b>\n"
        f"📥 Выдач: <b>{link['deliveries']}</b>"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="👀 Получить файлы", callback_data=f"adm:test:{code}")
    kb.button(text="🗑 Удалить", callback_data=f"adm:del:{code}")
    kb.button(text="◀️ Назад", callback_data="adm:links:0")
    kb.adjust(2, 1)
    await show(call, text, kb.as_markup())


@router.callback_query(F.data.startswith("adm:test:"))
async def cb_link_test(call: CallbackQuery, bot: Bot, db: Database):
    await call.answer()
    await send_link_files(bot, db, call.from_user.id, call.data.split(":", 2)[2])


@router.callback_query(F.data.startswith("adm:del:"))
async def cb_link_delete(call: CallbackQuery):
    code = call.data.split(":", 2)[2]
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 Да, удалить", callback_data=f"adm:delok:{code}")
    kb.button(text="◀️ Нет", callback_data=f"adm:link:{code}")
    await show(call, "<b>Удалить ссылку?</b>\nПользователи больше не смогут получить по ней файлы.", kb.as_markup())


@router.callback_query(F.data.startswith("adm:delok:"))
async def cb_link_delete_ok(call: CallbackQuery, db: Database):
    db.delete_link(call.data.split(":", 2)[2])
    await show(call, "<b>✅ Ссылка удалена.</b>", back_kb("adm:links:0", "◀️ К ссылкам"))


# ---------- каналы ----------

def channel_name(channel) -> str:
    return channel["title"] or channel["invite_link"]


@router.callback_query(F.data == "adm:channels")
async def cb_channels(call: CallbackQuery, state: FSMContext, db: Database):
    await state.clear()
    channels = db.get_channels()
    kb = InlineKeyboardBuilder()
    for channel in channels:
        mark = "✅" if channel["chat_id"] else "⏳"
        kb.button(text=f"{mark} {channel_name(channel)}", callback_data=f"adm:ch:{channel['id']}")
    kb.button(text="➕ Добавить канал", callback_data="adm:chadd")
    kb.button(text="◀️ Назад", callback_data="adm:menu")
    kb.adjust(1)
    text = (
        "<b>📢 Каналы для подписки</b>\n\n"
        "✅ — привязан, подписка проверяется\n"
        "⏳ — ждёт, пока бота добавят админом в канал (пользователям пока не показывается)"
    )
    await show(call, text, kb.as_markup())


@router.callback_query(F.data.startswith("adm:ch:"))
async def cb_channel_card(call: CallbackQuery, db: Database):
    channel = db.get_channel(int(call.data.split(":")[2]))
    if not channel:
        await call.answer("Канал уже удалён", show_alert=True)
        return
    status = f"✅ привязан (<code>{channel['chat_id']}</code>)" if channel["chat_id"] else "⏳ не привязан"
    text = (
        f"<b>📢 {html.escape(channel_name(channel))}</b>\n\n"
        f"Ссылка кнопки: {channel['invite_link']}\n"
        f"Статус: {status}"
    )
    kb = InlineKeyboardBuilder()
    if not channel["chat_id"]:
        kb.button(text="🔗 Привязать", callback_data=f"adm:chbind:{channel['id']}")
    kb.button(text="🗑 Удалить", callback_data=f"adm:chdel:{channel['id']}")
    kb.button(text="◀️ Назад", callback_data="adm:channels")
    kb.adjust(1)
    await show(call, text, kb.as_markup())


@router.callback_query(F.data.startswith("adm:chdel:"))
async def cb_channel_delete(call: CallbackQuery, db: Database):
    db.delete_channel(int(call.data.split(":")[2]))
    await show(call, "<b>✅ Канал удалён из подписки.</b>", back_kb("adm:channels", "◀️ К каналам"))


@router.callback_query(F.data == "adm:chadd")
async def cb_channel_add(call: CallbackQuery, state: FSMContext):
    await state.set_state(AddChannel.link)
    await show(call, ADD_CHANNEL_TEXT, back_kb("adm:channels", "✖️ Отмена"))


@router.callback_query(F.data.startswith("adm:chbind:"))
async def cb_channel_bind(call: CallbackQuery, state: FSMContext):
    await state.set_state(AddChannel.bind)
    await state.update_data(channel_id=int(call.data.split(":")[2]))
    await show(call, BIND_CHANNEL_TEXT, back_kb("adm:channels", "◀️ К каналам"))


async def try_bind(bot: Bot, db: Database, channel_id: int, chat_ref: int | str) -> str:
    """Привязывает запись канала к реальному чату. Возвращает текст результата."""
    try:
        chat = await bot.get_chat(chat_ref)
        me = await bot.get_chat_member(chat.id, bot.id)
    except (TelegramBadRequest, TelegramForbiddenError):
        return "<b>❌ Бот не видит этот канал.</b> Сначала добавь бота туда админом."
    if me.status != ChatMemberStatus.ADMINISTRATOR:
        return "<b>❌ Бот не админ в этом канале.</b> Выдай ему права администратора."
    if db.get_channel_by_chat(chat.id):
        return "<b>⚠️ Этот канал уже добавлен.</b>"
    db.bind_channel(channel_id, chat.id, chat.title)
    return f"<b>✅ Канал «{html.escape(chat.title or '')}» привязан!</b> Теперь подписка на него проверяется."


@router.message(AddChannel.link, F.text, not_command)
async def channel_link(message: Message, state: FSMContext, bot: Bot, db: Database):
    text = message.text.strip()
    username = None
    if text.startswith("@"):
        username = text
        link = f"https://t.me/{text[1:]}"
    elif text.startswith(("https://t.me/", "http://t.me/", "t.me/")):
        link = text if text.startswith("http") else f"https://{text}"
        tail = link.split("t.me/", 1)[1]
        if tail and not tail.startswith(("+", "joinchat/")):
            username = "@" + tail.split("/")[0]
    else:
        await message.answer("<b>Это не похоже на ссылку.</b> Пример: <code>https://t.me/+AbCdEf123</code>")
        return

    channel_id = db.add_channel(link)
    # Публичный канал, где бот уже админ, можно привязать сразу
    if username:
        result = await try_bind(bot, db, channel_id, username)
        if result.startswith("<b>✅"):
            await state.clear()
            await message.answer(result, reply_markup=back_kb("adm:channels", "◀️ К каналам"))
            return

    await state.set_state(AddChannel.bind)
    await state.update_data(channel_id=channel_id)
    await message.answer(BIND_CHANNEL_TEXT, reply_markup=back_kb("adm:channels", "◀️ К каналам"))


@router.message(AddChannel.bind, not_command)
async def channel_bind_manual(message: Message, state: FSMContext, bot: Bot, db: Database):
    channel_id = (await state.get_data()).get("channel_id")
    channel = db.get_channel(channel_id) if channel_id else None
    if not channel or channel["chat_id"]:
        await state.clear()
        await message.answer("<b>Канал уже привязан.</b>", reply_markup=back_kb("adm:channels", "◀️ К каналам"))
        return

    if isinstance(message.forward_origin, MessageOriginChannel):
        chat_ref = message.forward_origin.chat.id
    elif message.text and message.text.strip().lstrip("-").isdigit():
        chat_ref = int(message.text.strip())
    elif message.text and message.text.strip().startswith("@"):
        chat_ref = message.text.strip()
    else:
        await message.answer("<b>Перешли пост из канала</b> или отправь его ID / @username.")
        return

    result = await try_bind(bot, db, channel_id, chat_ref)
    if result.startswith("<b>✅"):
        await state.clear()
    await message.answer(result, reply_markup=back_kb("adm:channels", "◀️ К каналам"))


# ---------- кнопки из уведомления «бот стал админом» ----------

@router.callback_query(F.data.startswith("adm:bind:"))
async def cb_bind_pending(call: CallbackQuery, state: FSMContext, bot: Bot, db: Database):
    _, _, channel_id, chat_id = call.data.split(":")
    channel = db.get_channel(int(channel_id))
    if not channel or channel["chat_id"]:
        await show(call, "<b>Эта ссылка уже привязана или удалена.</b>", back_kb("adm:channels", "📢 Каналы"))
        return
    result = await try_bind(bot, db, int(channel_id), int(chat_id))
    if result.startswith("<b>✅"):
        await state.clear()
    await show(call, result, back_kb("adm:channels", "📢 Каналы"))


@router.callback_query(F.data.startswith("adm:bindnew:"))
async def cb_bind_new(call: CallbackQuery, bot: Bot, db: Database):
    _, _, chat_id, with_requests = call.data.split(":")
    chat_id = int(chat_id)
    if db.get_channel_by_chat(chat_id):
        await show(call, "<b>⚠️ Этот канал уже добавлен.</b>", back_kb("adm:channels", "📢 Каналы"))
        return
    try:
        chat = await bot.get_chat(chat_id)
        invite = await bot.create_chat_invite_link(
            chat_id, name="Бот", creates_join_request=with_requests == "1"
        )
    except (TelegramBadRequest, TelegramForbiddenError):
        await show(
            call,
            "<b>❌ Не получилось создать ссылку.</b> Дай боту право «Пригласительные ссылки».",
            back_kb("adm:channels", "📢 Каналы"),
        )
        return
    db.add_channel(invite.invite_link, chat_id, chat.title)
    await show(
        call,
        f"<b>✅ Канал «{html.escape(chat.title or '')}» добавлен!</b>\n\nСсылка кнопки: {invite.invite_link}",
        back_kb("adm:channels", "📢 Каналы"),
    )


@router.callback_query(F.data == "adm:bindskip")
async def cb_bind_skip(call: CallbackQuery):
    await show(call, "<b>Ок, канал не добавлен.</b>", back_kb())
