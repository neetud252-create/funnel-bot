import asyncio, hashlib, os, logging, random, re, time
from decimal import Decimal
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
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

# Deliberately a SEPARATE state from Reg.waiting_uid, not a flag on it. The
# funnel's capture path short-circuits on users.verified (it must not re-query
# the panel and risk a FloodWait); the Premium flow must do the opposite and
# re-check every single time. Two states keep those two rules from having to
# live in one handler, and leave the funnel's behaviour untouched.
class Premium(StatesGroup):
    waiting_uid = State()

UID_RE = re.compile(r"\d{5,15}")

OK_STATUS = ("creator", "administrator", "member")
_photo_cache = {}
_video_cache = {}
_nudge_tasks = {}
_signal_tasks = {}
_pair_choice = {}
_expiry_choice = {}
# tg_id -> time.monotonic() of that user's last panel lookup. In memory on
# purpose: it only has to survive between two taps, and a restart handing out
# one extra lookup is harmless.
_uid_lookup_at = {}

# Cache entries are (file_id, content_hash). The hash is of the file's BYTES at
# the moment Telegram issued that file_id, so replacing artwork on disk changes
# the hash, invalidates the entry and triggers exactly one re-upload - there is
# never a cache to clear by hand after swapping an image.
# _file_hashes memoises sha256 on (mtime_ns, size), so each file is read once
# per process and every later check is a bare os.stat.
_file_hashes = {}

def asset_path(key, ext):
    return "assets/" + key + "." + ext

