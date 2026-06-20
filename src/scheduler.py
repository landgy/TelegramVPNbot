"""
scheduler.py — Ежедневный планировщик проверки подписок.
Обходит все страны из countries.json.
"""

import logging
from datetime import datetime, date

from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from src.config import bot, ADMIN_IDS, REMINDER_DAYS
from src.database import load_json, save_json, get_countries, get_db_file

logger = logging.getLogger(__name__)


def _renew_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="💳 Продлить подписку", callback_data="user_buy")]]
    )


async def daily_subscription_checker() -> None:
    logger.info("▶ Запуск ежедневного планировщика подписок…")
    countries = await get_countries()
    today = date.today()

    for country in countries:
        db_file = get_db_file(country["code"])
        db = await load_json(db_file)
        changed = False

        for slot in db:
            slot_id = slot.get("id", "?")
            try:
                status = slot.get("status")
                if status not in ("active", "trial", "expired"):
                    continue

                raw_exp = slot.get("expires_at")
                if not raw_exp:
                    logger.warning(f"[{country['code']}] Слот {slot_id}: нет expires_at, пропускаю.")
                    continue

                try:
                    exp_date = datetime.strptime(raw_exp, "%Y-%m-%d").date()
                except ValueError:
                    logger.error(f"[{country['code']}] Слот {slot_id}: невалидный expires_at='{raw_exp}'.")
                    continue

                uid = slot.get("telegram_id")
                if not uid:
                    if exp_date <= today and status in ("active", "trial"):
                        slot["status"] = "expired"
                        changed = True
                    continue

                days_left = (exp_date - today).days

                if days_left <= 0 and status in ("active", "trial"):
                    slot["status"] = "expired"
                    changed = True
                    sent = await _safe_send(
                        uid,
                        f"🔴 <b>Подписка истекла.</b> Сервер: {country['flag']} {country['label']}.\n"
                        f"Доступ к VPN приостановлен. Для возобновления — оплатите в главном меню.",
                        reply_markup=_renew_kb(),
                    )
                    if not sent:
                        logger.warning(f"[{country['code']}] Слот {slot_id}: не удалось уведомить uid={uid}.")
                    for admin_id in ADMIN_IDS:
                        await _safe_send(
                            admin_id,
                            f"🚨 <b>Истекла подписка!</b>\n"
                            f"🌍 Страна: {country['flag']} {country['label']}\n"
                            f"Слот: <code>{slot_id}</code>\n"
                            f"Пользователь: {slot.get('comment', '—')} (ID: <code>{uid}</code>)",
                        )
                elif days_left in REMINDER_DAYS and status in ("active", "trial"):
                    await _safe_send(
                        uid,
                        f"⏳ <b>Напоминание:</b> до окончания VPN-подписки "
                        f"({country['flag']} {country['label']}) осталось "
                        f"<b>{days_left} {'день' if days_left == 1 else 'дня'}</b>. Продлите, чтобы не потерять доступ.",
                        reply_markup=_renew_kb(),
                    )

            except Exception as e:
                logger.error(f"[{country['code']}] Ошибка слота {slot_id}: {e}", exc_info=True)

        if changed:
            await save_json(db_file, db)
            logger.info(f"✔ [{country['code']}] БД обновлена.")
        else:
            logger.info(f"✔ [{country['code']}] Изменений нет.")


async def _safe_send(chat_id: int, text: str, reply_markup=None) -> bool:
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=reply_markup)
        return True
    except TelegramForbiddenError:
        logger.warning(f"Пользователь {chat_id} заблокировал бота.")
        return False
    except TelegramBadRequest as e:
        logger.error(f"TelegramBadRequest при отправке в {chat_id}: {e}")
        return False
    except Exception as e:
        logger.error(f"Ошибка отправки в {chat_id}: {e}")
        return False
