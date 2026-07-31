import asyncio, os, logging
import db
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message

logging.basicConfig(level=logging.INFO)
dp = Dispatcher()

@dp.message(CommandStart())
async def start(m: Message):
    await db.touch_user(m.from_user.id, m.from_user.username)
    await m.answer("alive")

async def main():
    await db.connect()
    bot = Bot(os.environ["BOT_TOKEN"])
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

asyncio.run(main())