def content_hash(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    sig = (st.st_mtime_ns, st.st_size)
    hit = _file_hashes.get(path)
    if hit and hit[0] == sig:
        return hit[1]
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    digest = h.hexdigest()
    _file_hashes[path] = (sig, digest)
    return digest

def cached_id(key, ext):
    """Stored file_id, but only while the file on disk still matches the hash it
    was cached under. A mismatch drops the entry so the caller re-uploads."""
    cache = _video_cache if ext == "mp4" else _photo_cache
    entry = cache.get(key)
    if not entry:
        return None
    file_id, cached_hash = entry
    path = asset_path(key, ext)
    if not os.path.exists(path):
        # Not shipped in this image - nothing to compare against, so trust it.
        return file_id
    if cached_hash and content_hash(path) != cached_hash:
        logging.info("asset %r changed on disk - re-uploading", key)
        cache.pop(key, None)
        return None
    return file_id

def photo_for(key):
    return cached_id(key, "jpg") or FSInputFile(asset_path(key, "jpg"))

def video_for(key):
    return cached_id(key, "mp4") or FSInputFile(asset_path(key, "mp4"))

def media_missing(key, ext):
    # A cached file_id means Telegram already holds the media; otherwise we have
    # to upload the local file, and an asset that never got committed takes the
    # whole screen down (see assets/howto.jpg, assets/mode.jpg).
    return not cached_id(key, ext) and not os.path.exists(asset_path(key, ext))

async def _store(key, ext, file_id):
    # Write through to the DB, but only when something actually changed - once
    # the cache is warm this is a dict comparison and no query at all.
    h = content_hash(asset_path(key, ext))
    cache = _video_cache if ext == "mp4" else _photo_cache
    if cache.get(key) == (file_id, h):
        return
    cache[key] = (file_id, h)
    try:
        await db.save_media_cache(key, file_id, h)
    except Exception:
        logging.exception("media_cache write failed for %r", key)

async def _forget(key, ext):
    (_video_cache if ext == "mp4" else _photo_cache).pop(key, None)
    try:
        await db.drop_media_cache(key)
    except Exception:
        logging.exception("media_cache delete failed for %r", key)

async def remember(key, msg):
    try:
        fid = msg.photo[-1].file_id if msg and getattr(msg, "photo", None) else None
    except Exception:
        return
    if fid:
        await _store(key, "jpg", fid)

async def remember_video(key, msg):
    try:
        fid = msg.video.file_id if msg and getattr(msg, "video", None) else None
    except Exception:
        return
    if fid:
        await _store(key, "mp4", fid)

async def send_media(bot, tg_id, key, is_video, text, kb):
    """Send a screen's media, preferring the cached file_id.

    Telegram can reject a stored file_id - they expire, and one issued to a
    different bot is never valid here. That must not surface as a broken screen:
    drop the row and pay for a single upload instead.
    """
    ext = "mp4" if is_video else "jpg"
    send = bot.send_video if is_video else bot.send_photo
    media = cached_id(key, ext) or FSInputFile(asset_path(key, ext))
    try:
        return await send(tg_id, media, caption=text, parse_mode="HTML",
                          reply_markup=kb)
    except TelegramBadRequest:
        if not isinstance(media, str):
            raise               # a fresh upload failed; nothing to retry with
        logging.warning("cached file_id rejected for %r - dropping it and "
                        "re-uploading", key)
        await _forget(key, ext)
        return await send(tg_id, FSInputFile(asset_path(key, ext)), caption=text,
                          parse_mode="HTML", reply_markup=kb)

async def load_media_cache():
    """One query at startup. Photo vs video is inferred from which file exists,
    which is why the table carries no kind column."""
    try:
        rows = await db.load_media_cache()
    except Exception:
        logging.exception("media_cache load failed - starting with an empty cache")
        return
    for r in rows:
        key = r["asset_key"]
        entry = (r["file_id"], r["content_hash"])
        if os.path.exists(asset_path(key, "mp4")):
            _video_cache[key] = entry
        else:
            _photo_cache[key] = entry
    logging.info("media_cache loaded: %d photo, %d video",
                 len(_photo_cache), len(_video_cache))

def referenced_assets():
    """Every (key, ext) a screen can actually send.

    Built from the same config the renderers read - SCREENS photo/video, the
    reviews album and the signal artwork - rather than from a directory
    listing. An asset sitting in assets/ that no screen references is never
    uploaded, and adding a screen needs no change here.
    """
    ref = set()
    for s in config.SCREENS.values():
        if s.get("video"):
            ref.add((s["video"], "mp4"))
        if s.get("photo"):
            ref.add((s["photo"], "jpg"))
    for key in config.REVIEWS:
        ref.add((key, "jpg"))
    for _label, key in config.SIGNAL_DIRECTIONS:
        ref.add((key, "jpg"))
    return ref

async def warm_media_cache(bot):
    """Upload every asset whose file_id is missing or stale, once, so no real
    user pays for an upload. Detached on purpose - polling is already serving
    while this works through the list."""
    chat = config.MEDIA_WARM_CHAT
    if not chat:
        logging.info("MEDIA_WARM_CHAT not set - skipping cache warm; the first "
                     "user on each screen pays for that screen's upload once")
        return
    warmed = missing = 0
    for key, ext in sorted(referenced_assets()):
        if cached_id(key, ext):
            continue
        if not os.path.exists(asset_path(key, ext)):
            # Referenced by a screen but not shipped in this image. render()
            # already degrades these to text-only, so there is nothing to
            # upload - but it is worth saying out loud once per boot.
            logging.warning("cache warm: %r is referenced by a screen but "
                            "missing from assets/", asset_path(key, ext))
            missing += 1
            continue
        try:
            sender = bot.send_video if ext == "mp4" else bot.send_photo
            m = await sender(chat, FSInputFile(asset_path(key, ext)))
            if ext == "mp4":
                await remember_video(key, m)
            else:
                await remember(key, m)
            warmed += 1
            try:
                await bot.delete_message(chat_id=chat, message_id=m.message_id)
            except Exception:
                pass
            # Spaced out so warming cannot trip Telegram's flood limits.
            await asyncio.sleep(1)
        except Exception:
            logging.exception("cache warm failed for %r", key)
    logging.info("media_cache warm complete: %d uploaded, %d referenced but "
                 "missing from assets/", warmed, missing)

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

def _row_field(row, name, default=None):
    # asyncpg Records and the plain dicts the test harness hands back both
    # index by column name, but only one of them tolerates a missing key.
    try:
        value = row[name]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value

async def _user_quota(tg_id):
    # (is_premium, that tier's daily limit). Resolved from the database on
    # every call rather than cached, so a /premium grant - or a restart that
    # picked up a new PREMIUM_DAILY_SIGNALS - applies to the very next tap.
    # This is the ONLY place a limit is chosen; nothing downstream names a
    # number of its own.
    user = await db.get_user(tg_id)
    premium = bool(_row_field(user, "is_premium", False)) if user else False
    return premium, config.daily_limit(premium)

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
    # Delete the current screen FIRST, then send its replacement. This is the
    # original transition and it is deliberate: Telegram plays its normal
    # message-removal animation on the old screen before the new one arrives,
    # so the two never overlap on screen.
    #
    # The trade-off is real and accepted: between the delete and the send the
    # chat is empty, so a send that fails leaves it that way. The try/except
    # below is what keeps that from being silent - it does not change the
    # ordering, it only makes a failed send say so instead of vanishing.
    if msg_id:
        try:
            await bot.delete_message(chat_id=tg_id, message_id=msg_id)
        except Exception as e:
            logging.warning("delete screen failed: %s", e)
    try:
        if media_key is None:
            # Deliberately text-only, not a missing asset - nothing to warn about.
            m = await bot.send_message(tg_id, text, parse_mode="HTML",
                                       reply_markup=kb)
        elif media_missing(media_key, "mp4" if is_video else "jpg"):
            # Text-only fallback: the user still gets the screen and its buttons
            # instead of a tap that does nothing.
            logging.error("asset %r missing - sending %r as text only; commit the "
                          "file to assets/ to restore the image", media_key, media_key)
            m = await bot.send_message(tg_id, text, parse_mode="HTML",
                                       reply_markup=kb)
        else:
            m = await send_media(bot, tg_id, media_key, is_video, text, kb)
            if is_video:
                await remember_video(media_key, m)
            else:
                await remember(media_key, m)
    except Exception:
        # The old screen is already gone by now, so there is nothing left on
        # screen to fall back to. Say something rather than leave the user with
        # an empty chat and a tap that looks dead.
        logging.exception("render failed for tg_id=%s screen=%r after the "
                          "previous screen was removed", tg_id, media_key)
        await _screen_error(bot, tg_id)
        return
    await db.set_ui_msg(tg_id, m.message_id)

async def _screen_error(bot, tg_id):
    # Plain text: no HTML, no entities, no keyboard. Whatever broke the screen
    # must not be able to break the message that reports it - that is exactly
    # how one bad entity turned into a chat with nothing in it. Sent only when
    # a render has already failed; it never appears on a successful screen.
    try:
        await bot.send_message(tg_id, config.MSG_SCREEN_ERROR)
    except Exception:
        logging.exception("could not even deliver the screen-error notice to %s",
                          tg_id)

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
    tg_id = m.from_user.id
    await db.touch_user(tg_id, m.from_user.username)
    user = await db.get_user(tg_id)
    if user and user["verified"]:
        # Verified users go straight to the menu and never re-enter the funnel
        # or the intro sequence. The stored flag is trusted deliberately - the
        # panel bot is NOT re-queried here. Panel lookups are rate limited and
        # serialised behind a lock, so one per /start would risk a FloodWait;
        # panelbot then raises PanelUnavailable("floodwait") and NO verification
        # succeeds for ANY user until it clears. A stale flag costs nothing; a
        # FloodWait breaks the funnel for everyone at once.
        await _show_menu(bot, tg_id)
        return
    await show(bot, tg_id, "gate")

@dp.message(Command("unverify"))
async def unverify_cmd(m: Message):
    # Admin-only testing helper: /unverify [tg_id], defaults to the caller.
    # Registered above the Reg.waiting_uid handler so it still works mid-funnel.
    if m.from_user.id not in config.ADMIN_IDS:
        return
    parts = (m.text or "").split()
    target = (int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit()
              else m.from_user.id)
    await db.unverify(target)
    await m.answer("Un-verified tg_id=%d. Send /start to re-run the flow." % target)

def _is_admin(tg_id):
    # ADMIN_IDS is empty unless it is set in Railway, so every admin command is
    # inert until it is - the safe default for an unconfigured service.
    return tg_id in config.ADMIN_IDS

# --- Admin: development reset -----------------------------------------------
# Walks the funnel from the top as if the sender had never used the bot, on the
# sender's own row and nothing else. Same registration band as /unverify above
# and the level commands below, for the same reason: above Reg.waiting_uid and
# above the bare-digits handler, or an admin parked mid-funnel would have this
# read as an account ID.
#
# Deliberately NOT a second /unverify. /unverify undoes the verification stamp
# and keeps the uid, which is what you want to re-test the verification step on
# its own. /devstart additionally clears the uid, the daily quota, the tier and
# every message-tracking id, for testing the funnel from its first screen. The
# split lives in db.py (unverify vs reset_user); neither command reimplements
# the other.

@dp.message(Command("devstart"))
async def cmd_devstart(m: Message, bot: Bot, state: FSMContext):
    # Silent for non-admins, and it returns before reading or writing anything
    # at all - a non-admin cannot use this to touch even their own row.
    tg_id = m.from_user.id
    if not _is_admin(tg_id):
        logging.warning("non-admin %s tried /devstart", tg_id)
        return
    # Order matters, twice over. wipe() reads ui_msg_id and album_ids and
    # _clear_nudge() reads nudge_msg_id; both have to run BEFORE the reset nulls
    # those columns, or the messages they point at are stranded in the chat with
    # nothing left to identify them by.
    await wipe(bot, tg_id)
    await _clear_nudge(bot, tg_id)
    if not await db.reset_user(tg_id):
        # No row yet - nothing to reset, and start() below creates one via
        # touch_user anyway, which is the state a reset would have produced.
        logging.info("/devstart: no existing row for %s, starting clean", tg_id)
    # In-memory session state for this user only. A countdown left running would
    # deliver a signal onto the fresh gate screen and spend a quota the reset
    # just cleared; a stale _uid_lookup_at would make the first account ID of
    # the new run bounce off the per-user panel cooldown.
    for tasks in (_signal_tasks, _nudge_tasks):
        task = tasks.pop(tg_id, None)
        if task:
            task.cancel()
    _pair_choice.pop(tg_id, None)
    _expiry_choice.pop(tg_id, None)
    _uid_lookup_at.pop(tg_id, None)
    logging.info("/devstart: reset tg_id=%s to a new-user state", tg_id)
    # Hand off to the real /start rather than repeating it. The row now reads
    # unverified, so start() takes its gate branch - and if the entry screen
    # ever changes again, /devstart follows it with no edit here.
    await start(m, bot, state)

# --- Admin level commands ---------------------------------------------------
# Registered above the Reg.waiting_uid message handler on purpose: aiogram
# dispatches in definition order, so an admin who happens to be parked on the
# register screen still gets the command instead of having it read as a UID.

async def _grant_level(m: Message, premium: bool):
    # Silent for non-admins: no reply at all, so an ordinary user cannot learn
    # the command exists, confirm who is Premium, or probe for valid tg_ids.
    # ADMIN_IDS is empty unless it is set in Railway, which means nobody can
    # change a tier until it is - the safe default.
    if not _is_admin(m.from_user.id):
        logging.warning("non-admin %s tried %r", m.from_user.id, (m.text or "")[:32])
        return
    parts = (m.text or "").split()
    cmd = parts[0] if parts else "/premium"
    if len(parts) != 2 or not parts[1].isdigit():
        await m.answer(config.MSG_ADMIN_USAGE.format(cmd=cmd))
        return
    target = int(parts[1])
    if not await db.set_premium(target, premium):
        await m.answer(config.MSG_ADMIN_NO_USER.format(tg_id=target))
        return
    # The new limit is reported from the same helper the enforcement uses, so
    # the confirmation cannot claim a number the user will not actually get.
    await m.answer(config.MSG_ADMIN_DONE.format(
        tg_id=target, level=config.level_label(premium),
        limit=config.daily_limit(premium)))

@dp.message(Command("premium"))
async def cmd_premium(m: Message):
    await _grant_level(m, True)

@dp.message(Command("startlevel"))
async def cmd_startlevel(m: Message):
    await _grant_level(m, False)

# --- Admin token commands ---------------------------------------------------
# Same rules as the level commands above: silent for non-admins, and registered
# here so an admin parked on the register screen is not read as sending a UID.
# These are the ONLY way a token balance is created - there is no earn path, no
# purchase and no link to a deposit.

async def _admin_tokens(m: Message, absolute: bool):
    if not _is_admin(m.from_user.id):
        logging.warning("non-admin %s tried %r", m.from_user.id, (m.text or "")[:32])
        return
    parts = (m.text or "").split()
    cmd = parts[0] if parts else "/tokens"
    # tg_id is unsigned; the amount may be negative for /tokens so an admin can
    # correct a mistake (db.add_tokens floors the balance at 0). lstrip("-") is
    # what lets isdigit() accept that sign.
    if (len(parts) != 3 or not parts[1].isdigit()
            or not parts[2].lstrip("-").isdigit()):
        await m.answer(config.MSG_TOKENS_USAGE.format(cmd=cmd))
        return
    target, amount = int(parts[1]), int(parts[2])
    balance = (await db.set_tokens(target, amount) if absolute
               else await db.add_tokens(target, amount))
    if balance is None:
        await m.answer(config.MSG_ADMIN_NO_USER.format(tg_id=target))
        return
    logging.info("admin %s set tg_id=%s tokens to %s (%s %s)",
                 m.from_user.id, target, balance,
                 "set" if absolute else "add", amount)
    await m.answer(config.MSG_TOKENS_DONE.format(tg_id=target, balance=balance))

@dp.message(Command("tokens"))
async def cmd_tokens(m: Message):
    await _admin_tokens(m, absolute=False)

@dp.message(Command("tokenset"))
async def cmd_tokenset(m: Message):
    await _admin_tokens(m, absolute=True)

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
        await remember(k, msg)
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
        nudge = await bot.send_message(tg_id, config.REGISTER_NUDGE, parse_mode="HTML")
        # Recorded so _clear_nudge can remove it once the user verifies. Still
        # not ui_msg_id - wipe()/render() must leave this message alone.
        await db.set_nudge_msg(tg_id, nudge.message_id)
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

async def _send_wait_screen(bot, tg_id, msg_id, total):
    # The waiting screen is two text messages and nothing else: the chart emoji
    # on its own, then the analysis text. This stage sends NO media - no photo,
    # no album, no video. The chart is a standalone message on purpose: as a
    # caption it would render as a small inline glyph on the same line as the
    # text instead of the full-size custom emoji the reference shows, and a
    # caption needs a photo to hang off, which is exactly what must not be here.
    # Returns (ui_id, extra_ids): ui_id is the message render() will clear when
    # the signal lands, extra_ids are the messages this has to delete itself,
    # since render() only ever clears ui_msg_id.
    # Clear the screen first, so the tapped button grid is gone while we wait.
    # ui_msg_id is the tapped screen, album_ids holds the waiting messages of a
    # run this tap just cancelled (they are parked there precisely so they
    # cannot be orphaned here), and msg_id is added only when it is neither -
    # a tap on a stale keyboard - so the common path issues no redundant delete.
    user = await db.get_user(tg_id)
    stale = []
    if user:
        if user["ui_msg_id"]:
            stale.append(int(user["ui_msg_id"]))
        if user["album_ids"]:
            stale.extend(int(m) for m in str(user["album_ids"]).split(","))
    if msg_id and msg_id not in stale:
        stale.append(msg_id)
    await _drop_msgs(bot, tg_id, stale)
    chart_msg = await bot.send_message(tg_id, config.SIGNAL_CHART, parse_mode="HTML")
    text_msg = await bot.send_message(
        tg_id, config.SIGNAL_ANALYZING.format(wait=_wait_label(total)),
        parse_mode="HTML")
    # The chart message is the one render() replaces with the finished signal,
    # so it goes in ui_msg_id and the analysis text is the lone extra.
    ui_id = chart_msg.message_id
    extra_ids = [text_msg.message_id]
    await db.set_ui_msg(tg_id, ui_id)
    # Park the extras in album_ids so that if the user walks off to another
    # screen mid-wait, wipe() takes them down with everything else.
    await db.set_album(tg_id, ",".join(str(i) for i in extra_ids))
    return ui_id, extra_ids

async def _drop_msgs(bot, tg_id, ids):
    # Best-effort delete. Deliberately touches no DB state: the cancellation
    # path below runs this detached, and a set_album(None) landing late would
    # wipe the bookkeeping of whichever run superseded it.
    for mid in ids:
        try:
            await bot.delete_message(chat_id=tg_id, message_id=mid)
        except Exception:
            pass

def _wait_label(seconds):
    # mm:ss clock label for the analyzing screen: 30 -> "00:30", 90 -> "01:30".
    # Derived from config.SIGNAL_COUNTDOWN rather than hardcoded into the copy,
    # so retuning the delay cannot leave the screen promising a stale number.
    minutes, secs = divmod(seconds, 60)
    return "%02d:%02d" % (minutes, secs)

async def _run_signal(bot, tg_id, msg_id, expiry):
    # Puts up the three-message waiting screen, waits out the fixed delay, then
    # clears the two text messages and replaces the image with the finished
    # signal. There is no live timer - the screen is written exactly once.
    # The wait is always config.SIGNAL_COUNTDOWN, whichever expiration was
    # tapped: the M button now only labels the trade on the result screen, it
    # does not set the delay. The deadline is taken before the three sends, so
    # their round-trips do not push delivery late - every signal lands 30s after
    # the final tap, M1 and M10 alike.
    extra_ids = []
    try:
        total = config.SIGNAL_COUNTDOWN
        loop = asyncio.get_running_loop()
        deadline = loop.time() + total
        ui_id, extra_ids = await _send_wait_screen(bot, tg_id, msg_id, total)
        await asyncio.sleep(max(0, deadline - loop.time()))
        # Unconditional, and ahead of the ownership check below: these two are
        # ours either way, and a screen that took over mid-wait replaced only
        # ui_msg_id, so nothing else is going to clean them up.
        await _drop_msgs(bot, tg_id, extra_ids)
        await db.set_album(tg_id, None)
        user = await db.get_user(tg_id)
        if user and user["ui_msg_id"] != ui_id:
            # Another screen took over the chat while we counted down; it owns
            # ui_msg_id now, so dropping the signal beats clobbering it.
            return
        # The quota is spent here, at delivery, not at the tap: a countdown
        # that got cancelled or superseded never cost the user a signal, and
        # this single atomic UPDATE is what actually enforces the cap.
        # The tier is re-read now rather than reused from the tap, so a grant
        # that landed during the countdown is already in force.
        _, limit = await _user_quota(tg_id)
        ok, used, left = await db.consume_signal(tg_id, limit)
        if not ok:
            # Raced past the cap while this one was counting down. Text-only by
            # design: this screen used to pass "buy" as its media key and render
            # as text purely because assets/buy.jpg did not exist. That asset is
            # now the green BUY board, and putting it above "Daily limit
            # reached" would read as a signal to trade.
            await render(bot, tg_id, None, config.MSG_DAILY_LIMIT, config.LIMIT_KB)
            return
        pair = _pair_choice.get(tg_id, config.DEFAULT_PAIR)
        # Fresh independent draw per signal - no alternating or cycling, so two
        # signals in a row can land on the same direction. The artwork travels
        # with the direction (see config.SIGNAL_DIRECTIONS), so BUY can only ever
        # render the green board and SELL only the red one.
        direction, photo = random.choice(config.SIGNAL_DIRECTIONS)
        # render() clears the analysis messages and puts the image up with the
        # result as its caption, keeping the New Signal button on that same
        # message. It falls back to text-only if the asset is missing.
        await render(bot, tg_id, photo,
                     config.SIGNAL_RESULT.format(pair=pair, expiry=expiry,
                                                 direction=direction),
                     config.SIGNAL_KB)
    except asyncio.CancelledError:
        # Superseded by a newer tap. Detached, because every await in here is
        # about to be cancelled too; the replacement run's wipe() is the backstop
        # if this loses the race.
        if extra_ids:
            asyncio.create_task(_drop_msgs(bot, tg_id, list(extra_ids)))
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
    _, limit = await _user_quota(tg_id)
    used, left = await db.signal_state(tg_id, limit)
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
        _run_signal(bot, tg_id, cb.message.message_id, expiry))

