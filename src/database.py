"""
database.py — Абстракция файловой БД с атомарными операциями.

ОБНОВЛЕНО:
  - Убран импорт DB_FILE — теперь файлы БД передаются явно (многостранная архитектура).
  - Добавлены get_countries(), get_db_file(), get_available_countries(), get_user_slots().
  - Остальная логика (локи, атомарная запись, история) без изменений.
"""

import asyncio
import os
import json
import logging
import tempfile
from datetime import datetime
from typing import Any

import aiofiles

from src.config import HISTORY_FILE, SUPPORT_FILE, COUNTRIES_FILE, DATA_DIR

logger = logging.getLogger(__name__)

# ─── Per-file Lock-и ─────────────────────────────────────────────────────────
_locks: dict[str, asyncio.Lock] = {}

def _get_lock(filepath: str) -> asyncio.Lock:
    """Возвращает (или создаёт) Lock, привязанный к конкретному файлу."""
    if filepath not in _locks:
        _locks[filepath] = asyncio.Lock()
    return _locks[filepath]


# ─── Базовые операции ────────────────────────────────────────────────────────

async def load_json(filepath: str, default_factory=list) -> Any:
    """Читает JSON-файл с блокировкой. Если файла нет — инициализирует его."""
    lock = _get_lock(filepath)
    async with lock:
        if not os.path.exists(filepath):
            try:
                async with aiofiles.open(filepath, mode="w", encoding="utf-8") as f:
                    await f.write(json.dumps(default_factory(), ensure_ascii=False))
                logger.info(f"Инициализирован новый файл: {filepath}")
            except Exception as e:
                logger.error(f"Ошибка инициализации файла {filepath}: {e}")
            return default_factory()
        try:
            async with aiofiles.open(filepath, mode="r", encoding="utf-8") as f:
                content = await f.read()
            if not content.strip():
                logger.warning(f"Файл {filepath} пуст — возвращаю default.")
                return default_factory()
            return json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"Повреждённый JSON в {filepath}: {e}")
            return default_factory()
        except Exception as e:
            logger.error(f"Ошибка чтения файла {filepath}: {e}")
            return default_factory()


async def save_json(filepath: str, data: Any) -> None:
    """
    Атомарная запись JSON: пишем во временный файл рядом с целевым,
    затем os.replace() — атомарная операция на POSIX, и безопасная на Windows.
    """
    lock = _get_lock(filepath)
    async with lock:
        dir_name = os.path.dirname(filepath) or "."
        try:
            fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
            try:
                async with aiofiles.open(fd, mode="w", encoding="utf-8", closefd=True) as f:
                    await f.write(json.dumps(data, ensure_ascii=False, indent=4))
                os.replace(tmp_path, filepath)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            logger.error(f"Ошибка атомарной записи в {filepath}: {e}")


# ─── Работа со странами ──────────────────────────────────────────────────────

async def get_countries() -> list[dict]:
    """Возвращает список всех стран из countries.json."""
    return await load_json(COUNTRIES_FILE)


def get_db_file(country_code: str) -> str:
    """Возвращает путь к файлу БД для конкретной страны."""
    return os.path.join(DATA_DIR, f"clients_{country_code}.json")


async def get_available_countries() -> list[dict]:
    """
    Возвращает только страны где есть хотя бы один свободный слот (inactive).
    Используется для динамического построения меню выбора страны.
    """
    countries = await get_countries()
    available = []
    for country in countries:
        db_file = get_db_file(country["code"])
        db = await load_json(db_file)
        if any(s.get("status") == "inactive" for s in db):
            available.append(country)
    return available


async def get_user_slots(tg_id: int) -> list[dict]:
    """
    Возвращает все слоты пользователя по всем странам.
    Каждый слот дополняется полями country_code, country_flag, country_label, db_file.
    """
    countries = await get_countries()
    slots = []
    for country in countries:
        db_file = get_db_file(country["code"])
        db = await load_json(db_file)
        for slot in db:
            if slot.get("telegram_id") == tg_id:
                slot["country_code"]  = country["code"]
                slot["country_flag"]  = country["flag"]
                slot["country_label"] = country["label"]
                slot["db_file"]       = db_file
                slots.append(slot)
    return slots


# ─── История (атомарные read-modify-write) ───────────────────────────────────

async def has_used_trial_or_paid(tg_id: int) -> bool:
    """
    Проверяет наличие записи в history.json.
    """
    history = await load_json(HISTORY_FILE)
    return any(h.get("telegram_id") == tg_id for h in history)


async def add_to_history(tg_id: int, note: str = "") -> bool:
    """
    Атомарно добавляет запись в history.json.
    Возвращает True если запись добавлена, False если уже существовала.
    Вся операция (проверка + запись) под одним Lock — исключает
    двойную выдачу триала при параллельных запросах.
    """
    lock = _get_lock(HISTORY_FILE)
    async with lock:
        history = await _load_json_unlocked(HISTORY_FILE)
        if any(h.get("telegram_id") == tg_id for h in history):
            return False
        history.append({
            "telegram_id": tg_id,
            "activated_at": datetime.now().isoformat() + (f" [{note}]" if note else "")
        })
        await _save_json_unlocked(HISTORY_FILE, history)
        logger.info(f"Пользователь {tg_id} занесён в историю ({note or 'без пометки'}).")
        return True


# ─── Внутренние хелперы (без захвата lock — только для вызовов из-под lock) ─

async def _load_json_unlocked(filepath: str, default_factory=list) -> Any:
    try:
        async with aiofiles.open(filepath, mode="r", encoding="utf-8") as f:
            content = await f.read()
        if not content.strip():
            return default_factory()
        return json.loads(content)
    except (FileNotFoundError, json.JSONDecodeError):
        return default_factory()
    except Exception as e:
        logger.error(f"[unlocked] Ошибка чтения {filepath}: {e}")
        return default_factory()


async def _save_json_unlocked(filepath: str, data: Any) -> None:
    dir_name = os.path.dirname(filepath) or "."
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        async with aiofiles.open(fd, mode="w", encoding="utf-8", closefd=True) as f:
            await f.write(json.dumps(data, ensure_ascii=False, indent=4))
        os.replace(tmp_path, filepath)
    except Exception as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        logger.error(f"[unlocked] Ошибка записи {filepath}: {e}")
        raise
