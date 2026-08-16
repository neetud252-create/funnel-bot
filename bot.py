import asyncio, os, logging, random, re
from decimal import Decimal
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (Message, CallbackQuery, InlineKeyboardMarkup,
                           InlineKeyboardButton, FSInputFile, InputMediaPhoto)
from aiogram.exceptions import TelegramBadRequest
import uvicorn
import db, config, panelbot
from server import app

logging.basicConfig(level=logging.INFO)
dp = Dispatcher()

class Reg(StatesGroup):
    waiting_uid = State()

UID_RE = re.compile(r"\d{5,15}")

OK_STATUS = ("creator", "administrator", "member")
_photo_cache = {}
_video_cache = {}
_nudge_tasks = {}
_signal_tasks = {}
_pair_choice = {}
_expiry_choice = {}

def photo_for(key):
    return _photo_cache.get(key) or FSInputFile("assets/" + key + ".jpg")

def video_for(key):
    return _video_cache.get(key) or FSInputFile("assets/" + key + ".mp4")

def media_missing(key, ext):
    # A cached file_id means Telegram already holds the media; otherwise we have
    # to upload the local file, and an asset that never got committed takes the
    # whole screen down (see assets/howto.jpg, assets/mode.jpg).
    cache = _video_cache if ext == "mp4" else _photo_cache
    return not cache.get(key) and not os.path.exists("assets/" + key + "." + ext)

def remember(key, msg):
    try:
        if msg and getattr(msg, "photo", None):
            _photo_cache[key] = msg.photo[-1].file_id
    except Exception:
        pass

def remember_video(key, msg):
    try:
        if msg and getattr(msg, "video", None):
            _video_cache[key] = msg.video.file_id
    except Exception:
        pass

# Telegram rejects the entire message if any inline button URL is malformed, so
# one unset link env var can blank out a whole screen. Require a scheme and a
# dotted host ("https://your-vip-link-here" fails this) and drop bad buttons
# instead of losing the screen.
_URL_OK = re.compile(r"^(?:https?://[^\s/?#]+\.[^\s/?#]+(?:[/?#]\S*)?|tg://\S+)$", re.I)

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
                url = action[4:]
                if not _URL_OK.match(url):
                    logging.warning("dropping button %r: invalid URL %r "
                                    "(set the matching link env var)", label, url)
                    continue
                kw["url"] = url
            else:
                kw["callback_data"] = action[3:]
            line.append(InlineKeyboardButton(**kw))
        if line:
            kb.append(line)
    return InlineKeyboardMarkup(inline_keyboard=kb)

async def is_subscribed(bot, tg_id):
    try:
        m = await bot.get_chat_member(config.CHANNEL_ID, tg_id)
        if m.status == "restricted":
            return bool(getattr(m, "is_member", False))
        return m.status in OK_STATUS
    except TelegramBadRequest as e:
        logging.warning("sub check failed: %s", e)
        return False

async def wipe(bot, tg_id):
    user = await db.get_user(tg_id)
    if not user:
        return
    if user["ui_msg_id"]:
        try:
            await bot.delete_message(chat_id=tg_id, message_id=user["ui_msg_id"])
        except Exception:
            pass
    ids = user["album_ids"]
    if ids:
        for mid in str(ids).split(","):
            try:
                await bot.delete_message(chat_id=tg_id, message_id=int(mid))
            except Exception:
                pass
        await db.set_album(tg_id, None)

