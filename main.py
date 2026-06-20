import asyncio
from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import bot, logger
from src.handlers import get_handlers_router
from src.scheduler import daily_subscription_checker

storage = MemoryStorage()
dp = Dispatcher(storage=storage)
dp.include_router(get_handlers_router())

scheduler = AsyncIOScheduler()

async def main():
    scheduler.add_job(daily_subscription_checker, CronTrigger(hour=0, minute=1))
    scheduler.start()
    logger.info("Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