@dp.callback_query(F.data.startswith("m:"))
async def m_action(cb: CallbackQuery, bot: Bot):
    # The unlocked options: expiration comes straight from the button (m:5 -> M5).
    if not cb.message:
        await cb.answer()
        return
    await _start_signal(bot, cb, "M" + cb.data.split(":", 1)[1])

@dp.callback_query(F.data == "new_signal")
async def new_signal(cb: CallbackQuery, bot: Bot):
    # Opens the currency-pair picker at page 1 instead of immediately repeating
    # the previous signal. The daily cap is re-checked here because this path no
    # longer runs through _start_signal, which is where it used to be gated -
    # without this, New Signal would be a way around the cap.
    if not cb.message:
        await cb.answer()
        return
    tg_id = cb.from_user.id
    # Per-user, like the other two cap checks: reading the global Start limit
    # here would hold a Premium user to 30 on this path alone.
    _, limit = await _user_quota(tg_id)
    _, left = await db.signal_state(tg_id, limit)
    if left <= 0:
        # Same contract as _start_signal: answer with the alert and touch no
        # message, so the user keeps the result screen they are on.
        await cb.answer(config.MSG_DAILY_LIMIT, show_alert=True)
        return
    await cb.answer()
    # A countdown still running for this user would render its result over the
    # pair picker when it lands. _start_signal cancels for the same reason.
    old = _signal_tasks.pop(tg_id, None)
    if old:
        old.cancel()
    await show_pairs(bot, tg_id, 0)

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

