import logging
from datetime import datetime

from aiogram import Router, F, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.config import ADMIN_IDS, SUPPORT_FILE, bot
from src.database import load_json, save_json, add_to_history, get_countries, get_db_file

logger = logging.getLogger(__name__)
router = Router()

EXIT_CHAT_TEXT = "🚪 Выйти из чата"


class AdminReplyState(StatesGroup):
    admin_in_chat = State()


class BroadcastState(StatesGroup):
    waiting_for_message = State()


# ─── Клавиатуры ──────────────────────────────────────────────────────────────

def get_admin_main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📊 Статистика сервиса", callback_data="admin_stats"))
    builder.row(InlineKeyboardButton(text="📣 Сделать объявление", callback_data="admin_broadcast"))
    builder.row(InlineKeyboardButton(text="🔄 Проверка платежей", callback_data="admin_payments_0"))
    builder.row(InlineKeyboardButton(text="🔴 Истекшие подписки", callback_data="admin_expired_0"))
    builder.row(InlineKeyboardButton(text="💬 Обращения в поддержку", callback_data="admin_support_0"))
    builder.row(InlineKeyboardButton(text="🚪 Выйти из админки", callback_data="to_main_menu"))
    return builder.as_markup()


def _back_to_admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="to_admin_menu")]]
    )


# ─── Вход в панель ───────────────────────────────────────────────────────────

@router.message(Command("admin"))
async def cmd_admin(message: types.Message, state: FSMContext):
    data = await state.get_data()
    last_msg_id = data.get("last_menu_msg_id")

    try:
        await message.delete()
    except Exception:
        pass

    if message.from_user.id not in ADMIN_IDS:
        return
        
    await state.set_state(None)

    # Удаляем старое меню (пользовательское или админское)
    if last_msg_id:
        try:
            await bot.delete_message(chat_id=message.chat.id, message_id=last_msg_id)
        except Exception:
            pass

    sent_msg = await message.answer("🛠 <b>Панель управления IFX-VPN</b>", reply_markup=get_admin_main_menu(), parse_mode="HTML")
    await state.update_data(last_menu_msg_id=sent_msg.message_id)


