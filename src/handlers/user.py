from datetime import datetime, date
import logging

from aiogram import Router, F, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
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

from src.config import SUPPORT_FILE, ADMIN_IDS, TRIAL_DAYS, bot
from src.database import load_json, save_json, has_used_trial_or_paid, add_to_history, get_user_slots, get_db_file, get_available_countries
from src.handlers.referral import ensure_ref_record, on_trial_activated

logger = logging.getLogger(__name__)
router = Router()

EXIT_CHAT_TEXT = "🚪 Выйти из чата"


class SupportState(StatesGroup):
    waiting_for_issue = State()
    user_in_chat = State()


# ─── Клавиатуры ──────────────────────────────────────────────────────────────

def get_user_main_menu(show_trial: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if show_trial:
        builder.row(InlineKeyboardButton(text="🎁 Получить пробный период (3 дня)", callback_data="user_trial"))
    builder.row(InlineKeyboardButton(text="💼 Личный кабинет / Подписка", callback_data="user_cabinet"))
    builder.row(InlineKeyboardButton(text="💳 Продлить / Купить подписку", callback_data="user_buy"))
    builder.row(InlineKeyboardButton(text="👥 Реферальная программа", callback_data="user_referral"))
    builder.row(InlineKeyboardButton(text="💬 Написать поддержке", callback_data="user_support"))
    builder.row(InlineKeyboardButton(text="ℹ️ Как настроить VPN?", callback_data="user_instruction"))
    return builder.as_markup()


def get_chat_exit_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=EXIT_CHAT_TEXT)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

# ─── Вспомогательные функции ──────────────────────────────────────────────────

async def _build_main_menu_for(tg_id: int) -> InlineKeyboardMarkup:
    slots = await get_user_slots(tg_id)
    show_trial = not await has_used_trial_or_paid(tg_id) and len(slots) == 0
    return get_user_main_menu(show_trial)


async def _exit_support_chat(message: types.Message, state: FSMContext) -> None:
    """Общий выход из чата поддержки — удаляем переписку и переносим меню вниз."""
    data = await state.get_data()
    last_msg_id = data.get("last_menu_msg_id")
    support_msg_ids = data.get("support_msg_ids", [])

    await state.set_state(None)
    tg_id = message.from_user.id

    # Удаляем накопленные сообщения переписки (сообщения юзера и ответы бота)
    for msg_id in support_msg_ids:
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

    if last_msg_id:
        try:
            await bot.delete_message(chat_id=message.chat.id, message_id=last_msg_id)
        except Exception:
            pass

    sent_msg = await message.answer(
        "🛠 <b>Главное меню:</b>",
        reply_markup=await _build_main_menu_for(tg_id),
        parse_mode="HTML",
    )
    await state.update_data(last_menu_msg_id=sent_msg.message_id)


# ─── /start и главное меню ────────────────────────────────────────────────────

@router.message(CommandStart())
async def command_start(message: types.Message, state: FSMContext):
    data = await state.get_data()
    last_msg_id = data.get("last_menu_msg_id")

    await state.set_state(None)

    try:
        await message.delete()
    except Exception:
        pass

    # Удаляем прошлое меню перед отправкой нового
    if last_msg_id:
        try:
            await bot.delete_message(chat_id=message.chat.id, message_id=last_msg_id)
        except Exception:
            pass

    # Обработка реферального аргумента (/start ref_123456789)
    tg_id = message.from_user.id
    args = message.text.split(maxsplit=1)
    referred_by: int | None = None
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            referrer_id = int(args[1][4:])
            if referrer_id != tg_id:  # нельзя пригласить самого себя
                referred_by = referrer_id
        except ValueError:
            pass

    # Создаём реферальную запись (если уже есть — referred_by не перезапишется)
    await ensure_ref_record(tg_id, referred_by)

    sent_msg = await message.answer(
        "👋 <b>Добро пожаловать в IFX-VPN!</b>\n\nНадёжный, быстрый и приватный VPN прямо в Telegram.",
        reply_markup=await _build_main_menu_for(tg_id),
        parse_mode="HTML",
    )
    await state.update_data(last_menu_msg_id=sent_msg.message_id)