# Must stay above menu_action, same definition-order reason as menu_signal.
@dp.callback_query(F.data == "menu:level")
async def menu_level(cb: CallbackQuery, bot: Bot):
    # Text-only: there is no artwork for this screen, and render() treats a
    # None media key as deliberate rather than as a missing asset.
    await cb.answer()
    tg_id = cb.from_user.id
    # One lookup feeds both the tier shown and the limit shown, and it is the
    # same helper _start_signal and _run_signal enforce with - the screen cannot
    # promise an allowance the server would refuse.
    premium, limit = await _user_quota(tg_id)
    used, left = await db.signal_state(tg_id, limit)
    tokens = await db.game_tokens(tg_id)
    await render(bot, tg_id, None,
                 config.MSG_LEVEL.format(icon=config.level_icon_tg(premium),
                                         name=config.level_name(premium),
                                         limit=limit, used=used, left=left,
                                         tokens=tokens),
                 config.LEVEL_KB)

# Must stay above menu_action, same definition-order reason as menu_signal.
@dp.callback_query(F.data == "menu:premium")
async def menu_premium(cb: CallbackQuery, bot: Bot, state: FSMContext):
    # Opens the Premium unlock flow. The button itself is untouched - text,
    # style, callback and custom emoji are all as they were; only what happens
    # after the tap changed.
    #
    # This screen never unlocks anything. It states the balance and the
    # shortfall and arms Premium.waiting_uid, because the unlock is gated on a
    # game UID check that has to happen on a message, not on this tap.
    await cb.answer()
    tg_id = cb.from_user.id
    premium = await db.is_premium(tg_id)
    balance = await db.game_tokens(tg_id)

    if premium:
        # Nothing to unlock, so no UID is asked for and no state is armed.
        await state.clear()
        text = config.MSG_PREMIUM_ALREADY.format(
            limit=config.daily_limit(True), balance=balance)
    else:
        needed = config.tokens_needed(balance)
        template = (config.MSG_PREMIUM_READY if needed == 0
                    else config.MSG_PREMIUM_SHORT)
        text = template.format(balance=balance, needed=needed)
        await state.set_state(Premium.waiting_uid)
    await render(bot, tg_id, None, text, config.UNLOCK_KB)

