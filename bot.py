import asyncio, os, logging
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (Message, CallbackQuery, InlineKeyboardMarkup,
                           InlineKeyboardButton, FSInputFile)
import db, config

logging.basicConfig(level=logging.INFO)
dp = Dispatcher()

_photo_cache = {}

def photo_for(key):
    return _photo_cache.get(key) or FSInputFile("assets/" + key + ".jpg")

def remember(key, msg):
    try:
        if msg and getattr(msg, "photo", None):
            _photo_cache[key] = msg.photo[-1].file_id
    except Exception:
        pass

def build_kb(rows):
    kb = []
    for row in rows:
        line = []
        for item in row:
            label = item[0]
            action = item[1]
            style = item[2] if len(item) > 2 else None
            icon = item[3] if len(item) > 3 else None
            kw = {"text": label}
            if style:
                kw["style"] = style
            if icon:
                kw["icon_custom_emoji_id"] = icon
            if action.startswith("url:"):
                kw["url"] = action[4:]
            else:
                kw["callback_data"] = action[3:]
            line.append(InlineKeyboardButton(**kw))
        kb.append(line)
    return InlineKeyboardMarkup(inline_keyboard=kb)

async def render(bot, tg_id, photo_key, text, kb_rows):
    kb = build_kb(kb_rows)
    user = await db.get_user(tg_id)
    msg_id = user["ui_msg_id"] if user else None
    if msg_id:
        try:
            await bot.delete_message(chat_id=tg_id, message_id=msg_id)
        except Exception as e:
            logging.warning("delete screen failed: %s", e)
    m = await bot.send_photo(tg_id, photo_for(photo_key), caption=text,
                             parse_mode="HTML", reply_markup=kb)
    remember(photo_key, m)
    await db.set_ui_msg(tg_id, m.message_id)

async def show(bot, tg_id, key):
    s = config.SCREENS[key]
    await render(bot, tg_id, s["photo"], s["text"], s["kb"])

@dp.message(CommandStart())
async def start(m: Message, bot: Bot):
    await db.touch_user(m.from_user.id, m.from_user.username)
    await show(bot, m.from_user.id, "welcome")

@dp.callback_query(F.data.startswith("go:"))
async def nav(cb: CallbackQuery, bot: Bot):
    await cb.answer()
    await show(bot, cb.from_user.id, cb.data.split(":", 1)[1])

@dp.callback_query(F.data.startswith("gallery:"))
async def gallery(cb: CallbackQuery, bot: Bot):
    await cb.answer()
    i = int(cb.data.split(":")[1]) % len(config.REVIEWS)
    kb = [[("\u25C0\uFE0F", "cb:gallery:" + str(i-1)),
           (str(i+1) + "/" + str(len(config.REVIEWS)), "cb:noop"),
           ("\u25B6\uFE0F", "cb:gallery:" + str(i+1))],
          [("Continue", "cb:go:final", "success")]]
    await render(bot, cb.from_user.id, config.REVIEWS[i], "<b>Real results</b>", kb)

@dp.callback_query(F.data == "noop")
async def noop(cb: CallbackQuery):
    await cb.answer()

async def main():
    await db.connect()
    bot = Bot(os.environ["BOT_TOKEN"])
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

asyncio.run(main())