async def render(bot, tg_id, media_key, text, kb_rows, is_video=False):
    kb = build_kb(kb_rows)
    user = await db.get_user(tg_id)
    msg_id = user["ui_msg_id"] if user else None
    if msg_id:
        try:
            await bot.delete_message(chat_id=tg_id, message_id=msg_id)
        except Exception as e:
            logging.warning("delete screen failed: %s", e)
    if media_missing(media_key, "mp4" if is_video else "jpg"):
        # Text-only fallback: the user still gets the screen and its buttons
        # instead of a tap that does nothing.
        logging.error("asset %r missing - sending %r as text only; commit the "
                      "file to assets/ to restore the image", media_key, media_key)
        m = await bot.send_message(tg_id, text, parse_mode="HTML", reply_markup=kb)
    elif is_video:
        m = await bot.send_video(tg_id, video_for(media_key), caption=text,
                                 parse_mode="HTML", reply_markup=kb)
        remember_video(media_key, m)
    else:
        m = await bot.send_photo(tg_id, photo_for(media_key), caption=text,
                                 parse_mode="HTML", reply_markup=kb)
        remember(media_key, m)
    await db.set_ui_msg(tg_id, m.message_id)

async def show(bot, tg_id, key):
    s = config.SCREENS[key]
    if key == "menu":
        # The menu caption is a template ({limit}/{used}/{left}); routing it
        # here means no caller can render it raw and leak the braces on screen.
        await _show_menu(bot, tg_id)
    elif "video" in s:
        await render(bot, tg_id, s["video"], s["text"], s["kb"], is_video=True)
    else:
        await render(bot, tg_id, s["photo"], s["text"], s["kb"])

@dp.message(CommandStart())
async def start(m: Message, bot: Bot, state: FSMContext):
    await state.clear()
    await db.touch_user(m.from_user.id, m.from_user.username)
    await show(bot, m.from_user.id, "gate")

@dp.callback_query(F.data == "check_sub")
async def check_sub(cb: CallbackQuery, bot: Bot):
    if await is_subscribed(bot, cb.from_user.id):
        await cb.answer("Verified")
        await show(bot, cb.from_user.id, "welcome")
    else:
        await cb.answer("You have not joined the channel yet. Subscribe first.", show_alert=True)

@dp.callback_query(F.data == "results")
async def results(cb: CallbackQuery, bot: Bot):
    await cb.answer()
    tg_id = cb.from_user.id
    await wipe(bot, tg_id)
    media = [InputMediaPhoto(media=photo_for(k)) for k in config.REVIEWS]
    msgs = await bot.send_media_group(tg_id, media)
    for k, msg in zip(config.REVIEWS, msgs):
        remember(k, msg)
    s = config.SCREENS["results"]
    m = await bot.send_message(tg_id, s["text"], parse_mode="HTML",
                               reply_markup=build_kb(s["kb"]))
    await db.set_ui_msg(tg_id, m.message_id)

async def _register_nudge(bot, tg_id, state):
    # Fire-and-forget follow-up ~4s after the register screen opens. Only nudges
    # if the user is still parked on it (skips if they sent an ID or navigated
    # away). Not recorded as ui_msg_id, so it doesn't interfere with wipe().
    try:
        await asyncio.sleep(4)
        if await state.get_state() != Reg.waiting_uid.state:
            return
        await bot.send_message(tg_id, config.REGISTER_NUDGE, parse_mode="HTML")
    except asyncio.CancelledError:
        pass
    except Exception:
        logging.warning("register nudge failed", exc_info=True)
    finally:
        if _nudge_tasks.get(tg_id) is asyncio.current_task():
            _nudge_tasks.pop(tg_id, None)

@dp.callback_query(F.data.startswith("go:"))
async def nav(cb: CallbackQuery, bot: Bot, state: FSMContext):
    await cb.answer()
    key = cb.data.split(":", 1)[1]
    await show(bot, cb.from_user.id, key)
    tg_id = cb.from_user.id
    # Arm UID capture only while the register screen is on-screen.
    if key == "register":
        await state.set_state(Reg.waiting_uid)
        # Cancel any pending nudge first so re-opening quickly doesn't stack them.
        old = _nudge_tasks.pop(tg_id, None)
        if old:
            old.cancel()
        _nudge_tasks[tg_id] = asyncio.create_task(_register_nudge(bot, tg_id, state))
    else:
        await state.clear()