@dp.callback_query(F.data.startswith("menu:"))
async def menu_action(cb: CallbackQuery):
    # "menu:signal" and "menu:level" are both handled above; this stays as the
    # catch-all for any future menu callback that has no screen yet.
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
    # The level and the limit come from the same lookup, so the caption can
    # never show "Premium" next to the Start allowance.
    s = config.SCREENS["menu"]
    premium, limit = await _user_quota(tg_id)
    used, left = await db.signal_state(tg_id, limit)
    # _tg variant: this caption is sent with parse_mode="HTML" by render(), so
    # the tier's custom-emoji entity resolves. The plain level_label() is for
    # the admin confirmation, which has no parse mode.
    text = s["text"].format(limit=limit, used=used, left=left,
                            level=config.level_label_tg(premium))
    if test_mode:
        text = config.MSG_TEST_MODE + "\n\n" + text
    await render(bot, tg_id, s["photo"], text, s["kb"])

async def _clear_nudge(bot, tg_id):
    """Delete the activation nudge once the user is verified.

    Best effort throughout: a bot may only delete its own messages, and only
    within 48 hours, so a nudge from an earlier session will fail. Logged at
    debug and swallowed - a failed delete must never break verification.
    """
    try:
        user = await db.get_user(tg_id)
        mid = user["nudge_msg_id"] if user else None
        if not mid:
            return
        try:
            await bot.delete_message(chat_id=tg_id, message_id=mid)
        except Exception as e:
            logging.debug("nudge delete failed tg_id=%s msg_id=%s: %s", tg_id, mid, e)
        # Cleared even when the delete failed, so a doomed message_id is not
        # retried on every later verification.
        await db.set_nudge_msg(tg_id, None)
    except Exception:
        logging.debug("nudge cleanup failed tg_id=%s", tg_id, exc_info=True)

