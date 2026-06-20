import logging
from datetime import datetime, timedelta

from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram import Router, F, types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton

from src.config import REFERRAL_FILE, BOT_USERNAME, REFERRAL_TRIAL_BONUS, REFERRAL_PAID_BONUS, bot
from src.database import load_json, save_json

logger = logging.getLogger(__name__)
router = Router()





# ─── Вспомогательные функции ─────────────────────────────────────────────────

async def get_ref_record(tg_id: int) -> dict | None:
    """Возвращает запись пользователя из referrals.json или None."""
    refs = await load_json(REFERRAL_FILE)
    return next((r for r in refs if r.get("telegram_id") == tg_id), None)


async def ensure_ref_record(tg_id: int, referred_by: int | None = None) -> dict:
    """
    Возвращает запись пользователя, создавая её если нет.
    referred_by устанавливается только при создании — повторно не перезаписывается.
    """
    refs = await load_json(REFERRAL_FILE)
    record = next((r for r in refs if r.get("telegram_id") == tg_id), None)
    if record is None:
        record = {
            "telegram_id": tg_id,
            "referred_by": referred_by,
            "refs": [],
            "paid_refs": [],
            "bonus_days_earned": 0,
        }
        refs.append(record)
        await save_json(REFERRAL_FILE, refs)
        logger.info(f"Создана реферальная запись для {tg_id} (invited by {referred_by}).")
    return record


async def add_bonus_days(tg_id: int, days: int) -> str | None:
    """
    Прибавляет бонусные дни к подписке пользователя в clients.json.
    Если подписка истекла — считает от сегодня.
    Если слота нет — бонус не начисляется (пользователь ещё не покупал).
    Возвращает новую дату истечения в виде строки или None если слот не найден.
    """
    from src.database import get_countries, get_db_file
    countries = await get_countries()
    # Ищем активный слот пользователя по всем странам (берём первый активный)
    for country in countries:
        db_file = get_db_file(country["code"])
        db = await load_json(db_file)
        slot = next((s for s in db if s.get("telegram_id") == tg_id and s.get("status") in ("active", "trial")), None)
        if slot:
            try:
                current_exp = datetime.strptime(slot["expires_at"], "%Y-%m-%d")
                base = current_exp if current_exp > datetime.now() else datetime.now()
            except (TypeError, ValueError):
                base = datetime.now()
            new_exp = (base + timedelta(days=days)).strftime("%Y-%m-%d")
            slot["expires_at"] = new_exp
            await save_json(db_file, db)
            logger.info(f"Пользователю {tg_id} начислено +{days} бонусных дней. Новая дата: {new_exp}.")
            return new_exp

    logger.warning(f"add_bonus_days: активный слот для {tg_id} не найден — бонус не начислен.")
    return None


async def _update_bonus_days_earned(tg_id: int, days: int) -> None:
    """Обновляет счётчик bonus_days_earned в referrals.json."""
    refs = await load_json(REFERRAL_FILE)
    record = next((r for r in refs if r.get("telegram_id") == tg_id), None)
    if record:
        record["bonus_days_earned"] = record.get("bonus_days_earned", 0) + days
        await save_json(REFERRAL_FILE, refs)


async def _notify_referrer(referrer_id: int, text: str) -> None:
    """Отправляет уведомление пригласившему пользователю."""
    try:
        await bot.send_message(chat_id=referrer_id, text=text, parse_mode="HTML")
    except TelegramForbiddenError:
        logger.warning(f"Реферер {referrer_id} заблокировал бота — уведомление не доставлено.")
    except TelegramBadRequest as e:
        logger.error(f"TelegramBadRequest при уведомлении реферера {referrer_id}: {e}")
    except Exception as e:
        logger.error(f"Ошибка уведомления реферера {referrer_id}: {e}")


# ─── Публичные триггеры (вызываются из других модулей) ───────────────────────