@router.callback_query(F.data == "to_main_menu")
async def callback_to_main_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(None)
    await state.update_data(last_menu_msg_id=callback.message.message_id)
    try:
        await callback.message.edit_text(
            "🛠 <b>Главное меню:</b>",
            reply_markup=await _build_main_menu_for(callback.from_user.id),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


# ─── Инструкция ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "user_instruction")
async def callback_user_instruction(callback: types.CallbackQuery):
    text = (
        "ℹ️ <b>Как настроить Amnezia VPN</b>\n\n"
        "1. Скачайте приложение <b>AmneziaVPN</b> с официального сайта <code>amnezia.org</code>\n"
        "2. Откройте приложение и нажмите <b>«Добавить конфигурацию»</b>\n"
        "3. Вставьте ключ из вашего личного кабинета\n"
        "4. Нажмите <b>«Подключить»</b>\n\n"
        "❓ Если возникли трудности — обратитесь в поддержку."
    )
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="to_main_menu"))
    try:
        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await callback.answer()


# ─── Личный кабинет ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "user_cabinet")
async def callback_user_cabinet(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(None)
    await state.update_data(last_menu_msg_id=callback.message.message_id)
    await _render_cabinet_gallery(callback.message, callback.from_user.id, 0)
    await callback.answer()


@router.callback_query(F.data.startswith("cabinet_page_"))
async def callback_cabinet_page(callback: types.CallbackQuery, state: FSMContext):
    idx = int(callback.data.split("_")[2])
    await _render_cabinet_gallery(callback.message, callback.from_user.id, idx)
    await callback.answer()


async def _render_cabinet_gallery(message: types.Message, tg_id: int, idx: int) -> None:
    status_map = {
        "trial":   "🟡 Пробный период",
        "active":  "🟢 Активна",
        "check":   "⏳ Проверяется",
        "expired": "🔴 Истекла",
        "banned":  "⚫️ Заблокирована",
    }

    slots = await get_user_slots(tg_id)
    builder = InlineKeyboardBuilder()

    if not slots:
        text = (
            "💼 <b>Личный кабинет</b>\n\n"
            "У вас пока нет подписок.\n"
            "Получите пробный период или купите подписку в меню."
        )
        builder.row(InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="to_main_menu"))
        try:
            await message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        except TelegramBadRequest:
            pass
        return

    idx = max(0, min(idx, len(slots) - 1))
    slot = slots[idx]

    status     = slot.get("status", "inactive")
    expires_at = slot.get("expires_at")
    vpn_key    = slot.get("vpn_key", "")
    flag       = slot.get("country_flag", "🌍")
    label      = slot.get("country_label", slot.get("country_code", "—"))

    status_str = status_map.get(status, "⚪️ Неактивна")

    days_left_str = "—"
    if expires_at:
        try:
            exp_date = datetime.strptime(expires_at, "%Y-%m-%d").date()
            delta = (exp_date - date.today()).days
            days_left_str = f"{max(0, delta)} дн."
        except ValueError:
            days_left_str = "ошибка даты"

    text = (
        f"💼 <b>Личный кабинет IFX-VPN</b>  ({idx + 1}/{len(slots)})\n\n"
        f"┌───────────────┐\n"
        f"  {flag} Страна:    <b>{label}</b>\n"
        f"  Статус:  <b>{status_str}</b>\n"
        f"  Осталось:  <b>{days_left_str}</b>\n"
        f"  До:  <b>{expires_at or '—'}</b>\n"
        f"└───────────────┘\n\n"
    )

    if vpn_key and status in ("active", "trial"):
        text += f"🔑 <b>Ключ (нажмите для копирования):</b>\n<code>{vpn_key}</code>\n\n"
    elif status == "check":
        text += "⏳ Чек на проверке у администратора. Ожидайте уведомление.\n\n"
    elif status == "expired":
        text += "🔴 Подписка истекла. Продлите в разделе «Купить/Продлить».\n\n"
    elif status == "banned":
        text += "⛔️ Пробный период завершён. Оформите платную подписку.\n\n"

    # Кнопка продления только для активных/истёкших
    if status in ("active", "trial", "expired"):
        builder.row(InlineKeyboardButton(
            text="💳 Продлить эту подписку",
            callback_data=f"renew_{slot['country_code']}",
        ))

    # Навигация
    nav_buttons = []
    if idx > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ Пред.", callback_data=f"cabinet_page_{idx - 1}"))
    if idx < len(slots) - 1:
        nav_buttons.append(InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"cabinet_page_{idx + 1}"))
    if nav_buttons:
        builder.row(*nav_buttons)

    builder.row(InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="to_main_menu"))
    try:
        await message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("renew_"))