async def _run_verification(bot, tg_id, uid):
    """Run the panel check and show the verdict.

    Returns True only when access was granted. Every other outcome tells the
    user to send their account ID again, so the caller must re-arm UID capture -
    with ENABLE_AUTO_RETRY off, that re-send is the only route to verification.
    """
    # Immediate ack while we query the panel (can take up to ~20s), then verdict.
    ack = await bot.send_message(
        tg_id, "\U000023F3 <b>Checking account</b> <code>" + uid + "</code>\U00002026",
        parse_mode="HTML")
    try:
        info = await panelbot.lookup_trader(uid)
    except panelbot.PanelUnavailable as e:
        # Panel silent/disabled. The reason string ("disabled"/"timeout"/
        # "floodwait"/"session"/"warmup"/"error") is what separates a broken
        # session from a slow panel. MSG_DELAYED asks the user to resend, and
        # the caller re-arms capture so that actually works.
        logging.warning("VERIFY uid=%s tg_id=%s -> PanelUnavailable(%s) -> MSG_DELAYED",
                        uid, tg_id, e)
        await _replace(bot, tg_id, ack.message_id, config.MSG_DELAYED)
        return False
    if info and str(info.get("campaign_id")) == str(config.CAMPAIGN_ID):
        dep = info.get("sum_deposits") or Decimal(0)
        logging.info("VERIFY uid=%s tg_id=%s campaign MATCH dep=%s min=%s -> %s",
                     uid, tg_id, dep, config.MIN_DEPOSIT,
                     "ACCESS" if dep >= config.MIN_DEPOSIT else "NEED_DEPOSIT")
        if dep >= config.MIN_DEPOSIT:
            await db.set_verified(tg_id, dep)
            await _clear_nudge(bot, tg_id)
            # Verified: drop the ack and hand the user the main menu.
            try:
                await bot.delete_message(chat_id=tg_id, message_id=ack.message_id)
            except Exception:
                pass
            await _show_menu(bot, tg_id)
            return True
        else:
            await _replace(bot, tg_id, ack.message_id, config.MSG_NEED_DEPOSIT, _register_btn())
            return False
    else:
        # Not found, or a different campaign. record_found=False with a healthy
        # panel usually means the reply format changed - see PANEL PARSE above.
        logging.info("VERIFY uid=%s tg_id=%s -> WRONG_LINK (record_found=%s "
                     "campaign_id=%s expected=%s)", uid, tg_id, info is not None,
                     info.get("campaign_id") if info else None, config.CAMPAIGN_ID)
        await _replace(bot, tg_id, ack.message_id, config.MSG_WRONG_LINK, _register_btn())
    return False