@router.callback_query(F.data == "to_admin_menu")
async def callback_to_admin_menu(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer()
        return
    await state.set_state(None)
    await state.update_data(last_menu_msg_id=callback.message.message_id)
    try:
        await callback.message.edit_text(
            "🛠 <b>Панель управления IFX-VPN</b>",
            reply_markup=get_admin_main_menu(),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


# ─── Статистика ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_stats")
async def callback_admin_stats(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer()
        return
    countries = await get_countries()
    counts = {}
    total_slots = 0
    for country in countries:
        db_file = get_db_file(country["code"])
        db = await load_json(db_file)
        total_slots += len(db)
        for item in db:
            s = item.get("status", "unknown")
            counts[s] = counts.get(s, 0) + 1

    tickets = await load_json(SUPPORT_FILE)
    open_tickets = sum(1 for t in tickets if t.get("status") == "open")

    stats_text = (
        "📊 <b>СТАТУС КЛИЕНТСКОЙ БАЗЫ</b>\n\n"
        "👥 <b>Слоты:</b>\n"
        f"├ 🟢 Активные:       <b>{counts.get('active', 0)}</b>\n"
        f"├ 🟡 Триалы:         <b>{counts.get('trial', 0)}</b>\n"
        f"├ ⏳ На проверке:    <b>{counts.get('check', 0)}</b>\n"
        f"├ 🔴 Истекшие:       <b>{counts.get('expired', 0)}</b>\n"
        f"├ ⚪️ Свободные:      <b>{counts.get('inactive', 0)}</b>\n"
        f"└ ⚫️ Заблокированные: <b>{counts.get('banned', 0)}</b>\n\n"
        f"📦 Всего слотов: <b>{total_slots}</b>\n\n"
        "💬 <b>Поддержка:</b>\n"
        f"└ Открытых тикетов: <b>{open_tickets}</b>\n"
    )

    try:
        await callback.message.edit_text(stats_text, reply_markup=_back_to_admin_kb(), parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await callback.answer()

# ─── Рассылка (объявление) ───────────────────────────────────────────────────

@router.callback_query(F.data == "admin_broadcast")
async def callback_admin_broadcast(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer()
        return

    await state.set_state(BroadcastState.waiting_for_message)
    await state.update_data(last_menu_msg_id=callback.message.message_id)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⬅️ Отмена", callback_data="to_admin_menu"))
    try:
        await callback.message.edit_text(
            "📣 <b>Рассылка объявления</b>\n\n"
            "Отправьте сообщение, которое получат все пользователи бота.\n\n"
            "Поддерживаются: текст, фото, видео, документ.\n"
            "Форматирование HTML работает для текстовых сообщений.",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.message(BroadcastState.waiting_for_message)
async def process_broadcast_message(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    data = await state.get_data()
    last_menu_msg_id = data.get("last_menu_msg_id")

    # Собираем уникальных пользователей из всех стран
    countries = await get_countries()
    recipients: set[int] = set()
    for country in countries:
        db_file = get_db_file(country["code"])
        db = await load_json(db_file)
        for slot in db:
            if slot.get("telegram_id"):
                recipients.add(slot["telegram_id"])

    # Удаляем сообщение админа сразу — чистота чата
    try:
        await message.delete()
    except Exception:
        pass

    sent_count = 0
    failed_count = 0
    for uid in recipients:
        try:
            if message.photo:
                await bot.send_photo(
                    chat_id=uid,
                    photo=message.photo[-1].file_id,
                    caption=message.caption or "",
                    parse_mode="HTML",
                )
            elif message.video:
                await bot.send_video(
                    chat_id=uid,
                    video=message.video.file_id,
                    caption=message.caption or "",
                    parse_mode="HTML",
                )
            elif message.document:
                await bot.send_document(
                    chat_id=uid,
                    document=message.document.file_id,
                    caption=message.caption or "",
                    parse_mode="HTML",
                )
            elif message.text:
                await bot.send_message(
                    chat_id=uid,
                    text=message.text,
                    parse_mode="HTML",
                )
            else:
                continue
            sent_count += 1
        except TelegramForbiddenError:
            logger.warning(f"Broadcast: пользователь {uid} заблокировал бота.")
            failed_count += 1
        except Exception as e:
            logger.error(f"Broadcast: ошибка отправки в {uid}: {e}")
            failed_count += 1

    await state.set_state(None)

    result_text = (
        f"🛠 <b>Панель управления IFX-VPN</b>\n\n"
        f"📣 Рассылка завершена:\n"
        f"├ ✅ Доставлено: <b>{sent_count}</b>\n"
        f"└ ❌ Не доставлено: <b>{failed_count}</b>"
    )
    try:
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=last_menu_msg_id,
            text=result_text,
            reply_markup=get_admin_main_menu(),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass

# ─── Галерея платежей ────────────────────────────────────────────────────────

async def _render_payments_gallery(message: types.Message, current_idx: int) -> None:
    countries = await get_countries()
    check_slots = []
    for country in countries:
        db_file = get_db_file(country["code"])
        db = await load_json(db_file)
        for slot in db:
            if slot.get("status") == "check":
                slot["_country_code"]  = country["code"]
                slot["_country_flag"]  = country["flag"]
                slot["_country_label"] = country["label"]
                slot["_db_file"]       = db_file
                check_slots.append(slot)

    if not check_slots:
        try:
            await message.edit_text(
                "🔄 Платежей на проверке нет.",
                reply_markup=_back_to_admin_kb(),
            )
        except TelegramBadRequest:
            pass
        return

    current_idx = max(0, min(current_idx, len(check_slots) - 1))
    slot = check_slots[current_idx]
    country_code = slot["_country_code"]

    text = (
        f"🔄 <b>Проверка платежей ({current_idx + 1}/{len(check_slots)})</b>\n\n"
        f"▪️ Слот ID: <code>{slot['id']}</code>\n"
        f"▪️ Страна: {slot['_country_flag']} {slot['_country_label']}\n"
        f"▪️ Юзер: {slot.get('comment', '—')}\n"
        f"▪️ Действует до: {slot.get('expires_at', '—')}"
    )
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Одобрить ✅", callback_data=f"galapprove_{country_code}_{slot['id']}_{current_idx}"),
        InlineKeyboardButton(text="Отклонить ❌", callback_data=f"galdecline_{country_code}_{slot['id']}_{current_idx}"),
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ Пред.", callback_data=f"admin_payments_{current_idx - 1}"),
        InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"admin_payments_{current_idx + 1}"),
    )
    builder.row(InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="to_admin_menu"))
    try:
        await message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("admin_payments_"))
async def callback_admin_payments_gallery(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer()
        return
    current_idx = int(callback.data.split("_")[2])
    await _render_payments_gallery(callback.message, current_idx)
    await callback.answer()


@router.callback_query(F.data.startswith("galapprove_"))
async def callback_gallery_approve(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer()
        return
    parts = callback.data.split("_")
    country_code, slot_id, current_idx = parts[1], int(parts[2]), int(parts[3])

    db_file = get_db_file(country_code)
    db = await load_json(db_file)
    slot = next((item for item in db if item["id"] == slot_id), None)

    if not slot:
        await callback.answer("⚠️ Слот не найден в базе!", show_alert=True)
        await _render_payments_gallery(callback.message, current_idx)
        return

    if slot["status"] != "check":
        await callback.answer(
            f"⚠️ Этот чек уже обработан (статус: {slot['status']}).\nДругой администратор успел раньше.",
            show_alert=True,
        )
        await _render_payments_gallery(callback.message, current_idx)
        return

    slot["status"] = "active"
    await save_json(db_file, db)

    if slot.get("telegram_id"):
        await add_to_history(slot["telegram_id"], "PAID")
        countries = await get_countries()
        country = next((c for c in countries if c["code"] == country_code), None)
        country_label = f"{country['flag']} {country['label']}" if country else country_code
        try:
            await bot.send_message(
                chat_id=slot["telegram_id"],
                text=(
                    f"🎉 <b>Платёж одобрен!</b> Подписка активирована.\n"
                    f"🌍 Сервер: {country_label}\n\n"
                    f"🔑 Ваш ключ конфигурации:\n<code>{slot.get('vpn_key', '—')}</code>\n\n"
                    f"Вставьте его в приложение Amnezia VPN."
                ),
                parse_mode="HTML",
            )
        except TelegramForbiddenError:
            logger.warning(f"Пользователь {slot['telegram_id']} заблокировал бота.")
        except Exception as e:
            logger.error(f"Ошибка уведомления пользователя: {e}")

        from src.referral import on_payment_approved
        await on_payment_approved(slot["telegram_id"])

    await callback.answer("✅ Платёж одобрен!")
    await _render_payments_gallery(callback.message, current_idx)


@router.callback_query(F.data.startswith("galdecline_"))
async def callback_gallery_decline(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer()
        return
    parts = callback.data.split("_")
    country_code, slot_id, current_idx = parts[1], int(parts[2]), int(parts[3])

    db_file = get_db_file(country_code)
    db = await load_json(db_file)
    slot = next((item for item in db if item["id"] == slot_id), None)

    if not slot:
        await callback.answer("⚠️ Слот не найден!", show_alert=True)
        await _render_payments_gallery(callback.message, current_idx)
        return

    if slot["status"] != "check":
        await callback.answer(
            f"⚠️ Чек уже обработан (статус: {slot['status']}). Другой администратор успел раньше.",
            show_alert=True,
        )
        await _render_payments_gallery(callback.message, current_idx)
        return

    uid = slot.get("telegram_id")
    slot["status"] = "inactive"
    slot["telegram_id"] = None
    slot["expires_at"] = None
    slot["comment"] = ""
    await save_json(db_file, db)

    if uid:
        try:
            await bot.send_message(
                chat_id=uid,
                text="❌ <b>Оплата отклонена администратором.</b>\nПроверьте реквизиты или сумму и попробуйте снова.",
                parse_mode="HTML",
            )
        except TelegramForbiddenError:
            pass
        except Exception as e:
            logger.error(f"Ошибка уведомления при отклонении: {e}")

    await callback.answer("❌ Платёж отклонён.")
    await _render_payments_gallery(callback.message, current_idx)


# ─── Галерея истекших подписок ───────────────────────────────────────────────

async def _render_expired_gallery(message: types.Message, current_idx: int) -> None:
    countries = await get_countries()
    expired_slots = []
    for country in countries:
        db_file = get_db_file(country["code"])
        db = await load_json(db_file)
        for slot in db:
            if slot.get("status") == "expired":
                slot["_country_code"]  = country["code"]
                slot["_country_flag"]  = country["flag"]
                slot["_country_label"] = country["label"]
                slot["_db_file"]       = db_file
                expired_slots.append(slot)

    if not expired_slots:
        try:
            await message.edit_text("🔴 Истекших подписок нет.", reply_markup=_back_to_admin_kb())
        except TelegramBadRequest:
            pass
        return

    current_idx = max(0, min(current_idx, len(expired_slots) - 1))
    slot = expired_slots[current_idx]
    country_code = slot["_country_code"]

    text = (
        f"🔴 <b>Истекшие подписки ({current_idx + 1}/{len(expired_slots)})</b>\n\n"
        f"🌍 Страна: {slot['_country_flag']} {slot['_country_label']}\n"
        f"👤 Юзер: {slot.get('comment', '—')}\n"
        f"🆔 Слот: <code>{slot['id']}</code>\n"
        f"📅 Истёк: {slot.get('expires_at', '—')}"
    )
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Освободить слот 🔄", callback_data=f"amnezbanned_{country_code}_{slot['id']}_{current_idx}")
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ Пред.", callback_data=f"admin_expired_{current_idx - 1}"),
        InlineKeyboardButton(text="След. ➡️", callback_data=f"admin_expired_{current_idx + 1}"),
    )
    builder.row(InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="to_admin_menu"))
    try:
        await message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("admin_expired_"))
async def callback_admin_expired_gallery(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer()
        return
    current_idx = int(callback.data.split("_")[2])
    await _render_expired_gallery(callback.message, current_idx)
    await callback.answer()


@router.callback_query(F.data.startswith("amnezbanned_"))
async def callback_amnezia_free_slot(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer()
        return
    parts = callback.data.split("_")
    country_code, slot_id, current_idx = parts[1], int(parts[2]), int(parts[3])

    db_file = get_db_file(country_code)
    db = await load_json(db_file)
    slot = next((item for item in db if item["id"] == slot_id), None)

    if slot:
        slot.clear()
        slot.update({
            "id": slot_id,
            "vpn_key": "",
            "telegram_id": None,
            "status": "banned",
            "expires_at": None,
            "comment": "",
        })
        await save_json(db_file, db)
        await callback.answer("✅ Данные пользователя очищены, слот заблокирован.", show_alert=True)
    else:
        await callback.answer("⚠️ Слот не найден.", show_alert=True)

    await _render_expired_gallery(callback.message, current_idx)


# ─── Галерея тикетов поддержки ───────────────────────────────────────────────

async def _render_support_gallery(message: types.Message, current_idx: int) -> None:
    tickets = await load_json(SUPPORT_FILE)
    open_tickets = [t for t in tickets if t.get("status") == "open"]

    if not open_tickets:
        try:
            await message.edit_text("💬 Все обращения обработаны.", reply_markup=_back_to_admin_kb())
        except TelegramBadRequest:
            pass
        return

    current_idx = max(0, min(current_idx, len(open_tickets) - 1))
    ticket = open_tickets[current_idx]

    history_lines = [
        f"<b>{'Юзер' if m['sender'] == 'user' else 'Админ'}:</b> {m['text']}"
        for m in ticket.get("messages", [])
    ]
    chat_history_text = "\n".join(history_lines) or "—"

    if len(chat_history_text) > 3000:
        chat_history_text = "…" + chat_history_text[-3000:]

    text = (
        f"💬 <b>Тикет №{ticket['ticket_id']} ({current_idx + 1}/{len(open_tickets)})</b>\n"
        f"👤 От: {ticket['full_name']} (ID: <code>{ticket['telegram_id']}</code>)\n\n"
        f"📜 <b>Переписка:</b>\n{chat_history_text}"
    )

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✍️ Ответить", callback_data=f"tkreply_{ticket['ticket_id']}_{current_idx}"),
        InlineKeyboardButton(text="Закрыть ✅", callback_data=f"tkclose_{ticket['ticket_id']}_{current_idx}"),
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ Пред.", callback_data=f"admin_support_{current_idx - 1}"),
        InlineKeyboardButton(text="След. ➡️", callback_data=f"admin_support_{current_idx + 1}"),
    )
    builder.row(InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="to_admin_menu"))
    try:
        await message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("admin_support_"))
async def callback_admin_support_gallery(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer()
        return
    current_idx = int(callback.data.split("_")[2])
    await _render_support_gallery(callback.message, current_idx)
    await callback.answer()


@router.callback_query(F.data.startswith("tkclose_"))
async def callback_ticket_close(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer()
        return
    parts = callback.data.split("_")
    ticket_id, current_idx = int(parts[1]), int(parts[2])

    tickets = await load_json(SUPPORT_FILE)
    ticket = next((t for t in tickets if t["ticket_id"] == ticket_id), None)

    if ticket and ticket["status"] == "open":
        ticket["status"] = "closed"
        ticket["closed_at"] = datetime.now().isoformat()
        await save_json(SUPPORT_FILE, tickets)
        try:
            await bot.send_message(
                chat_id=ticket["telegram_id"],
                text="🔒 <b>Ваше обращение закрыто поддержкой.</b> Если вопрос остался — создайте новый тикет.",
                parse_mode="HTML",
            )
        except (TelegramForbiddenError, TelegramBadRequest):
            pass
        await callback.answer("Тикет закрыт!", show_alert=True)
    else:
        await callback.answer("⚠️ Тикет уже закрыт или не найден.", show_alert=True)

    await _render_support_gallery(callback.message, current_idx)


@router.callback_query(F.data.startswith("tkreply_"))
async def callback_ticket_reply_start(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer()
        return
    parts = callback.data.split("_")
    ticket_id, current_idx = int(parts[1]), int(parts[2])

    await state.set_state(AdminReplyState.admin_in_chat)
    await state.update_data(
        ticket_id=ticket_id,
        current_idx=current_idx,
    )

    admin_chat_kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=EXIT_CHAT_TEXT)]],
        resize_keyboard=True,
    )
    
    # Сохраняем ID сообщения об открытии чата
    sent_msg = await callback.message.answer(
        f"🟢 <b>Чат с тикетом №{ticket_id} открыт!</b>\n"
        f"Каждое сообщение отправляется пользователю напрямую.\n"
        f"Для выхода нажмите «{EXIT_CHAT_TEXT}».",
        reply_markup=admin_chat_kb,
        parse_mode="HTML",
    )
    await state.update_data(support_msg_ids=[sent_msg.message_id])
    
    await callback.answer()


@router.message(AdminReplyState.admin_in_chat, F.text)
async def process_admin_chat_messages(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    data = await state.get_data()

    # Запоминаем каждое новое отправленное сообщение/ответ админа в чате поддержки
    msg_ids = data.get("support_msg_ids", [])
    msg_ids.append(message.message_id)
    await state.update_data(support_msg_ids=msg_ids)

    if message.text == EXIT_CHAT_TEXT:
        last_msg_id = data.get("last_menu_msg_id")
        current_idx = data.get("current_idx", 0)

        await state.set_state(None)

        # Удаляем накопленные сообщения переписки (уведомления, ответы админа)
        for msg_id in msg_ids:
            try:
                await bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
            except Exception:
                pass

        try:
            await message.delete()
        except Exception:
            pass
            
        remove_msg = await message.answer("🔄", reply_markup=ReplyKeyboardRemove())
        try:
            await remove_msg.delete()
        except Exception:
            pass
        
        # Удаляем старую галерею, оставшуюся в истории
        if last_msg_id:
            try:
                await bot.delete_message(chat_id=message.chat.id, message_id=last_msg_id)
            except Exception:
                pass

        # Специальный класс, который перехватывает edit_text и делает send_message + update_data
        class _FakeMessage:
            def __init__(self, bot_inst, chat_id, state_ref):
                self._bot = bot_inst
                self.chat = type("c", (), {"id": chat_id})()
                self._state = state_ref

            async def edit_text(self, text, reply_markup=None, parse_mode=None):
                sent_msg = await self._bot.send_message(
                    chat_id=self.chat.id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                )
                await self._state.update_data(last_menu_msg_id=sent_msg.message_id)

        fake_msg = _FakeMessage(bot, message.chat.id, state)
        await _render_support_gallery(fake_msg, current_idx)
        return
    

    tickets = await load_json(SUPPORT_FILE)
    ticket = next((t for t in tickets if t["ticket_id"] == data.get("ticket_id")), None)

    if not ticket:
        await message.answer("⚠️ Тикет не найден.")
        await state.set_state(None)
        return

    if ticket["status"] != "open":
        await message.answer("⚠️ Тикет уже закрыт. Выйдите из чата.")
        return

    ticket.setdefault("messages", []).append({
        "sender": "admin",
        "text": message.text,
        "timestamp": datetime.now().isoformat(),
    })
    await save_json(SUPPORT_FILE, tickets)

    try:
        await bot.send_message(
            chat_id=ticket["telegram_id"],
            text=f"✉️ <b>Ответ поддержки:</b>\n\n{message.text}",
            parse_mode="HTML",
        )
        conf_msg = await message.answer("✈️ Отправлено.")
        msg_ids.append(conf_msg.message_id)
        await state.update_data(support_msg_ids=msg_ids)
    except TelegramForbiddenError:
        err_msg = await message.answer("⚠️ Пользователь заблокировал бота — сообщение не доставлено.")
        msg_ids.append(err_msg.message_id)
        await state.update_data(support_msg_ids=msg_ids)
    except Exception as e:
        logger.error(f"Ошибка отправки ответа по тикету: {e}")
        err_msg = await message.answer("⚠️ Сообщение сохранено в тикете, но не доставлено.")
        msg_ids.append(err_msg.message_id)
        await state.update_data(support_msg_ids=msg_ids)