async def callback_renew_slot(callback: types.CallbackQuery, state: FSMContext):
    """Продление конкретного ключа — передаём country_code в FSM и кидаем в оплату."""
    country_code = callback.data.split("_", 1)[1]
    await state.update_data(country_code=country_code)
    # Перенаправляем в флоу оплаты — выбор метода (страна уже выбрана)
    from src.handlers.payment import get_payment_method_menu
    try:
        await callback.message.edit_text(
            "💳 <b>Выберите метод оплаты для продления</b>",
            reply_markup=get_payment_method_menu(back_cb="user_cabinet"),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass
    from src.handlers.payment import PaymentState
    await state.set_state(PaymentState.waiting_for_screenshot)
    await callback.answer()


# ─── Триал ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "user_trial")
async def callback_user_trial(callback: types.CallbackQuery):
    from datetime import timedelta

    tg_id = callback.from_user.id

    added = await add_to_history(tg_id, "TRIAL_CHECK")
    if not added:
        await callback.answer("❌ Вы уже использовали пробный период!", show_alert=True)
        return

    # Ищем любую страну со свободным слотом
    available = await get_available_countries()
    if not available:
        await callback.message.answer(
            "🚨 Свободных мест для триала нет. Обратитесь в поддержку.",
            parse_mode="HTML",
        )
        await callback.answer()
        return

    # Берём первую доступную страну
    country = available[0]
    db_file = get_db_file(country["code"])
    db = await load_json(db_file)

    free_slot = next((s for s in db if s.get("status") == "inactive"), None)
    if not free_slot:
        await callback.message.answer(
            "🚨 Свободных мест для триала нет. Обратитесь в поддержку.",
            parse_mode="HTML",
        )
        await callback.answer()
        return

    free_slot["telegram_id"] = tg_id
    free_slot["status"] = "trial"
    free_slot["expires_at"] = (datetime.now() + timedelta(days=TRIAL_DAYS)).strftime("%Y-%m-%d")
    free_slot["comment"] = f"@{callback.from_user.username or ''} ({callback.from_user.first_name}) [TRIAL]"

    await save_json(db_file, db)

    await callback.message.answer(
        f"🎁 <b>Пробный период на {TRIAL_DAYS} дня активирован!</b>\n"
        f"🌍 Сервер: {country['flag']} {country['label']}\n\n"
        f"🔑 Ваш ключ доступа:\n<code>{free_slot['vpn_key']}</code>\n\n"
        f"Скопируйте его и вставьте в приложение Amnezia VPN.",
        parse_mode="HTML",
    )
    await callback.answer()

    await on_trial_activated(tg_id)


# ─── Поддержка: открытие ─────────────────────────────────────────────────────

@router.callback_query(F.data == "user_support")
async def callback_user_support(callback: types.CallbackQuery, state: FSMContext):
    tg_id = callback.from_user.id
    tickets = await load_json(SUPPORT_FILE)
    active_ticket = next(
        (t for t in tickets if t.get("telegram_id") == tg_id and t.get("status") == "open"), None
    )

    await state.update_data(last_menu_msg_id=callback.message.message_id)

    if active_ticket:
        await state.set_state(SupportState.user_in_chat)
        await state.update_data(ticket_id=active_ticket["ticket_id"])

        history_lines = [
            f"<b>{'Вы' if m['sender'] == 'user' else 'Поддержка'}:</b> {m['text']}"
            for m in active_ticket.get("messages", [])[-3:]
        ]
        history_text = "\n".join(history_lines) or "История пуста."

        sent_msg = await callback.message.answer(
            f"🔄 <b>Ваш активный чат с поддержкой восстановлен!</b>\n\n"
            f"💬 <b>Последние сообщения:</b>\n{history_text}\n\n"
            f"✍️ Пишите сюда — операторы сразу получат.",
            reply_markup=get_chat_exit_keyboard(),
            parse_mode="HTML",
        )
        
        # Получаем актуальные данные и сохраняем ID сообщения
        data = await state.get_data()
        support_msg_ids = data.get("support_msg_ids", [])
        support_msg_ids.append(sent_msg.message_id)
        await state.update_data(support_msg_ids=support_msg_ids)
        
        await callback.answer()
        return

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⬅️ Отмена", callback_data="to_main_menu"))
    try:
        await callback.message.edit_text(
            "💬 <b>Служба поддержки IFX-VPN</b>\n\n"
            "Опишите проблему одним сообщением. Операторы ответят в ближайшее время.",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass
    
    # Сохраняем ID сообщения с текстом поддержки, чтобы потом удалить его
    data = await state.get_data()
    support_msg_ids = data.get("support_msg_ids", [])
    support_msg_ids.append(callback.message.message_id)
    await state.update_data(support_msg_ids=support_msg_ids)

    await state.set_state(SupportState.waiting_for_issue)
    await callback.answer()


# ─── Поддержка: первое сообщение (создание тикета) ──────────────────────────

@router.message(SupportState.waiting_for_issue, F.text)
async def process_support_issue(message: types.Message, state: FSMContext):
    if message.text == EXIT_CHAT_TEXT:
        await _exit_support_chat(message, state)
        return

    tg_id = message.from_user.id
    tickets = await load_json(SUPPORT_FILE)

    existing_ids = {t["ticket_id"] for t in tickets if "ticket_id" in t}
    ticket_id = max(existing_ids, default=0) + 1

    new_ticket = {
        "ticket_id": ticket_id,
        "telegram_id": tg_id,
        "username": message.from_user.username or "",
        "full_name": message.from_user.full_name,
        "status": "open",
        "created_at": datetime.now().isoformat(),
        "messages": [{"sender": "user", "text": message.text, "timestamp": datetime.now().isoformat()}],
    }
    tickets.append(new_ticket)
    await save_json(SUPPORT_FILE, tickets)

    await state.set_state(SupportState.user_in_chat)
    await state.update_data(ticket_id=ticket_id)

    sent_msg = await message.answer(
        "✅ <b>Обращение зарегистрировано.</b>\n"
        "Чат открыт — пишите дополнения прямо сюда. "
        "Когда закончите — нажмите «Выйти из чата».",
        reply_markup=get_chat_exit_keyboard(),
        parse_mode="HTML",
    )
    await state.update_data(last_menu_msg_id=sent_msg.message_id)

    # Запоминаем ID сообщения юзера и ответа бота для последующего удаления
    data = await state.get_data()
    msg_ids = data.get("support_msg_ids", [])
    msg_ids.extend([message.message_id, sent_msg.message_id])
    await state.update_data(support_msg_ids=msg_ids)

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=f"💬 <b>Новый тикет №{ticket_id}!</b>\n👤 От: {message.from_user.full_name}\n\nОткройте админ-панель.",
                parse_mode="HTML",
            )
        except (TelegramForbiddenError, TelegramBadRequest):
            pass
        except Exception as e:
            logger.error(f"Ошибка уведомления админа {admin_id}: {e}")


# ─── Поддержка: активный чат (последующие сообщения) ────────────────────────

@router.message(SupportState.user_in_chat, F.text)
async def process_user_chat_message(message: types.Message, state: FSMContext):
    if message.text == EXIT_CHAT_TEXT:
        await _exit_support_chat(message, state)
        return

    data = await state.get_data()
    
    # Запоминаем каждое новое отправленное пользователем сообщение в чате поддержки
    msg_ids = data.get("support_msg_ids", [])
    msg_ids.append(message.message_id)
    await state.update_data(support_msg_ids=msg_ids)

    tickets = await load_json(SUPPORT_FILE)
    ticket = next((t for t in tickets if t["ticket_id"] == data.get("ticket_id")), None)

    if not ticket:
        await message.answer("⚠️ Тикет не найден. Возможно, он был закрыт. Обратитесь в поддержку заново.")
        await state.set_state(None)
        return

    if ticket["status"] != "open":
        await message.answer(
            "🔒 Ваш тикет уже закрыт оператором. Если вопрос остался — создайте новое обращение через меню."
        )
        await state.set_state(None)
        return

    ticket.setdefault("messages", []).append({
        "sender": "user",
        "text": message.text,
        "timestamp": datetime.now().isoformat(),
    })
    await save_json(SUPPORT_FILE, tickets)

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=(
                    f"✉️ <b>Новое сообщение в тикет №{ticket['ticket_id']}!</b>\n"
                    f"👤 Юзер: {ticket['full_name']}\n"
                    f"📝 Текст: <i>{message.text[:300]}</i>"
                ),
                parse_mode="HTML",
            )
        except (TelegramForbiddenError, TelegramBadRequest):
            pass
        except Exception as e:
            logger.error(f"Ошибка уведомления админа {admin_id}: {e}")