async def _verify_game_uid(tg_id, uid):
    """Check one game UID. Returns True when it is valid.

    This is the Premium flow's OWN check and it is intentionally self-contained:
    it never calls panelbot, never reads users.verified, users.uid or
    users.deposit, and has nothing to do with the trading-account verification
    that gates the funnel. A game UID is a game UID.

    It also holds no state of its own - no cache, no memo of the last uid, no
    "already checked" flag. Every call does the full check from scratch, which
    is what makes a repeated UID verify again instead of being waved through.
    """
    uid = (uid or "").strip()
    ok = bool(UID_RE.fullmatch(uid))
    logging.info("GAME UID CHECK tg_id=%s uid=%r -> %s", tg_id, uid[:16],
                 "VALID" if ok else "INVALID")
    return ok

# MUST stay above the Reg.waiting_uid handler and uid_anytime below: aiogram
# dispatches in definition order, and uid_anytime matches any bare number in
# any state. Registered here, Premium.waiting_uid wins while it is armed, so a
# UID sent during the unlock flow never reaches the funnel's capture path.
@dp.message(Premium.waiting_uid)
async def premium_uid(m: Message, bot: Bot, state: FSMContext):
    tg_id = m.from_user.id
    uid = (m.text or "").strip()
    try:
        await bot.delete_message(chat_id=tg_id, message_id=m.message_id)
    except Exception:
        pass

    # Fresh verification on EVERY message, unconditionally. There is no check
    # of users.verified, no comparison against the previously sent uid and no
    # one-time flag anywhere on this path: the third send of the same UID runs
    # exactly the same check as the first.
    if not await _verify_game_uid(tg_id, uid):
        # Re-armed, so resending is a working retry with no attempt limit.
        await state.set_state(Premium.waiting_uid)
        await render(bot, tg_id, None, config.MSG_GAME_UID_INVALID,
                     config.UNLOCK_KB)
        return

    # UID accepted. The spend is still the single gated UPDATE - the check
    # above decides whether we ATTEMPT it, never whether it succeeds, so the
    # atomicity of the deduction is exactly what it was.
    cost = config.PREMIUM_UNLOCK_COST
    unlocked, balance, premium = await db.unlock_premium(tg_id, cost)

    if unlocked:
        logging.info("premium unlocked by tg_id=%s for %s tokens, %s left",
                     tg_id, cost, balance)
        await state.clear()
        text = config.MSG_PREMIUM_UNLOCKED.format(limit=config.daily_limit(True))
    elif premium:
        # Already Premium - the statement matched no row, so nothing was spent.
        await state.clear()
        text = config.MSG_PREMIUM_ALREADY.format(
            limit=config.daily_limit(True), balance=balance)
    else:
        # Valid UID, not enough tokens. Stay armed so the user can send the UID
        # again once they have earned the rest, and check it again when they do.
        await state.set_state(Premium.waiting_uid)
        text = config.MSG_PREMIUM_STILL_SHORT.format(
            balance=balance, needed=config.tokens_needed(balance))
    await render(bot, tg_id, None, text, config.UNLOCK_KB)

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