# Must stay above menu_action: aiogram matches handlers in definition order and
# that one swallows every "menu:" callback.
@dp.callback_query(F.data == "menu:signal")
async def menu_signal(cb: CallbackQuery, bot: Bot):
    await cb.answer()
    await show(bot, cb.from_user.id, "mode")

# Must stay above mode_action, same definition-order reason as menu_signal.
@dp.callback_query(F.data == "mode:manual")
async def mode_manual(cb: CallbackQuery, bot: Bot):
    await cb.answer()
    await show(bot, cb.from_user.id, "type")

# Must stay above type_action, same definition-order reason as menu_signal.
@dp.callback_query(F.data == "type:otc")
async def type_otc(cb: CallbackQuery, bot: Bot):
    await cb.answer()
    await show(bot, cb.from_user.id, "asset")

async def show_pairs(bot, tg_id, page=0):
    # The pair picker is the one screen whose keyboard depends on state, so it
    # is built per page instead of taken straight off SCREENS. pairs_kb wraps
    # the page number, so a stale button can never render an empty grid.
    s = config.SCREENS["pairs"]
    await render(bot, tg_id, s["photo"], s["text"], config.pairs_kb(page))

# Must stay above asset_action, same definition-order reason as menu_signal.
@dp.callback_query(F.data == "asset:forex")
async def asset_forex(cb: CallbackQuery, bot: Bot):
    await cb.answer()
    await show_pairs(bot, cb.from_user.id, 0)

# Must stay above pair_action: "pairpage:3" does not match "pair:" (the 5th
# character is "p", not ":"), but keeping the specific handler first matches how
# every other pair of handlers in this file is ordered.
@dp.callback_query(F.data.startswith("pairpage:"))
async def pairs_page(cb: CallbackQuery, bot: Bot):
    # "›" on the pair picker. Re-renders the same screen one page along.
    await cb.answer()
    try:
        page = int(cb.data.split(":", 1)[1])
    except (IndexError, ValueError):
        page = 0
    await show_pairs(bot, cb.from_user.id, page)

@dp.callback_query(F.data.startswith("pair:"))
async def pair_action(cb: CallbackQuery, bot: Bot):
    # Any pair on any page opens the test menu. show() sends a new message (it
    # never edits), same as every other screen. The label comes from
    # config.PAIR_CODES, which covers the whole list rather than one page.
    await cb.answer()
    code = cb.data.split(":", 1)[1]
    _pair_choice[cb.from_user.id] = config.PAIR_CODES.get(code, config.DEFAULT_PAIR)
    await show(bot, cb.from_user.id, "test_menu")

@dp.callback_query(F.data.startswith("s:"))
async def s_action(cb: CallbackQuery):
    # Every S option is locked for now.
    await cb.answer("\U0001F512 Locked. Coming soon", show_alert=True)

async def _edit_signal(bot, tg_id, msg_id, text, is_caption):
    # The screen being counted down on is a photo when assets/test_menu.jpg (or
    # assets/buy.jpg) exists and plain text when render() fell back, so pick the
    # matching edit method. A failed edit only costs one tick - "message is not
    # modified", a flood wait or a screen the user already navigated away from
    # must never take the countdown down with it.
    try:
        if is_caption:
            await bot.edit_message_caption(chat_id=tg_id, message_id=msg_id,
                                           caption=text, parse_mode="HTML")
        else:
            await bot.edit_message_text(text=text, chat_id=tg_id, message_id=msg_id,
                                        parse_mode="HTML")
    except Exception as e:
        logging.warning("signal edit failed for tg_id=%s msg_id=%s: %s", tg_id, msg_id, e)

def _wait_label(seconds):
    # mm:ss clock label for the analyzing screen: 30 -> "00:30", 90 -> "01:30".
    # Derived from config.SIGNAL_COUNTDOWN rather than hardcoded into the copy,
    # so retuning the delay cannot leave the screen promising a stale number.
    minutes, secs = divmod(seconds, 60)
    return "%02d:%02d" % (minutes, secs)