async def on_trial_activated(new_user_id: int) -> None:
    """
    Вызывается из user.py после успешной выдачи триала.
    Начисляет +3 дня рефереру если он есть и ещё не получал бонус за этого реферала.
    """
    record = await get_ref_record(new_user_id)
    if not record or not record.get("referred_by"):
        return

    referrer_id = record["referred_by"]

    # Защита от повторного начисления
    refs = await load_json(REFERRAL_FILE)
    referrer_record = next((r for r in refs if r.get("telegram_id") == referrer_id), None)
    if not referrer_record:
        return
    if new_user_id in referrer_record.get("refs", []):
        logger.info(f"Бонус за триал реферала {new_user_id} уже начислен рефереру {referrer_id}.")
        return

    # Фиксируем реферала
    referrer_record.setdefault("refs", []).append(new_user_id)
    await save_json(REFERRAL_FILE, refs)

    # Начисляем дни
    new_exp = await add_bonus_days(referrer_id, REFERRAL_TRIAL_BONUS)
    await _update_bonus_days_earned(referrer_id, REFERRAL_TRIAL_BONUS)

    if new_exp:
        await _notify_referrer(
            referrer_id,
            f"🎉 <b>Твой друг активировал пробный период!</b>\n"
            f"Мы начислили тебе <b>+{REFERRAL_TRIAL_BONUS} дня</b> к подписке.\n"
            f"📅 Твоя подписка теперь до: <b>{new_exp}</b>",
        )
    else:
        await _notify_referrer(
            referrer_id,
            f"🎉 <b>Твой друг активировал пробный период!</b>\n"
            f"Бонус <b>+{REFERRAL_TRIAL_BONUS} дня</b> будет применён при активации твоей подписки.",
        )


async def on_payment_approved(new_user_id: int) -> None:
    """
    Вызывается из admin.py после одобрения оплаты.
    Начисляет +7 дней рефереру если он есть и ещё не получал бонус за оплату этого реферала.
    """
    record = await get_ref_record(new_user_id)
    if not record or not record.get("referred_by"):
        return

    referrer_id = record["referred_by"]

    # Защита от повторного начисления за оплату
    refs = await load_json(REFERRAL_FILE)
    referrer_record = next((r for r in refs if r.get("telegram_id") == referrer_id), None)
    if not referrer_record:
        return
    if new_user_id in referrer_record.get("paid_refs", []):
        logger.info(f"Бонус за оплату реферала {new_user_id} уже начислен рефереру {referrer_id}.")
        return

    # Фиксируем оплату реферала
    referrer_record.setdefault("paid_refs", []).append(new_user_id)
    await save_json(REFERRAL_FILE, refs)

    # Начисляем дни
    new_exp = await add_bonus_days(referrer_id, REFERRAL_PAID_BONUS)
    await _update_bonus_days_earned(referrer_id, REFERRAL_PAID_BONUS)

    if new_exp:
        await _notify_referrer(
            referrer_id,
            f"🚀 <b>Твой друг оплатил подписку!</b>\n"
            f"Мы начислили тебе <b>+{REFERRAL_PAID_BONUS} дней</b> к подписке.\n"
            f"📅 Твоя подписка теперь до: <b>{new_exp}</b>",
        )
    else:
        await _notify_referrer(
            referrer_id,
            f"🚀 <b>Твой друг оплатил подписку!</b>\n"
            f"Бонус <b>+{REFERRAL_PAID_BONUS} дней</b> будет применён при активации твоей подписки.",
        )


# ─── Хендлер: страница реферальной программы ─────────────────────────────────

@router.callback_query(F.data == "user_referral")
async def callback_user_referral(callback: types.CallbackQuery):
    tg_id = callback.from_user.id
    record = await get_ref_record(tg_id)

    refs_count = len(record.get("refs", [])) if record else 0
    bonus_earned = record.get("bonus_days_earned", 0) if record else 0
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{tg_id}"

    text = (
        "🎁 <b>Реферальная программа IFX-VPN</b>\n\n"
        "Приглашай друзей и пользуйся VPN бесплатно!\n\n"
        "Делись своей персональной ссылкой. За каждого друга, "
        "который активирует пробный период, ты получаешь <b>+3 дня</b>.\n"
        "А если друг купит подписку — ещё <b>+7 дней</b>! 🚀\n\n"
        f"🔗 <b>Твоя реферальная ссылка:</b>\n<code>{ref_link}</code>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"├ 👥 Приглашено друзей: <b>{refs_count}</b>\n"
        f"└ 🎁 Всего заработано дней: <b>{bonus_earned}</b>"
    )

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="to_main_menu"))

    try:
        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await callback.answer()