@dp.message(F.text.regexp(r"^\s*\d+\s*$"))
async def uid_anytime(m: Message, bot: Bot, state: FSMContext):
    # A bare number is always a UID attempt, whatever the FSM state says. The
    # state is cleared on /start (:160), on every non-register navigation
    # (:245) and before verification runs (:657), so relying on Reg.waiting_uid
    # alone left users unhandled after their first attempt. Registered below
    # the Reg.waiting_uid handler, which still wins when that state is set -
    # both funnel into the same code path, so they cannot drift.
    await capture_uid(m, bot, state)

async def _capture_uid(m: Message, bot: Bot, state: FSMContext):
    tg_id = m.from_user.id
    uid = (m.text or "").strip()
    try:
        await bot.delete_message(chat_id=tg_id, message_id=m.message_id)
    except Exception:
        pass
    user = await db.get_user(tg_id)
    if user and user["verified"]:
        # Already verified: never re-run the panel, just put them back on the
        # menu. Same reasoning as /start - a stale flag costs nothing, an
        # avoidable lookup risks a FloodWait that hits everyone.
        await state.clear()
        await _show_menu(bot, tg_id)
        return
    if not UID_RE.fullmatch(uid):
        # Numeric but the wrong length. Answered from the format rule alone -
        # the panel is never queried for something that cannot be an account id.
        await bot.send_message(tg_id, "\U00002757 Your account ID must be <b>numbers only</b> "
                               "(5\U0000201315 digits). Example: <b>123456789</b>", parse_mode="HTML")
        await state.set_state(Reg.waiting_uid)
        return
    wait = config.UID_LOOKUP_COOLDOWN - (time.monotonic() - _uid_lookup_at.get(tg_id, 0.0))
    if wait > 0:
        # Throttled before the shared panel queue is ever touched.
        logging.info("UID COOLDOWN tg_id=%s uid=%s %.0fs remaining", tg_id, uid, wait)
        await bot.send_message(tg_id, config.MSG_UID_COOLDOWN.format(seconds=int(wait) + 1),
                               parse_mode="HTML")
        await state.set_state(Reg.waiting_uid)
        return
    # No uniqueness gate: a uid already held by another telegram account still
    # goes through the full panel lookup, and access is granted on the campaign
    # + deposit check alone. Sharing is allowed by design.
    await db.save_uid_only(tg_id, uid)
    # ...but it is worth seeing. Never let this reporting break the funnel.
    try:
        holders = await db.uid_owners(uid)
        if len(holders) > 1:
            logging.warning("UID SHARED uid=%s claimed by %d telegram ids: %s",
                            uid, len(holders), ",".join(str(h) for h in holders))
    except Exception:
        logging.exception("uid_owners lookup failed for uid=%s", uid)
    await state.clear()
    await wipe(bot, tg_id)
    if config.TEST_MODE:
        # TODO: testing bypass - no panel lookup, no campaign or deposit check.
        # Set VERIFY_MODE back to "live" before real users reach the bot.
        # Deposit is recorded as 0 because nothing was actually checked.
        logging.warning("VERIFY_MODE=test: bypassing verification for tg_id=%s uid=%s", tg_id, uid)
        await db.set_verified(tg_id, Decimal(0))
        await _clear_nudge(bot, tg_id)
        await _show_menu(bot, tg_id, test_mode=True)
        return
    # Stamped only where a panel lookup actually happens - the TEST_MODE
    # bypass above queries nothing, so it must not start a cooldown.
    _uid_lookup_at[tg_id] = time.monotonic()
    granted = await _run_verification(bot, tg_id, uid)
    if not granted:
        # Every non-granted verdict asks the user to send their ID again
        # (deposit then resend / register then resend / retry shortly). The
        # state was cleared above and there is no catch-all message handler, so
        # without re-arming here their next message matches nothing and is
        # silently dropped. With ENABLE_AUTO_RETRY off this is the only way in.
        await state.set_state(Reg.waiting_uid)

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
                        await _clear_nudge(bot, r["tg_id"])
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
    # file_id cache: one query to load it, then warm the gaps in the background
    # so startup is not delayed and no user ever pays for an upload.
    await load_media_cache()
    asyncio.create_task(warm_media_cache(bot))
    # Railway routes the custom domain to $PORT (8080). Run the postback API
    # (uvicorn) and long polling side by side, plus the retry worker if enabled.
    port = int(os.environ.get("PORT", 8000))
    uv_config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(uv_config)
    tasks = [server.serve(), dp.start_polling(bot)]
    if config.ENABLE_AUTO_RETRY:
        logging.info("ENABLE_AUTO_RETRY on - background re-check every 30 min")
        tasks.append(retry_worker(bot))
    else:
        logging.info("ENABLE_AUTO_RETRY off - no background re-check; users "
                     "re-send their account ID after depositing to verify")
    await asyncio.gather(*tasks)

asyncio.run(main())