async def _run_signal(bot, tg_id, msg_id, is_caption, expiry):
    # Edits the tapped screen once into the static "analyzing" notice (no new
    # message, and the edit drops the button grid so nothing is tappable while
    # we wait), waits out the fixed delay, then swaps it for the finished
    # signal. There is no live timer - the screen is written exactly once.
    # The wait is always config.SIGNAL_COUNTDOWN, whichever expiration was
    # tapped: the M button now only labels the trade on the result screen, it
    # does not set the delay. The deadline is taken before that edit, so its
    # round-trip does not push delivery late - every signal lands 30s after the
    # final tap, M1 and M10 alike.
    try:
        total = config.SIGNAL_COUNTDOWN
        loop = asyncio.get_running_loop()
        deadline = loop.time() + total
        await _edit_signal(bot, tg_id, msg_id,
                           config.SIGNAL_ANALYZING.format(wait=_wait_label(total)),
                           is_caption)
        await asyncio.sleep(max(0, deadline - loop.time()))
        user = await db.get_user(tg_id)
        if user and user["ui_msg_id"] != msg_id:
            # Another screen took over the chat while we counted down; it owns
            # ui_msg_id now, so dropping the signal beats clobbering it.
            return
        # The quota is spent here, at delivery, not at the tap: a countdown
        # that got cancelled or superseded never cost the user a signal, and
        # this single atomic UPDATE is what actually enforces the cap.
        ok, used, left = await db.consume_signal(tg_id, config.DAILY_SIGNAL_LIMIT)
        if not ok:
            # Raced past the cap while this one was counting down.
            await render(bot, tg_id, "buy", config.MSG_DAILY_LIMIT, config.LIMIT_KB)
            return
        pair = _pair_choice.get(tg_id, config.DEFAULT_PAIR)
        # render() deletes the analyzing message and falls back to text-only if
        # assets/buy.jpg is missing.
        # Fresh independent draw per signal - no alternating or cycling, so two
        # signals in a row can land on the same direction.
        direction = random.choice(config.SIGNAL_DIRECTIONS)
        await render(bot, tg_id, "buy",
                     config.SIGNAL_RESULT.format(pair=pair, expiry=expiry,
                                                 direction=direction),
                     config.SIGNAL_KB)
    except asyncio.CancelledError:
        pass
    except Exception:
        logging.exception("signal flow failed for tg_id=%s", tg_id)
    finally:
        if _signal_tasks.get(tg_id) is asyncio.current_task():
            _signal_tasks.pop(tg_id, None)

async def _start_signal(bot, cb, expiry):
    # Single entry point for both M taps and "New Signal", so the daily cap is
    # checked in exactly one place. This answers the callback itself (with the
    # limit alert or an empty ack) - callers must not answer first, or Telegram
    # discards the alert as a duplicate.
    tg_id = cb.from_user.id
    used, left = await db.signal_state(tg_id, config.DAILY_SIGNAL_LIMIT)
    if left <= 0:
        # Over the cap: no countdown is started and no message is touched, so
        # the user keeps whatever screen they were on.
        await cb.answer(config.MSG_DAILY_LIMIT, show_alert=True)
        return
    await cb.answer()
    # Cancel any countdown already running for this user first: two of them
    # would fight over the same message and both try to replace it at the end.
    _expiry_choice[tg_id] = expiry
    old = _signal_tasks.pop(tg_id, None)
    if old:
        old.cancel()
    _signal_tasks[tg_id] = asyncio.create_task(
        _run_signal(bot, tg_id, cb.message.message_id,
                    bool(getattr(cb.message, "photo", None)), expiry))

@dp.callback_query(F.data.startswith("m:"))
async def m_action(cb: CallbackQuery, bot: Bot):
    # The unlocked options: expiration comes straight from the button (m:5 -> M5).
    if not cb.message:
        await cb.answer()
        return
    await _start_signal(bot, cb, "M" + cb.data.split(":", 1)[1])

