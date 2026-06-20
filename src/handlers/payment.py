"""
payment.py — Биллинг: выбор страны, метода оплаты, приём скриншота.

ОБНОВЛЕНО:
  - Добавлен экран выбора страны (динамически из countries.json).
  - Каждая покупка — всегда новый слот в выбранной стране.
  - Продление конкретного ключа инициируется из личного кабинета
    с передачей country_code через FSM state.
  - Страна сохраняется в FSM и используется при резервировании слота.
"""

import logging
from datetime import datetime, timedelta

from aiogram import Router, F, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.config import (
    PRICE_RUB, PRICE_USDT, REKVIZITY_SBP, WALLETS,
    ADMIN_IDS, SUBSCRIPTION_DAYS, bot,
)
from src.database import (
    load_json, save_json,
    get_available_countries, get_db_file,
)

logger = logging.getLogger(__name__)
router = Router()


class PaymentState(StatesGroup):
    waiting_for_country    = State()
    waiting_for_screenshot = State()


# ─── Клавиатуры ──────────────────────────────────────────────────────────────

async def get_country_menu(back_cb: str = "to_main_menu") -> InlineKeyboardMarkup:
    """Строит меню стран динамически — только те где есть свободные слоты."""
    countries = await get_available_countries()
    builder = InlineKeyboardBuilder()
    for c in countries:
        builder.row(InlineKeyboardButton(
            text=f"{c['flag']} {c['label']}",
            callback_data=f"country_{c['code']}",
        ))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb))
    return builder.as_markup()


def get_payment_method_menu(back_cb: str = "user_buy") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=f"📲 Рубли ({PRICE_RUB:.0f} руб)", callback_data="pay_sbp"))
    if WALLETS:
        builder.row(InlineKeyboardButton(text=f"🪙 USDT ({PRICE_USDT} $)", callback_data="pay_usdt"))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb))
    return builder.as_markup()


def get_crypto_networks_menu() -> InlineKeyboardMarkup:
    _LABELS = {"trc20": "TRC-20", "erc20": "ERC-20", "bep20": "BEP-20", "ton": "TON"}
    builder = InlineKeyboardBuilder()
    buttons = [
        InlineKeyboardButton(text=_LABELS[net], callback_data=f"net_{net}")
        for net in _LABELS if net in WALLETS
    ]
    for i in range(0, len(buttons), 2):
        builder.row(*buttons[i:i + 2])
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="pay_method_back"))
    return builder.as_markup()


def _back_to_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад в главное меню", callback_data="to_main_menu")]]
    )


def _moderation_kb(slot_id: int, country_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="Одобрить ✅", callback_data=f"galapprove_{country_code}_{slot_id}_0"),
            InlineKeyboardButton(text="Отклонить ❌", callback_data=f"galdecline_{country_code}_{slot_id}_0"),
        ]]
    )


# ─── Вход в покупку ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "user_buy")
async def callback_user_buy(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    countries = await get_available_countries()
    if not countries:
        try:
            await callback.message.edit_text(
                "❌ <b>Свободных мест нет ни в одной стране.</b>\n"
                "Обратитесь в поддержку.",
                reply_markup=_back_to_menu_kb(),
                parse_mode="HTML",
            )
        except TelegramBadRequest:
            pass
        await callback.answer()
        return

    try:
        await callback.message.edit_text(
            f"🌍 <b>Выберите страну сервера</b>\n\n"
            f"Стоимость: <b>{PRICE_RUB:.0f} руб</b> или <b>{PRICE_USDT} USDT</b> / {SUBSCRIPTION_DAYS} дней",
            reply_markup=await get_country_menu(),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass
    await state.set_state(PaymentState.waiting_for_country)
    await callback.answer()


@router.callback_query(PaymentState.waiting_for_country, F.data.startswith("country_"))
async def callback_select_country(callback: types.CallbackQuery, state: FSMContext):
    country_code = callback.data.split("_", 1)[1]
    await state.update_data(country_code=country_code)

    try:
        await callback.message.edit_text(
            f"💳 <b>Выберите метод оплаты</b>",
            reply_markup=get_payment_method_menu(back_cb="user_buy"),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


# ─── Обратная навигация из метода оплаты к выбору страны ─────────────────────

@router.callback_query(F.data == "pay_method_back")
async def callback_pay_method_back(callback: types.CallbackQuery, state: FSMContext):
    try:
        await callback.message.edit_text(
            f"🌍 <b>Выберите страну сервера</b>\n\n"
            f"Стоимость: <b>{PRICE_RUB:.0f} руб</b> или <b>{PRICE_USDT} USDT</b> / {SUBSCRIPTION_DAYS} дней",
            reply_markup=await get_country_menu(),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass
    await state.set_state(PaymentState.waiting_for_country)
    await callback.answer()


# ─── Методы оплаты ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "pay_sbp")
async def callback_pay_sbp(callback: types.CallbackQuery, state: FSMContext):
    text = (
        f"💵 <b>Оплата рублями через СБП</b>\n"
        f"Сумма: <code>{PRICE_RUB} руб.</code>\n\n"
        f"{REKVIZITY_SBP}\n\n"
        f"⚠️ После перевода пришлите <b>скриншот чека</b> следующим сообщением."
    )
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="pay_method_back"))
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
    except TelegramBadRequest:
        pass
    await state.set_state(PaymentState.waiting_for_screenshot)
    await callback.answer()


@router.callback_query(F.data == "pay_usdt")
async def callback_pay_usdt(callback: types.CallbackQuery):
    try:
        await callback.message.edit_text(
            "🪙 <b>Выберите блокчейн-сеть для перевода USDT:</b>",
            reply_markup=get_crypto_networks_menu(),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("net_"))
async def callback_crypto_net(callback: types.CallbackQuery, state: FSMContext):
    net = callback.data.split("_")[1]
    wallet = WALLETS.get(net, "⚠️ Кошелёк не найден")
    text = (
        f"🪙 <b>Оплата USDT ({net.upper()})</b>\n"
        f"Сумма: <code>{PRICE_USDT} USDT</code>\n\n"
        f"Адрес кошелька:\n<code>{wallet}</code>\n\n"
        f"⚠️ После отправки пришлите <b>скриншот транзакции</b> следующим сообщением."
    )
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="pay_usdt"))
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
    except TelegramBadRequest:
        pass
    await state.set_state(PaymentState.waiting_for_screenshot)
    await callback.answer()


