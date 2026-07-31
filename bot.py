import asyncio, os, logging
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message

logging.basicConfig(level=logging.INFO)
dp = Dispatcher()

@dp.message(CommandStart())
async def start(m: Message):
    await m.answer("alive")

async def main():
    bot = Bot(os.environ["BOT_TOKEN"])
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

asyncio.run(main())