@dp.callback_query(F.data == "new_signal")
async def new_signal(cb: CallbackQuery, bot: Bot):
    # Re-runs the flow on the signal message itself, reusing the last expiration.
    if not cb.message:
        await cb.answer()
        return
    await _start_signal(bot, cb, _expiry_choice.get(cb.from_user.id, "M1"))

@dp.callback_query(F.data.startswith("asset:"))
async def asset_action(cb: CallbackQuery):
    # TODO: the four locked categories ("asset:forex" is handled above and opens
    # the currency-pair screen).
    await cb.answer("Coming soon \U0001F680", show_alert=True)

@dp.callback_query(F.data.startswith("type:"))
async def type_action(cb: CallbackQuery):
    # TODO: FIN market flow ("type:otc" is handled above and opens the
    # asset-category screen).
    await cb.answer("Coming soon \U0001F680", show_alert=True)

@dp.callback_query(F.data.startswith("mode:"))
async def mode_action(cb: CallbackQuery):
    # TODO: automatic-mode signal logic, pending the signal source decision
    # ("mode:manual" is handled above and opens the market-type screen).
    await cb.answer("Coming soon \U0001F680", show_alert=True)

@dp.callback_query(F.data.startswith("menu:"))
async def menu_action(cb: CallbackQuery):
    # TODO: real level logic; placeholder popup for now ("menu:signal" is handled
    # above and opens the mode screen).
    await cb.answer("Coming soon \U0001F680", show_alert=True)

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

def _register_btn():
    return [[("\U0001F511 Register & Get Access", "url:" + config.REF_LINK, "success",
              "5307843983102204243")]]

async def _replace(bot, tg_id, old_msg_id, text, kb_rows=None):
    # Swap the transient "checking" message for the verdict; track as ui_msg.
    try:
        await bot.delete_message(chat_id=tg_id, message_id=old_msg_id)
    except Exception:
        pass
    markup = build_kb(kb_rows) if kb_rows else None
    m = await bot.send_message(tg_id, text, parse_mode="HTML", reply_markup=markup)
    await db.set_ui_msg(tg_id, m.message_id)

async def _show_menu(bot, tg_id, test_mode=False):
    # The verified landing screen. In test mode the caption carries a banner so
    # the bypass is obvious on-screen. The signal counters are read fresh on
    # every render (signal_state also does the new-day rollover), so the numbers
    # are correct whenever the user lands here rather than only at login.
    s = config.SCREENS["menu"]
    used, left = await db.signal_state(tg_id, config.DAILY_SIGNAL_LIMIT)
    text = s["text"].format(limit=config.DAILY_SIGNAL_LIMIT, used=used, left=left)
    if test_mode:
        text = config.MSG_TEST_MODE + "\n\n" + text
    await render(bot, tg_id, s["photo"], text, s["kb"])

async def _run_verification(bot, tg_id, uid):
    # Immediate ack while we query the panel (can take up to ~20s), then verdict.
    ack = await bot.send_message(
        tg_id, "\U000023F3 <b>Checking account</b> <code>" + uid + "</code>\U00002026",
        parse_mode="HTML")
    try:
        info = await panelbot.lookup_trader(uid)
    except panelbot.PanelUnavailable:
        # Panel silent/disabled: defer to the retry worker, tell the user.
        await _replace(bot, tg_id, ack.message_id, config.MSG_DELAYED)
        return
    if info and str(info.get("campaign_id")) == str(config.CAMPAIGN_ID):
        dep = info.get("sum_deposits") or Decimal(0)
        if dep >= config.MIN_DEPOSIT:
            await db.set_verified(tg_id, dep)
            # Verified: drop the ack and hand the user the main menu.
            try:
                await bot.delete_message(chat_id=tg_id, message_id=ack.message_id)
            except Exception:
                pass
            await _show_menu(bot, tg_id)
        else:
            await _replace(bot, tg_id, ack.message_id, config.MSG_NEED_DEPOSIT, _register_btn())
    else:
        # Not found, or a different campaign.
        await _replace(bot, tg_id, ack.message_id, config.MSG_WRONG_LINK, _register_btn())