# ─── Приём скриншота ─────────────────────────────────────────────────────────

@router.message(PaymentState.waiting_for_screenshot, F.photo)
async def process_screenshot(message: types.Message, state: FSMContext):
    tg_id = message.from_user.id
    photo_id = message.photo[-1].file_id

    data = await state.get_data()
    country_code = data.get("country_code")

    if not country_code:
        await message.answer(
            "⚠️ Страна не выбрана. Пожалуйста, начните покупку заново.",
            reply_markup=_back_to_menu_kb(),
        )
        await state.clear()
        return

    db_file = get_db_file(country_code)
    db = await load_json(db_file)

    # Каждая покупка — новый слот (поддержка нескольких ключей)
    free_slot = next((s for s in db if s.get("status") == "inactive"), None)
    if not free_slot:
        await message.answer(
            f"🚨 <b>Свободных слотов для этой страны нет.</b>\n"
            f"Выберите другую страну или обратитесь в поддержку.",
            parse_mode="HTML",
            reply_markup=_back_to_menu_kb(),
        )
        await state.clear()
        return

    free_slot["telegram_id"] = tg_id
    free_slot["status"] = "check"
    free_slot["expires_at"] = (datetime.now() + timedelta(days=SUBSCRIPTION_DAYS)).strftime("%Y-%m-%d")
    free_slot["comment"] = f"@{message.from_user.username or ''} ({message.from_user.first_name})"

    # Сохраняем ДО уведомлений
    await save_json(db_file, db)
    await state.clear()

    await message.answer(
        "📥 <b>Чек принят!</b>\n\n"
        "Администратор проверит оплату и активирует подписку. "
        "Вы получите уведомление, когда всё будет готово.\n\n"
        "Обычно это занимает до 30 минут в рабочее время.",
        parse_mode="HTML",
        reply_markup=_back_to_menu_kb(),
    )

    # Уведомление администраторам
    from src.database import get_countries
    countries = await get_countries()
    country = next((c for c in countries if c["code"] == country_code), None)
    country_label = f"{country['flag']} {country['label']}" if country else country_code

    admin_text = (
        f"💰 <b>Новый платёж на проверку!</b>\n"
        f"👤 Юзер: {free_slot['comment']} (ID: <code>{tg_id}</code>)\n"
        f"🌍 Страна: {country_label}\n"
        f"🆔 Слот: <code>{free_slot['id']}</code>"
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_photo(
                chat_id=admin_id,
                photo=photo_id,
                caption=admin_text,
                reply_markup=_moderation_kb(free_slot["id"], country_code),
                parse_mode="HTML",
            )
        except TelegramForbiddenError:
            logger.error(f"Админ {admin_id} заблокировал бота.")
        except Exception as e:
            logger.error(f"Ошибка уведомления админа {admin_id}: {e}")


@router.message(PaymentState.waiting_for_screenshot, ~F.photo)
async def process_screenshot_not_photo(message: types.Message):
    await message.answer(
        "📸 Пожалуйста, пришлите <b>скриншот чека как фото</b> (не файлом).",
        parse_mode="HTML",
    )
