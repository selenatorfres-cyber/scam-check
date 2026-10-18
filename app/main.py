from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from app.bot.app import run_bot
from app.config import settings
from app.database.db import init_db, seed_admins
from app.monitor.client import Monitor, run_monitor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)


async def main():
    await init_db()
    await seed_admins()

    bot = Bot(settings.bot_token)
    monitor = Monitor(bot)

    try:
        await asyncio.gather(
            run_bot(bot, monitor),
            run_monitor(bot, monitor),
        )
    finally:
        await bot.session.close()
        if monitor.client.is_connected():
            await monitor.client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