@dp.message(Reg.waiting_uid)
async def capture_uid(m: Message, bot: Bot, state: FSMContext):
    # The user's message is deleted the moment it arrives, so anything that
    # throws below would leave them staring at a vanished ID and no reply.
    # Every path out of here must put something back on screen.
    tg_id = m.from_user.id
    try:
        await _capture_uid(m, bot, state)
    except Exception:
        logging.exception("capture_uid failed for tg_id=%s", tg_id)
        try:
            # Re-arm capture so simply resending the ID is a working retry, and
            # send plain text with no keyboard - a bad button URL is exactly the
            # kind of failure that lands us here.
            await state.set_state(Reg.waiting_uid)
            await bot.send_message(tg_id, config.MSG_UID_ERROR, parse_mode="HTML")
        except Exception:
            logging.exception("capture_uid fallback reply failed for tg_id=%s", tg_id)

async def _capture_uid(m: Message, bot: Bot, state: FSMContext):
    tg_id = m.from_user.id
    uid = (m.text or "").strip()
    try:
        await bot.delete_message(chat_id=tg_id, message_id=m.message_id)
    except Exception:
        pass
    if not UID_RE.fullmatch(uid):
        await bot.send_message(tg_id, "\U00002757 Your account ID must be <b>numbers only</b> "
                               "(5\U0000201315 digits). Example: <b>123456789</b>", parse_mode="HTML")
        return
    owner = await db.uid_owner(uid)
    if owner is not None and owner != tg_id:
        await bot.send_message(tg_id, "\U000026A0\U0000FE0F That account ID is already registered to another "
                               "user. Please double-check and send your own ID.", parse_mode="HTML")
        return
    await db.save_uid_only(tg_id, uid)
    await state.clear()
    await wipe(bot, tg_id)
    if config.TEST_MODE:
        # TODO: testing bypass - no panel lookup, no campaign or deposit check.
        # Set VERIFY_MODE back to "live" before real users reach the bot.
        # Deposit is recorded as 0 because nothing was actually checked.
        logging.warning("VERIFY_MODE=test: bypassing verification for tg_id=%s uid=%s", tg_id, uid)
        await db.set_verified(tg_id, Decimal(0))
        await _show_menu(bot, tg_id, test_mode=True)
        return
    await _run_verification(bot, tg_id, uid)

async def retry_worker(bot):
    # Every 30 min, re-check users who have a uid but aren't verified yet.
    # On success, proactively send the confirmation. If the panel is down this
    # cycle, stop early and try again next cycle.
    while True:
        try:
            await asyncio.sleep(1800)
            rows = await db.unverified_with_uid()
            for r in rows:
                try:
                    info = await panelbot.lookup_trader(r["uid"])
                except panelbot.PanelUnavailable:
                    break
                if info and str(info.get("campaign_id")) == str(config.CAMPAIGN_ID):
                    dep = info.get("sum_deposits") or Decimal(0)
                    if dep >= config.MIN_DEPOSIT:
                        await db.set_verified(r["tg_id"], dep)
                        try:
                            await _show_menu(bot, r["tg_id"])
                        except Exception:
                            logging.exception("retry_worker: notify failed for %s", r["tg_id"])
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("retry_worker cycle failed")

async def main():
    await db.connect()
    bot = Bot(os.environ["BOT_TOKEN"])
    if config.TEST_MODE:
        logging.warning("VERIFY_MODE=test - panel verification is BYPASSED. "
                        "Set VERIFY_MODE=live before real users.")
    await bot.delete_webhook(drop_pending_updates=True)
    # Connect the panel-bot verification session (degrades gracefully if unset).
    await panelbot.start()
    # Railway routes the custom domain to $PORT (8080). Run the postback API
    # (uvicorn), long polling, and the retry worker side by side.
    port = int(os.environ.get("PORT", 8000))
    uv_config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(uv_config)
    await asyncio.gather(server.serve(), dp.start_polling(bot), retry_worker(bot))

asyncio.run(main())
