import asyncio, hashlib, os, logging, random, re, time
import weakref
from decimal import Decimal
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (Message, CallbackQuery, InlineKeyboardMarkup,
                           InlineKeyboardButton, FSInputFile, BufferedInputFile, InputMediaPhoto, InputMediaVideo, ReplyParameters)
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
import uvicorn
import db, config, panelbot
import broadcast
import verification_reminders
import elena
import activation
import activity_stats
import analytics
import analytics_reports
import home_menu
import start_here
import journey
import localization
import bot_profile
import chart_signals
import market_signals
import market_data
from server import app

logging.basicConfig(level=logging.INFO)
dp = Dispatcher()


async def _broadcast_open(cb, bot, state):
    user = await db.get_user(cb.from_user.id)
    if user and user['verified']:
        await state.clear()
        await _show_menu(bot, cb.from_user.id)
    else:
        await state.clear()
        await _resume_setup(bot, cb.from_user.id)


# Register before UID handlers; admin drafting never changes the funnel FSM.
activity_stats.install(dp)
analytics_reports.install(dp)
broadcast.install(dp, lambda uid: uid in config.ADMIN_IDS, _broadcast_open)
verification_reminders.install(dp, lambda uid: uid in config.ADMIN_IDS, _broadcast_open)
elena.install(dp, lambda tg_id, uid: _panel_verdict(tg_id, uid),
              lambda tg_id: tg_id in _verify_inflight or tg_id in _premium_checks)
activation.configure(lambda bot, tg_id: _show_activation_gate(bot, tg_id),
                     lambda bot, tg_id: _open_from_elena(bot, tg_id))

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

# Telegram deep-link payload: t.me/<bot>?start=<code>. Telegram itself only
# ever delivers 1-64 characters of A-Za-z0-9_- here, so anything else did not
# come from a real deep link and is dropped rather than stored. Filtering at
# the edge is also what keeps arbitrary user text out of the log line in
# _capture_ref() - the payload is echoed there, and only a matched string is.
#
# Shared with server.py's /click endpoint, which validates the landing page's
# cid against the same pattern: a cid that one half accepts and the other
# rejects is a click that can never be joined to a user.
REF_RE = config.REF_CODE_RE

OK_STATUS = ("creator", "administrator", "member")
_photo_cache = {}
_video_cache = {}
_signal_tasks = {}
_pair_choice = {}
_expiry_choice = {}
# tg_id -> time.monotonic() of that user's last panel lookup. In memory on
# purpose: it only has to survive between two taps, and a restart handing out
# one extra lookup is harmless.
_uid_lookup_at = {}
# Single-flight verification: tg_id -> {"uid": str, "task": asyncio.Task}.
#
# One entry means one real panel lookup is in progress for that user. A second
# message arriving while it is there never starts another - it awaits the same
# task and reuses its result. This is what stops a user's own duplicate sends
# from queueing extra lookups behind panelbot's lock, and it is why they no
# longer have to resend an ID they already sent.
#
# Keyed by tg_id, not by (tg_id, uid): one user has at most one check running,
# so a different uid arriving mid-check is answered rather than run in
# parallel. Entries are removed by _clear_inflight from the task's done
# callback, so completion, failure, timeout and cancellation all clean up.
_verify_inflight = {}
_premium_checks = set()
_welcome_locks = weakref.WeakValueDictionary()
_welcome_sync_locks = weakref.WeakValueDictionary()

# Cache entries are (file_id, content_hash). The hash is of the file's BYTES at
# the moment Telegram issued that file_id, so replacing artwork on disk changes
# the hash, invalidates the entry and triggers exactly one re-upload - there is
# never a cache to clear by hand after swapping an image.
# _file_hashes memoises sha256 on (mtime_ns, size), so each file is read once
# per process and every later check is a bare os.stat.
_file_hashes = {}

def asset_path(key, ext):
    if key == home_menu.VIDEO and ext == 'mp4':
        key = home_menu.ANIMATION
    # The photo cache uses the logical "jpg" type; retain the owner's PNG
    # unchanged on disk and upload it with its actual filename.
    if key == activation.PHOTO and ext == 'jpg':
        ext = 'png'
    elif key == home_menu.PHOTO and ext == 'jpg':
        ext = home_menu.PHOTO_EXT
    elif key == config.UID_GUIDE_PHOTO and ext == 'jpg':
        ext = 'png'
    elif key in (journey.REGISTER_PHOTO, journey.RECOVERY_PHOTO) and ext == 'jpg':
        ext = 'png'
    elif ext == 'jpg' and key in (*market_signals.DIRECTION_BANNERS.values(),
                                  'trading_type', 'asset_category', 'currency_pair', 'expiration_time', 'menu'):
        ext = 'png'
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
        kind = 'animation' if key == home_menu.ANIMATION else 'video'
        media = getattr(msg, kind, None) if msg else None
        fid = media.file_id if media else None
    except Exception:
        return
    if fid:
        await _store(key, "mp4", fid)

def media_sender(bot, key, ext):
    # Telegram animations use silent MP4s but return animation.file_id, not
    # video.file_id. A distinct asset key keeps both cached types separate.
    if ext == 'mp4':
        return bot.send_animation if key == home_menu.ANIMATION else bot.send_video
    return bot.send_photo

def media_send_options(key, ext):
    # Supply the source dimensions on the first upload, including cache warming.
    # Telegram retains the uploaded video's metadata when its file_id is reused.
    if ext == "mp4":
        if key in (home_menu.ANIMATION, home_menu.VIDEO):
            options = {"width": 1280, "height": 720, "duration": 6}
            if key == home_menu.VIDEO:
                options["supports_streaming"] = True
            return options
        for screen in config.SCREENS.values():
            if screen.get("video") == key:
                options = dict(screen.get("video_options", {}))
                if cover := screen.get("video_cover"):
                    # The square cover sizes the chat preview; the video keeps
                    # its original dimensions in the full-screen player. Send
                    # the cover even when reusing the video's cached file_id.
                    options["cover"] = FSInputFile(asset_path(cover, "jpg"))
                return options
    return {}

async def send_media(bot, tg_id, key, is_video, text, kb):
    """Send a screen's media, preferring the cached file_id.

    Telegram can reject a stored file_id - they expire, and one issued to a
    different bot is never valid here. That must not surface as a broken screen:
    drop the row and pay for a single upload instead.
    """
    ext = "mp4" if is_video else "jpg"
    send = media_sender(bot, key, ext)
    options = media_send_options(key, ext)
    media = cached_id(key, ext) or FSInputFile(asset_path(key, ext))
    try:
        return await send(tg_id, media, caption=text, parse_mode="HTML",
                          reply_markup=kb, **options)
    except TelegramBadRequest:
        if not isinstance(media, str):
            raise               # a fresh upload failed; nothing to retry with
        logging.warning("cached file_id rejected for %r - dropping it and "
                        "re-uploading", key)
        await _forget(key, ext)
        return await send(tg_id, FSInputFile(asset_path(key, ext)), caption=text,
                          parse_mode="HTML", reply_markup=kb, **options)

async def send_review_album(bot, tg_id):
    """send_media()'s contract, for the one screen that cannot use it.

    The reviews go out as an album, and send_media_group is a single call: one
    stale file_id fails the whole group ("Wrong file identifier/HTTP URL
    specified" against message #1) rather than one photo. A file_id issued to a
    different bot is never valid here, so drop every cached review id and retry
    once from the assets already in the repo.
    """
    media = [InputMediaPhoto(media=photo_for(k)) for k in config.REVIEWS]
    try:
        return await bot.send_media_group(tg_id, media)
    except TelegramBadRequest:
        cached = [k for k, item in zip(config.REVIEWS, media)
                  if isinstance(item.media, str)]
        if not cached:
            raise               # a fresh upload failed; nothing to retry with
        logging.warning("cached album file_id rejected for %s - dropping and "
                        "re-uploading", ", ".join(repr(k) for k in cached))
        for key in cached:
            await _forget(key, "jpg")
        # Every item from disk: Telegram only names the first bad one, so a
        # partial retry could trip straight over the next stale id.
        return await bot.send_media_group(
            tg_id, [InputMediaPhoto(media=FSInputFile(asset_path(k, "jpg")))
                    for k in config.REVIEWS])

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
    ref = {(activation.PHOTO, 'jpg'), (home_menu.PHOTO, 'jpg'), (home_menu.VIDEO, 'mp4'),
           (config.UID_GUIDE_PHOTO, 'jpg'), (journey.REGISTER_PHOTO, 'jpg'),
           (journey.RECOVERY_PHOTO, 'jpg')}
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
            sender = media_sender(bot, key, ext)
            m = await sender(chat, FSInputFile(asset_path(key, ext)),
                             **media_send_options(key, ext))
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

async def _sub_id(tg_id):
    """The value every outbound affiliate link carries for this user.

    ref_code when the landing page supplied one, the tg_id otherwise, so the
    link is NEVER sent without a sub-ID - an untracked click is worse than one
    tracked by a fallback. The two are told apart on the way back in: a tg_id
    is all digits and matches no ref_code, which is what server.py's probe
    reports separately.
    """
    try:
        user = await db.get_user(tg_id)
    except Exception:
        # Tracking must not cost anyone the register screen.
        logging.exception("sub-id lookup failed for tg_id=%s, using the tg_id", tg_id)
        user = None
    return _row_field(user, "ref_code") or str(tg_id)


async def _ref_url(tg_id):
    return config.ref_url(await _sub_id(tg_id))


async def _user_quota(tg_id):
    # (is_premium, that tier's daily limit). Resolved from the database on
    # every call rather than cached, so a /premium grant - or a restart that
    # picked up a new PREMIUM_DAILY_SIGNALS - applies to the very next tap.
    # Chart analysis uses the same config.daily_limit and persisted counter.
    user = await db.get_user(tg_id)
    premium = bool(_row_field(user, "is_premium", False)) if user else False
    return premium, config.daily_limit(premium)

async def wipe(bot, tg_id):
    user = await db.get_user(tg_id)
    if not user:
        return
    if user["ui_msg_id"] and user["ui_msg_id"] != user.get('welcome_message_id'):
        try:
            await bot.delete_message(chat_id=tg_id, message_id=user["ui_msg_id"])
        except Exception:
            pass
    ids = user["album_ids"]
    if ids:
        for mid in str(ids).split(","):
            if int(mid) == user.get('welcome_message_id'):
                continue
            try:
                await bot.delete_message(chat_id=tg_id, message_id=int(mid))
            except Exception:
                pass
        await db.set_album(tg_id, None)

async def render(bot, tg_id, media_key, text, kb_rows, is_video=False, *,
                 reply_to_message_id=None, raise_on_error=False, screen_key=None, companion_key=None):
    token = analytics._screen.set(screen_key or analytics.screen_name(text))
    try:
        return await _render(bot, tg_id, media_key, text, kb_rows, is_video,
                             reply_to_message_id=reply_to_message_id, raise_on_error=raise_on_error,
                             companion_key=companion_key)
    finally:
        analytics._screen.reset(token)


async def _render(bot, tg_id, media_key, text, kb_rows, is_video=False, *,
                 reply_to_message_id=None, raise_on_error=False, companion_key=None):
    is_home_media = media_key in (home_menu.ANIMATION, home_menu.VIDEO)
    if is_home_media and media_missing(media_key, 'mp4'):
        media_key, is_video = home_menu.PHOTO, False
    kb = build_kb(kb_rows)
    user = await db.get_user(tg_id)
    if user and user.get('welcome_message_id'):
        await _sync_welcome(bot, tg_id)
    msg_id = user["ui_msg_id"] if user else None
    # Keep the pinned welcome; delete the current disposable screen FIRST,
    # then send its replacement. This is the
    # original transition and it is deliberate: Telegram plays its normal
    # message-removal animation on the old screen before the new one arrives,
    # so the two never overlap on screen.
    #
    # Between the delete and the send there may be no active menu besides the
    # pinned welcome, so a send that fails needs an error notice. The try/except
    # below is what keeps that from being silent - it does not change the
    # ordering, it only makes a failed send say so instead of vanishing.
    if msg_id and msg_id != user.get('welcome_message_id'):
        try:
            await bot.delete_message(chat_id=tg_id, message_id=msg_id)
        except Exception as e:
            logging.warning("delete screen failed: %s", e)
    # Clean up companion messages, including GIFs from the old split home layout.
    if user and user['album_ids']:
        await _drop_msgs(bot, tg_id, [int(mid) for mid in str(user['album_ids']).split(',')])
        await db.set_album(tg_id, None)
    try:
        if isinstance(media_key, BufferedInputFile):
            # Generated OHLC charts are per-request bytes, never shared file IDs.
            m = await bot.send_photo(tg_id, media_key, caption=text,
                                     parse_mode='HTML', reply_markup=kb)
        elif media_key is None:
            # Deliberately text-only, not a missing asset - nothing to warn about.
            reply = {'reply_parameters': ReplyParameters(message_id=reply_to_message_id,
                     allow_sending_without_reply=True)} if reply_to_message_id else {}
            m = await bot.send_message(tg_id, text, parse_mode="HTML",
                                       reply_markup=kb, **reply)
        elif media_missing(media_key, "mp4" if is_video else "jpg"):
            # Text-only fallback: the user still gets the screen and its buttons
            # instead of a tap that does nothing.
            logging.error("asset %r missing - sending %r as text only; commit the "
                          "file to assets/ to restore the image", media_key, media_key)
            m = await bot.send_message(tg_id, text, parse_mode="HTML",
                                       reply_markup=kb)
        else:
            try:
                m = await send_media(bot, tg_id, media_key, is_video, text, kb)
            except Exception:
                if not is_home_media:
                    raise
                logging.exception('Welcome media failed; using the still banner or text')
                failed_key = media_key
                media_key, is_video = home_menu.PHOTO, False
                m = None
                if failed_key != media_key and not media_missing(media_key, 'jpg'):
                    try:
                        m = await send_media(bot, tg_id, media_key, False, text, kb)
                    except Exception:
                        logging.exception('Welcome still banner failed; using text')
                if m is None:
                    m = await bot.send_message(tg_id, text, parse_mode='HTML', reply_markup=kb)
            if is_video:
                await remember_video(media_key, m)
            else:
                await remember(media_key, m)
    except Exception:
        if raise_on_error:
            raise
        # The old screen is already gone by now, so there is nothing left on
        # screen to fall back to. Say something rather than leave the user with
        # an empty chat and a tap that looks dead.
        logging.exception("render failed for tg_id=%s screen=%r after the "
                          "previous screen was removed", tg_id, media_key)
        await _screen_error(bot, tg_id)
        return
    await db.set_ui_msg(tg_id, m.message_id)
    if companion_key:
        await _send_result_banner(bot, tg_id, m.message_id, companion_key, kb)
    return m


async def _send_result_banner(bot, tg_id, chart_id, key, kb):
    """Keep an already-delivered result usable if its decorative banner fails."""
    banner = None
    try:
        user = await db.get_user(tg_id)
        if not user or user.get('ui_msg_id') != chart_id or media_missing(key, 'jpg'):
            return
        banner = await send_media(bot, tg_id, key, False, '', kb)
        await remember(key, banner)
        if not await db.set_album_for_ui(tg_id, chart_id, str(banner.message_id)):
            await _drop_msgs(bot, tg_id, [banner.message_id])
            return
    except asyncio.CancelledError:
        if banner:
            await _drop_msgs(bot, tg_id, [banner.message_id])
        raise
    except Exception as exc:
        if banner:
            await _drop_msgs(bot, tg_id, [banner.message_id])
        logging.warning('Signal banner unavailable: %s', type(exc).__name__)
        return
    # Only move controls after both messages are delivered and tracked. The
    # chart keeps its buttons if sending the banner failed; neither costs again.
    try:
        await bot.edit_message_reply_markup(chat_id=tg_id, message_id=chart_id, reply_markup=None)
    except Exception as exc:
        logging.warning('Signal chart controls retained: %s', type(exc).__name__)

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
    # Previously sent buttons remain usable, but old promotional cards no longer
    # bypass requirements or display unsupported profit/accuracy claims.
    legacy = {'welcome': 'how', 'how': 'how', 'tech': 'how', 'ai': 'explain',
              'results': 'reviews', 'howto': 'account_help'}
    if key in legacy:
        await _journey(bot, tg_id, journey.screen(legacy[key], await db.get_user(tg_id)))
        return
    if key in ('access', 'final'):
        await _resume_setup(bot, tg_id)
        return
    s = config.SCREENS[key]
    if key == "menu":
        # The menu caption is a template ({limit}/{used}/{left}); routing it
        # here means no caller can render it raw and leak the braces on screen.
        await _show_menu(bot, tg_id)
    elif key == "register":
        # Same reason, one step further: this caption is a template ({ref}) AND
        # its Register button is per-user, so no caller may render it raw.
        await _show_register(bot, tg_id)
    elif "video" in s:
        await render(bot, tg_id, s["video"], s["text"], s["kb"], is_video=True)
    else:
        await render(bot, tg_id, s["photo"], s["text"], s["kb"])

async def _capture_ref(tg_id, command):
    # First-touch attribution: db.save_ref_code only fills a NULL, so a user
    # who later arrives on a different landing link keeps the code that
    # actually brought them in. The return value says which of the two
    # happened, and the log line reports it either way - that is the line to
    # grep to confirm a payload arrived.
    raw = (command.args or "").strip() if command else ""
    if not raw:
        return
    if not REF_RE.match(raw):
        # Length only, never the value: an unmatched payload is unvalidated
        # user input and does not belong in the log verbatim.
        logging.warning("/start: tg_id=%s sent a %d-char payload that is not a "
                        "valid deep-link tracking id - ignored", tg_id, len(raw))
        return
    try:
        stored = await db.save_ref_code(tg_id, raw)
    except Exception:
        # Tracking is not worth losing a /start over: the funnel continues and
        # the failure is on record. That is the only reason this is wrapped.
        logging.exception("/start: could not store ref_code=%s for tg_id=%s",
                          raw, tg_id)
        return
    logging.info("/start deep-link ref_code=%s tg_id=%s (%s)", raw, tg_id,
                 "stored" if stored else "already attributed, kept first touch")

@dp.message(CommandStart())
async def start(m: Message, bot: Bot, state: FSMContext,
                command: CommandObject | None = None):
    await state.clear()
    tg_id = m.from_user.id
    await db.touch_user(tg_id, m.from_user.username)
    # Elena links resume an existing feature; they are not advertising attribution.
    resume = getattr(command, 'args', None) if command else None
    if resume not in ('elena_home', 'elena_setup', 'elena_uid', 'elena_signals', 'elena_guide'):
        await _capture_ref(tg_id, command)
    await _clear_nudge(bot, tg_id)
    if await activation.required(tg_id):
        await _show_activation_gate(bot, tg_id)
        return
    if activation.enabled():
        # A manual return takes over any pending automatic menu delivery.
        await elena.store.activation_opened(tg_id)
    if resume in ('elena_home', 'elena_setup', 'elena_uid', 'elena_signals', 'elena_guide'):
        if resume == 'elena_uid':
            user = await db.get_user(tg_id)
            if user and user.get('verified'):
                await _show_menu(bot, tg_id)
            else:
                await _open_uid_entry(bot, tg_id, state)
        elif resume == 'elena_setup':
            await _resume_setup(bot, tg_id, state)
        elif resume == 'elena_guide':
            await show(bot, tg_id, 'guide')
        else:
            user = await db.get_user(tg_id)
            await (_show_menu(bot, tg_id) if user and user.get('verified') else _show_home(bot, tg_id))
        return
    # Serialize starts and read access under the lock. Only accounts that have
    # completed verification go directly to the existing main menu.
    lock = _welcome_locks.setdefault(tg_id, asyncio.Lock())
    async with lock:
        user = await db.get_user(tg_id)
        if user and user.get('verified'):
            await _show_menu(bot, tg_id)
            return
        message = await _show_home(bot, tg_id)
        if getattr(getattr(m, 'chat', None), 'type', None) == 'private':
            await _pin_welcome(bot, tg_id, message)


async def _show_activation_gate(bot, tg_id):
    lock = _welcome_locks.setdefault(tg_id, asyncio.Lock())
    async with lock:
        if not await activation.required(tg_id):
            # The DM may arrive while this /start or stale button is waiting.
            message = await _show_current_interface(bot, tg_id)
            if message:
                await elena.store.activation_opened(tg_id)
            return message
        try:
            return await render(bot, tg_id, activation.PHOTO, elena.copy.ACTIVATION,
                                activation.keyboard(), raise_on_error=True, screen_key='elena_activation')
        except Exception:
            # The single activation action stays usable even if artwork fails.
            return await render(bot, tg_id, None, elena.copy.ACTIVATION,
                                activation.keyboard(), screen_key='elena_activation')


async def _show_current_interface(bot, tg_id):
    user = await db.get_user(tg_id)
    if user and user.get('verified'):
        return await _show_menu(bot, tg_id)
    message = await _show_home(bot, tg_id)
    await _pin_welcome(bot, tg_id, message)
    return message


async def _open_from_elena(bot, tg_id):
    lock = _welcome_locks.setdefault(tg_id, asyncio.Lock())
    async with lock:
        session = await elena.store.session(tg_id)
        if not activation.current(session):
            return False
        if session.get('activation_opened_at'):
            return True
        await dp.fsm.get_context(bot=bot, chat_id=tg_id, user_id=tg_id).clear()
        return bool(await _show_current_interface(bot, tg_id))


async def _show_home(bot, tg_id):
    user = await db.get_user(tg_id)
    return await render(bot, tg_id, home_menu.VIDEO, home_menu.TEXT, home_menu.keyboard(user), is_video=True)


async def _pin_welcome(bot, tg_id, message):
    """Pin once, persist the anchor, and never let pin failures block the menu."""
    if message is None:
        return
    try:
        saved = await db.claim_welcome_message(tg_id, message.message_id)
        if saved:
            await _sync_welcome(bot, tg_id)
        if not saved or saved['welcome_pinned_at']:
            # Respect a user's later unpin/hide; don't force the pin back.
            return
        message_id = saved['welcome_message_id']
        try:
            await bot.pin_chat_message(chat_id=tg_id, message_id=message_id,
                                       disable_notification=True)
        except TelegramBadRequest as error:
            if 'message to pin not found' in str(error).lower() or 'message_id_invalid' in str(error).lower():
                # The pending welcome was deleted. Use this start's fresh
                # welcome; never discard a reference to a successful pin.
                await db.clear_missing_welcome(tg_id, message_id)
                if message_id != message.message_id:
                    return await _pin_welcome(bot, tg_id, message)
            raise
        await db.mark_welcome_pinned(tg_id, message_id)
        logging.info('Welcome message pinned for tg_id=%s', tg_id)
    except Exception:
        logging.warning('Welcome pin unavailable for tg_id=%s; menu remains usable',
                        tg_id, exc_info=True)


async def _upgrade_welcome_media(bot, tg_id, message_id, keyboard):
    """Replace the old GIF in place, reusing only a video-type Telegram file ID."""
    key = home_menu.VIDEO
    file_id = cached_id(key, 'mp4')
    for attempt in range(2):
        media = InputMediaVideo(
            media=file_id or FSInputFile(asset_path(key, 'mp4')),
            caption=start_here.TEXT, parse_mode='HTML',
            **media_send_options(key, 'mp4'))
        try:
            message = await bot.edit_message_media(
                chat_id=tg_id, message_id=message_id, media=media, reply_markup=keyboard)
        except TelegramBadRequest as error:
            description = str(error).lower()
            if 'message is not modified' in description:
                return
            if not file_id or attempt or 'message to edit not found' in description:
                raise
            await _forget(key, 'mp4')
            file_id = None
        else:
            await remember_video(key, message)
            return


async def _sync_welcome(bot, tg_id, *, raise_retry_after=False):
    """Edit the saved card in place; never re-pin a user's dismissed message."""
    lock = _welcome_sync_locks.setdefault(tg_id, asyncio.Lock())
    try:
        async with lock, asyncio.timeout(10):
            # Re-read under the lock so a verification during migration wins.
            user = await db.get_user(tg_id)
            message_id = user.get('welcome_message_id') if user else None
            if not message_id:
                return 'unchanged'
            signature = start_here.signature(user)
            if user.get('welcome_content_hash') == signature:
                return 'unchanged'
            kwargs = dict(chat_id=tg_id, message_id=message_id, parse_mode='HTML',
                          reply_markup=build_kb(start_here.keyboard(user)))
            media_ready = (user.get('welcome_content_hash') or '').startswith(start_here.MEDIA_VERSION)
            if not media_ready and not media_missing(home_menu.VIDEO, 'mp4'):
                await _upgrade_welcome_media(bot, tg_id, message_id, kwargs['reply_markup'])
                await db.mark_welcome_content(tg_id, message_id, signature)
                return 'updated'
            try:
                try:
                    # Once migrated, ordinary copy/keyboard updates only edit the caption.
                    await bot.edit_message_caption(caption=start_here.TEXT, **kwargs)
                except TelegramBadRequest as error:
                    description = str(error).lower()
                    if ('no caption' not in description and
                            'not a media message' not in description):
                        raise
                    # A welcome sent during a media outage may be text-only.
                    await bot.edit_message_text(text=start_here.TEXT, **kwargs)
            except TelegramBadRequest as error:
                if 'message is not modified' not in str(error).lower():
                    raise
            # Persist only after Telegram accepts the edit, including a retry
            # whose previous edit succeeded but whose database write failed.
            if not media_ready:
                # A missing local video must not permanently mark a fallback as migrated.
                return 'unavailable'
            await db.mark_welcome_content(tg_id, message_id, signature)
            return 'updated'
    except TelegramRetryAfter:
        if raise_retry_after:
            raise
        logging.warning('Start Here update rate limited for tg_id=%s; will retry', tg_id)
    except Exception:
        logging.warning('Start Here update unavailable for tg_id=%s; menu remains usable',
                        tg_id, exc_info=True)
    return 'unavailable'


async def _refresh_saved_welcomes(bot):
    """Upgrade existing cards at startup without sending or pinning new messages."""
    after = 0
    counts = dict(updated=0, unchanged=0, unavailable=0)
    try:
        while rows := await db.welcome_users_after(after):
            for row in rows:
                tg_id = row['tg_id']
                for attempt in range(3):
                    try:
                        result = await _sync_welcome(bot, tg_id, raise_retry_after=True)
                        break
                    except TelegramRetryAfter as error:
                        await asyncio.sleep(error.retry_after + 1)
                else:
                    result = 'unavailable'
                counts[result] += 1
                after = tg_id
                # Leave headroom for normal bot replies and other background jobs.
                if result != 'unchanged':
                    await asyncio.sleep(1)
        logging.info('Start Here refresh complete: updated=%d unchanged=%d unavailable=%d',
                     counts['updated'], counts['unchanged'], counts['unavailable'])
    except Exception:
        logging.exception('Start Here refresh interrupted; remaining cards retry on interaction or restart')


async def _journey(bot, tg_id, content, media=None, key=None):
    text, rows = content
    await render(bot, tg_id, media, text, rows, screen_key=key)


async def _open_uid_entry(bot, tg_id, state=None):
    await db.set_setup_stage(tg_id, 'id')
    if state is not None:
        await state.set_state(Reg.waiting_uid.state)
    await _journey(bot, tg_id, journey.uid(await _ref_url(tg_id)), key='account_id_prompt')


async def _show_existing_registration(bot, tg_id, user, state=None):
    if state is not None:
        await state.set_state(Reg.waiting_uid.state)
    ref = await _ref_url(tg_id)
    if ref:
        await activity_stats.record(tg_id, 'registration_opened')
    await _journey(bot, tg_id, journey.existing(ref),
                   media=journey.REGISTER_PHOTO, key='existing_account_registration')


async def _show_account_recovery(bot, tg_id, state=None):
    if state is not None:
        await state.set_state(Reg.waiting_uid.state)
    await _journey(bot, tg_id, journey.unlinked(ref=await _ref_url(tg_id)),
                   media=journey.RECOVERY_PHOTO, key='account_unlinked')


async def _resume_setup(bot, tg_id, state=None):
    user = await db.get_user(tg_id) or {}
    stage = user.get('setup_stage')
    if user.get('verified'):
        await _show_menu(bot, tg_id)
    elif stage == 'account_help':
        await _journey(bot, tg_id, journey.account_help(), key='account_question_help')
    elif stage == 'requirements' and user.get('account_kind') == 'new' and not user.get('uid'):
        # Visitors saved on the former introduction now resume registration.
        await _show_register(bot, tg_id, state)
    elif stage in ('existing_account', 'existing_register'):
        await _show_existing_registration(bot, tg_id, user, state)
    elif stage == 'referral_help':
        await _journey(bot, tg_id, journey.referral_help(), key='referral_help')
    elif stage == 'id':
        # Explicit ID entry takes priority over an earlier failed check.
        await _open_uid_entry(bot, tg_id, state)
    elif user.get('access_check_status') == config.VERIFY_NEED_DEPOSIT and user.get('uid'):
        await _journey(bot, tg_id, journey.deposit(user['access_check_deposit']), key='deposit_requirements')
    elif user.get('access_check_status') == config.VERIFY_WRONG_LINK:
        await _show_account_recovery(bot, tg_id, state)
    elif user.get('access_check_status') == config.VERIFY_TEMPORARY and user.get('uid'):
        await _journey(bot, tg_id, journey.pending(), key='verification_pending')
    elif user.get('account_kind') == 'existing' or user.get('uid'):
        await _show_existing_registration(bot, tg_id, user, state)
    else:
        await _show_register(bot, tg_id, state)


@dp.callback_query(F.data.startswith('flow:'))
async def flow_action(cb: CallbackQuery, bot: Bot, state: FSMContext):
    if not cb.message or cb.message.chat.type != 'private':
        await cb.answer('Open Pocket IQ in a private chat.', show_alert=True)
        return
    await cb.answer()
    tg_id = cb.from_user.id
    action = cb.data.split(':', 1)[1]
    await state.clear()
    user = await db.get_user(tg_id) or {}
    if action == 'home':
        await _show_home(bot, tg_id)
    elif action == 'dashboard':
        await _show_menu(bot, tg_id)
    elif action == 'setup':
        await _resume_setup(bot, tg_id, state)
    elif action == 'not_sure':
        if user.get('verified'):
            await _show_menu(bot, tg_id)
            return
        await db.set_setup_stage(tg_id, 'account_help')
        await _journey(bot, tg_id, journey.account_help(), key='account_question_help')
    elif action in ('new', 'existing'):
        if user.get('verified'):
            await _show_menu(bot, tg_id)
            return
        # An already-linked account continues from its saved deposit result.
        if user.get('uid') and user.get('access_check_status') == config.VERIFY_NEED_DEPOSIT:
            await db.set_setup_stage(tg_id, 'deposit', account_kind='existing')
            await _resume_setup(bot, tg_id, state)
        elif action == 'existing' or user.get('uid'):
            await db.set_setup_stage(tg_id, 'existing_account', account_kind='existing')
            await _show_existing_registration(bot, tg_id, user, state)
        else:
            await db.set_setup_stage(tg_id, 'register', account_kind='new')
            await _show_register(bot, tg_id, state)
    elif action == 'register_again':
        if user.get('verified'):
            await _show_menu(bot, tg_id)
            return
        if user.get('access_check_status') == config.VERIFY_NEED_DEPOSIT:
            await db.set_setup_stage(tg_id, 'deposit')
            await _resume_setup(bot, tg_id, state)
            return
        # Reopen registration without erasing the submitted ID or its result.
        await db.set_setup_stage(tg_id, 'existing_register')
        await _show_existing_registration(bot, tg_id, user, state)
    elif action == 'register_existing':
        if user.get('verified'):
            await _show_menu(bot, tg_id)
            return
        if user.get('uid') and user.get('access_check_status') == config.VERIFY_NEED_DEPOSIT:
            await db.set_setup_stage(tg_id, 'deposit')
        else:
            await db.set_setup_stage(tg_id, 'existing_register', account_kind='existing')
        await _resume_setup(bot, tg_id, state)
    elif action in ('register', 'eligibility', 'eligible'):
        # Old Continue/confirmation buttons also resume the simplified setup.
        # Do not record an age declaration that this flow no longer asks for.
        if user.get('verified'):
            await _show_menu(bot, tg_id)
            return
        if (user.get('account_kind') == 'new' and not user.get('uid')
                and user.get('setup_stage') != 'id'):
            await db.set_setup_stage(tg_id, 'register')
        await _resume_setup(bot, tg_id, state)
    elif action == 'requirements':
        await _journey(bot, tg_id, journey.requirements(), key='access_requirements_details')
    elif action == 'referral_help':
        if user.get('verified'):
            await _show_menu(bot, tg_id)
            return
        await db.set_setup_stage(tg_id, 'referral_help', account_kind='existing')
        await _journey(bot, tg_id, journey.referral_help(), key='referral_help')
    elif action == 'ineligible':
        await db.clear_eligibility(tg_id)
        await db.set_setup_stage(tg_id, 'help')
        await _journey(bot, tg_id, journey.screen('ineligible', user), key='eligibility_declined')
    elif action == 'enter_id':
        if user.get('verified'):
            await _show_menu(bot, tg_id)
            return
        if not user.get('account_kind'):
            await db.set_setup_stage(tg_id, 'id', account_kind='existing')
        await _open_uid_entry(bot, tg_id, state)
    elif action == 'id_guide':
        await _journey(bot, tg_id, (config.MSG_ENTER_UID,
            [[journey.cb('✅ Ready — Enter My ID', 'enter_id')],
             [journey.button('⬅️ Back', 'cb:home:access')]]),
            media=config.UID_GUIDE_PHOTO, key='account_id_guide')
    elif action == 'recheck':
        if user.get('verified'):
            await _show_menu(bot, tg_id)
        elif not user.get('uid'):
            await _resume_setup(bot, tg_id)
        else:
            await _submit_uid(tg_id, str(user['uid']), bot, state)
    elif action == 'usage':
        if not user.get('verified'):
            await _resume_setup(bot, tg_id)
            return
        premium, limit = await _user_quota(tg_id)
        used, left = await db.signal_state(tg_id, limit)
        await _journey(bot, tg_id, (config.MSG_LEVEL.format(icon=config.level_icon_tg(premium),
            name=config.level_name(premium), limit=limit, used=used, left=left),
            [[journey.cb('👑 View Premium Requirements', 'compare')],
             [journey.cb('💬 Access Help', 'support')], *journey.back('dashboard')]), key='access_usage')
    elif action == 'compare':
        if not user.get('verified'):
            await _resume_setup(bot, tg_id)
        elif user.get('is_premium'):
            await _journey(bot, tg_id, (config.MSG_PREMIUM_ALREADY.format(limit=config.daily_limit(True)),
                journey.back('dashboard')), key='premium_active')
        else:
            await _journey(bot, tg_id, journey.premium(), key='premium_requirements')
    elif action == 'premium_check':
        if tg_id in _premium_checks:
            await bot.send_message(tg_id, config.MSG_PREMIUM_CHECKING, parse_mode='HTML')
            return
        if not user.get('verified') or not user.get('uid'):
            await _resume_setup(bot, tg_id)
            return
        _premium_checks.add(tg_id)
        try:
            await _check_premium(tg_id, str(user['uid']), bot, state)
        finally:
            _premium_checks.discard(tg_id)
    elif action in ('notifications', 'notifications_on', 'notifications_off'):
        if action != 'notifications':
            await db.set_notifications(tg_id, action == 'notifications_on')
            user = await db.get_user(tg_id) or {}
        await _journey(bot, tg_id, journey.notifications(user), key='notifications')
    elif action == 'example':
        await _journey(bot, tg_id, journey.screen('screenshot', user), key='asset_guide')
    elif action == 'risks':
        # Retired Tools button on an older message returns to the current menu.
        await _journey(bot, tg_id, journey.screen('tools', user), key='flow_tools')
    elif action in journey.TEXTS:
        await _journey(bot, tg_id, journey.screen(action, user), key='flow_' + action)
    else:
        await _show_home(bot, tg_id)


@dp.callback_query(F.data.startswith("home:"))
async def home_action(cb: CallbackQuery, bot: Bot, state: FSMContext):
    action = cb.data.split(":", 1)[1]
    if action == 'sheet':
        await cb.answer()
        try:
            if await _send_gift(bot, cb.from_user.id):
                return
        except Exception as exc:
            logging.warning('Sheet delivery failed: %s', type(exc).__name__)
        await bot.send_message(cb.from_user.id, home_menu.SHEET_UNAVAILABLE)
        return
    if action == 'test':
        await market_signals.open_screen(cb, bot, state, render)
        return
    if action == 'language':
        await cb.answer()
        if await state.get_state() in (chart_signals.Chart.waiting_image.state, market_signals.Market.browsing.state):
            await state.clear()
        language = await localization.language_for(cb.from_user.id)
        await render(bot, cb.from_user.id, None, localization.SELECTOR_TEXT,
                     localization.selector_keyboard(language))
        return
    if action == 'back':
        await cb.answer()
        await state.clear()
        await _clear_nudge(bot, cb.from_user.id)
        await _show_home(bot, cb.from_user.id)
        return
    if action in home_menu.PENDING:
        await cb.answer(home_menu.PENDING[action], show_alert=True)
        return
    if action != 'access':
        await cb.answer()
        return
    await cb.answer()
    await state.clear()
    tg_id = cb.from_user.id
    user = await db.get_user(tg_id)
    if user and user['verified']:
        # Trust the saved verification just as /start did before the welcome menu.
        await _show_menu(bot, tg_id)
    else:
        await _resume_setup(bot, tg_id, state)


@dp.callback_query(F.data == 'reg:enter_uid')
async def enter_registration_uid(cb: CallbackQuery, bot: Bot, state: FSMContext):
    await flow_action(cb.model_copy(update={'data': 'flow:enter_id'}), bot, state)


@dp.callback_query(F.data.startswith('lang:'))
async def choose_language(cb: CallbackQuery, bot: Bot, state: FSMContext = None):
    language = cb.data.split(':', 1)[1]
    if language not in localization.CODES:
        await cb.answer('Please choose a language from the list.', show_alert=True)
        return
    await db.set_language(cb.from_user.id, language)
    if state is not None:
        # A previous analysis must not replace the newly translated home screen.
        # Persistent account/setup progress remains in the user's database row.
        await state.clear()
    await cb.answer('Language saved.')
    await _show_home(bot, cb.from_user.id)
    await _sync_welcome(bot, cb.from_user.id)

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


GIFT_FILE_ID_KEY = "money_management_gift_file_id"
GIFT_FILE_NAME_KEY = "money_management_gift_file_name"


@dp.message(Command("setgift"))
async def cmd_setgift(m: Message):
    """Save an Excel document uploaded to the Pocket IQ bot by an administrator."""
    if not _is_admin(m.from_user.id):
        logging.warning("non-admin %s tried /setgift", m.from_user.id)
        return
    document = getattr(m, "document", None)
    name = (getattr(document, "file_name", None) or "").strip()
    if not document or not name.lower().endswith((".xlsx", ".xls")):
        await m.answer(
            "Attach the .xlsx or .xls file as a document and put /setgift "
            "in the file caption.")
        return
    await db.set_setting(GIFT_FILE_ID_KEY, document.file_id)
    await db.set_setting(GIFT_FILE_NAME_KEY, name)
    await m.answer(
        "✅ GIFT UPLOADED\n\n"
        f"File: {name}\n"
        "Users can download it with Money Management Sheet on the welcome menu.")


def _sheet_kb():
    return build_kb([[("« Back", "cb:home:back")]])


async def _send_gift(bot, tg_id):
    """Send the saved workbook only on an explicit home-menu request."""
    file_id = await db.get_setting(GIFT_FILE_ID_KEY)
    if not file_id:
        logging.warning("gift requested for tg_id=%s but /setgift is not configured", tg_id)
        return False
    name = await db.get_setting(GIFT_FILE_NAME_KEY) or "Apex Trader Money Management.xlsx"
    await bot.send_document(
        tg_id, file_id,
        caption=home_menu.SHEET_CAPTION,
        parse_mode="HTML", reply_markup=_sheet_kb())
    await db.mark_gift_sent(tg_id)
    await activity_stats.record(tg_id, 'gift_delivered')
    logging.info("gift delivered tg_id=%s file=%r", tg_id, name)
    return True


async def _show_start_trading(bot, tg_id):
    await _resume_setup(bot, tg_id)


@dp.callback_query(F.data == "gift:downloaded")
async def gift_downloaded(cb: CallbackQuery, bot: Bot):
    await cb.answer("Gift confirmed")
    await db.acknowledge_gift(cb.from_user.id)
    await activity_stats.record(cb.from_user.id, 'gift_ack')
    await _show_start_trading(bot, cb.from_user.id)


@dp.callback_query(F.data == "gift:start")
async def gift_start(cb: CallbackQuery, bot: Bot, state: FSMContext):
    await cb.answer()
    tg_id = cb.from_user.id
    user = await db.get_user(tg_id)
    if user and user["verified"]:
        await _show_menu(bot, tg_id)
        return
    await state.clear()
    await _resume_setup(bot, tg_id)

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
    task = _signal_tasks.pop(tg_id, None)
    if task:
        task.cancel()
    # A verification still running from the previous run would clear its own
    # entry later and could report a verdict onto the fresh funnel, so it is
    # cancelled here with the other per-user tasks.
    inflight = _verify_inflight.pop(tg_id, None)
    if inflight and not inflight["task"].done():
        inflight["task"].cancel()
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
    await _journey(bot, tg_id, journey.screen('reviews', await db.get_user(tg_id)))

async def _open_registration(bot, tg_id, state):
    await _clear_nudge(bot, tg_id)
    await state.clear()
    await _resume_setup(bot, tg_id, state)


# Must precede the retired trading callbacks below. Existing messages migrate
# into the catalogue and cannot invoke screenshot uploads or random signals.
market_signals.install(dp, render)


@dp.callback_query(F.data.startswith("go:"))
async def nav(cb: CallbackQuery, bot: Bot, state: FSMContext):
    await cb.answer()
    key = cb.data.split(":", 1)[1]
    if key == "register":
        await _open_registration(bot, cb.from_user.id, state)
    else:
        await show(bot, cb.from_user.id, key)
        await state.clear()

async def _require_signal_verification(bot, tg_id, cb=None):
    # Only the shared analysis trial grants unverified signals (two lifetime slots).
    # Old/manual buttons must not unlock a separate, daily-resetting allowance.
    user = await db.get_user(tg_id)
    if user and user.get("verified"):
        return True
    if cb is not None:
        await cb.answer()
    await _show_register(bot, tg_id)
    return False


# Must stay above menu_action: aiogram matches handlers in definition order and
# that one swallows every "menu:" callback.
@dp.callback_query(F.data == "menu:signal")
async def menu_signal(cb: CallbackQuery, bot: Bot, state: FSMContext):
    if not await _require_signal_verification(bot, cb.from_user.id, cb):
        await state.clear()
        return
    # Reuse the asset catalogue and the shared candle-analysis allowance.
    old = _signal_tasks.pop(cb.from_user.id, None)
    if old:
        old.cancel()
    await market_signals.open_screen(cb, bot, state, render)

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
    # Returns (ui_id, extra_ids); render() or this task cleans up both messages
    # when navigation or the finished signal replaces the waiting screen.
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
    try:
        user = await db.get_user(tg_id)
    except Exception:
        logging.warning('Skipping message cleanup: cannot check welcome anchor', exc_info=True)
        return
    pinned = user.get('welcome_message_id') if user else None
    for mid in ids:
        if mid == pinned:
            continue
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
        # These IDs belong to this task even if navigation already removed them.
        # Do not clear a newer screen's companion-message tracking mid-wait.
        await _drop_msgs(bot, tg_id, extra_ids)
        user = await db.get_user(tg_id)
        if user and user["ui_msg_id"] != ui_id:
            # Another screen took over the chat while we counted down; it owns
            # ui_msg_id now, so dropping the signal beats clobbering it.
            return
        await db.set_album(tg_id, None)
        # Re-check after the countdown: verification may have been revoked.
        if not await _require_signal_verification(bot, tg_id):
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
    if not await _require_signal_verification(bot, tg_id, cb):
        return
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
    if not await _require_signal_verification(bot, tg_id, cb):
        return
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
    user = await db.get_user(tg_id)
    if not user or not user.get('verified'):
        await _resume_setup(bot, tg_id)
        return
    # One lookup feeds both the tier shown and the limit shown, and it is the
    # same helper _start_signal and _run_signal enforce with - the screen cannot
    # promise an allowance the server would refuse.
    premium, limit = await _user_quota(tg_id)
    used, left = await db.signal_state(tg_id, limit)
    await render(bot, tg_id, None,
                 config.MSG_LEVEL.format(icon=config.level_icon_tg(premium),
                                         name=config.level_name(premium),
                                         limit=limit, used=used, left=left),
                 [[journey.cb('👑 View Premium Requirements', 'compare')], *journey.back('dashboard')])

# Must stay above menu_action, same definition-order reason as menu_signal.
@dp.callback_query(F.data == "menu:premium")
async def menu_premium(cb: CallbackQuery, bot: Bot, state: FSMContext):
    await cb.answer()
    tg_id = cb.from_user.id
    user = await db.get_user(tg_id)
    if not user or not user['verified'] or not user['uid']:
        await _open_registration(bot, tg_id, state)
        return
    if user['is_premium']:
        await state.clear()
        text = config.MSG_PREMIUM_ALREADY.format(limit=config.daily_limit(True))
        await _journey(bot, tg_id, (text, journey.back('dashboard')))
    else:
        await state.clear()
        await _journey(bot, tg_id, journey.premium(), key='premium_requirements')

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

def _register_btn(label="\U0001F511 Register & Get Access",
                  icon="5307843983102204243", url=None):
    # Shared by the two verification verdicts. The defaults are what the
    # wrong-link verdict has always rendered and must not change; the deposit
    # verdict passes its own label and icon so only that screen restyles.
    # Style and position are the same for both and are not overridable.
    # url defaults to the un-subbed REF_LINK so a caller that cannot resolve a
    # sub-ID still renders a working button; both real callers pass one.
    return [[(label, "url:" + (url or config.REF_LINK), "success", icon)]]

async def _replace(bot, tg_id, old_msg_id, text, kb_rows=None):
    # Swap the transient "checking" message for the verdict; track as ui_msg.
    await _drop_msgs(bot, tg_id, [old_msg_id])
    markup = build_kb(kb_rows) if kb_rows else None
    m = await bot.send_message(tg_id, text, parse_mode="HTML", reply_markup=markup)
    await db.set_ui_msg(tg_id, m.message_id)

async def _show_menu(bot, tg_id, test_mode=False):
    user = await db.get_user(tg_id)
    if not user or not user.get('verified'):
        await _resume_setup(bot, tg_id)
        return
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
    return await render(bot, tg_id, s["photo"], text, journey.dashboard_keyboard())

def _with_ref(item, url):
    # Rewrite only the referral URL; ID-entry and Back callbacks pass through.
    if item[1] == "url:" + config.REF_LINK:
        return (item[0], "url:" + url) + tuple(item[2:])
    return item


async def _show_register(bot, tg_id, state=None):
    # Preserve the existing attribution sub-ID on the registration button.
    user = await db.get_user(tg_id) or {}
    if user.get('verified'):
        await _show_menu(bot, tg_id)
        return
    if user.get('uid'):
        await _resume_setup(bot, tg_id, state)
        return
    url = await _ref_url(tg_id)
    await db.set_setup_stage(tg_id, 'register', account_kind=user.get('account_kind') or 'new')
    if state is not None:
        await state.set_state(Reg.waiting_uid.state)
    await activity_stats.record(tg_id, 'registration_opened')
    await _journey(bot, tg_id, journey.registration(url), media=journey.REGISTER_PHOTO, key='register')


async def _clear_nudge(bot, tg_id):
    """Clean up a promotion sent before the registration follow-up was removed.

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

async def _panel_verdict(tg_id, uid, *, minimum_deposit=None):
    """ONE panel query, classified. Sends nothing to the user.

    Returns (status, detail). Splitting the query from the messaging is what
    lets the caller retry a VERIFY_TEMPORARY without re-sending the "checking"
    ack or showing a verdict that is about to be superseded. The campaign and
    deposit rules are exactly as they were - only their result is now named
    rather than flattened into a bool.
    """
    minimum_deposit = config.MIN_DEPOSIT if minimum_deposit is None else minimum_deposit
    try:
        info = await panelbot.lookup_trader(uid)
    except panelbot.PanelUnavailable as e:
        # The reason string ("disabled"/"timeout"/"floodwait"/"session"/
        # "warmup"/"error") is what separates a broken session from a slow
        # panel, and it selects the backoff below.
        logging.warning("VERIFY uid=%s tg_id=%s -> PanelUnavailable(%s) -> TEMPORARY",
                        uid, tg_id, e)
        return config.VERIFY_TEMPORARY, str(e)
    if info and str(info.get("campaign_id")) == str(config.CAMPAIGN_ID):
        try:
            dep = Decimal(str(info.get('sum_deposits')))
        except Exception:
            return config.VERIFY_TEMPORARY, 'deposit_unavailable'
        if not dep.is_finite() or dep < 0:
            return config.VERIFY_TEMPORARY, 'deposit_unavailable'
        logging.info("VERIFY uid=%s tg_id=%s campaign MATCH dep=%s min=%s -> %s",
                     uid, tg_id, dep, minimum_deposit,
                     "ACCESS" if dep >= minimum_deposit else "NEED_DEPOSIT")
        if dep >= minimum_deposit:
            return config.VERIFY_GRANTED, dep
        return config.VERIFY_NEED_DEPOSIT, dep
    # Not found, or a different campaign. record_found=False with a healthy
    # panel usually means the reply format changed - see PANEL PARSE above.
    logging.info("VERIFY uid=%s tg_id=%s -> WRONG_LINK (record_found=%s "
                 "campaign_id=%s expected=%s)", uid, tg_id, info is not None,
                 info.get("campaign_id") if info else None, config.CAMPAIGN_ID)
    return config.VERIFY_WRONG_LINK, info

def _retry_delay(attempt, reason):
    # Doubling backoff. "floodwait" means the panel has already rate-limited
    # us, so it gets a much longer floor - retrying that quickly is what turns
    # a short block into a long one for every user, not just this one.
    delay = config.UID_VERIFY_BACKOFF * (2 ** attempt)
    if reason == "floodwait":
        return max(delay, config.UID_VERIFY_FLOOD_BACKOFF)
    return delay

async def _run_verification(bot, tg_id, uid):
    """Run the panel check, retry transient failures, and show the verdict.

    Returns one of the config.VERIFY_* statuses. A settled refusal
    (VERIFY_NOT_ELIGIBLE) is returned on the first answer and never retried -
    the panel has told us something true about the account. Only
    VERIFY_TEMPORARY is retried, and only inside this one call, so the user is
    never asked to resend an ID they already sent.
    """
    # Immediate ack while we query the panel (can take up to ~20s), then verdict.
    # It stays up across retries, so a transient failure is invisible to the
    # user rather than a verdict that flickers and is replaced.
    ack = await bot.send_message(
        tg_id, "\U000023F3 <b>Checking account</b> <code>" + uid + "</code>\U00002026",
        parse_mode="HTML")

    status, detail = await _panel_verdict(tg_id, uid)
    attempt = 0
    while status == config.VERIFY_TEMPORARY and attempt < config.UID_VERIFY_RETRIES:
        delay = _retry_delay(attempt, detail)
        attempt += 1
        logging.info("VERIFY RETRY tg_id=%s uid=%s reason=%s attempt=%d/%d in %.1fs",
                     tg_id, uid, detail, attempt, config.UID_VERIFY_RETRIES, delay)
        await asyncio.sleep(delay)
        status, detail = await _panel_verdict(tg_id, uid)

    if status == config.VERIFY_GRANTED:
        await db.set_verified(tg_id, detail)
        await db.record_access_check(tg_id, status, detail)
        await db.set_setup_stage(tg_id, 'active')
        await _clear_nudge(bot, tg_id)
        # Confirm completion and let the user choose their next action.
        try:
            await bot.delete_message(chat_id=tg_id, message_id=ack.message_id)
        except Exception:
            pass
        await _journey(bot, tg_id, journey.ready(), key='access_ready')
        return status
    if status == config.VERIFY_NEED_DEPOSIT:
        await db.record_access_check(tg_id, status, detail)
        await db.set_setup_stage(tg_id, 'deposit')
        await activity_stats.record(tg_id, 'referral_confirmed')
        # Campaign matched, but the required access deposit is still missing.
        try:
            await bot.delete_message(chat_id=tg_id, message_id=ack.message_id)
        except Exception:
            pass
        await _journey(bot, tg_id, journey.deposit(detail), key='deposit_requirements')
        return status
    if status == config.VERIFY_WRONG_LINK:
        await db.record_access_check(tg_id, status)
        await db.set_setup_stage(tg_id, 'help')
        try:
            await bot.delete_message(chat_id=tg_id, message_id=ack.message_id)
        except Exception:
            pass
        await _show_account_recovery(bot, tg_id)
        return status
    # Every retry was also temporary. Say so once, after the attempts, rather
    # than after each one.
    logging.warning("VERIFY EXHAUSTED tg_id=%s uid=%s reason=%s after %d retries",
                    tg_id, uid, detail, attempt)
    await db.record_access_check(tg_id, config.VERIFY_TEMPORARY)
    await db.set_setup_stage(tg_id, 'help')
    text, rows = journey.pending()
    await _replace(bot, tg_id, ack.message_id, text, rows)
    return config.VERIFY_TEMPORARY

async def _verify_account_id(tg_id, account_id):
    """Validate the ID format before querying the real affiliate panel."""
    account_id = (account_id or "").strip()
    ok = bool(UID_RE.fullmatch(account_id))
    logging.info("ACCOUNT ID CHECK tg_id=%s id=%r -> %s", tg_id,
                 account_id[:16], "VALID" if ok else "INVALID")
    return ok

# Screenshot handlers are retired. Market handlers are installed before the
# legacy trading callbacks and UID capture, while commands keep priority.


# MUST stay above the Reg.waiting_uid handler and uid_anytime below: aiogram
# dispatches in definition order, and uid_anytime matches any bare number in
# any state. Registered here, Premium.waiting_uid wins while it is armed, so a
# UID sent during the unlock flow never reaches the funnel's capture path.
@dp.message(Premium.waiting_uid)
async def premium_uid(m: Message, bot: Bot, state: FSMContext):
    tg_id = m.from_user.id
    # Acquire before the first await so simultaneous messages cannot both pass.
    if tg_id in _premium_checks:
        return
    _premium_checks.add(tg_id)
    try:
        await _check_premium_uid(m, bot, state)
    finally:
        _premium_checks.discard(tg_id)


async def _check_premium_uid(m, bot, state):
    tg_id = m.from_user.id
    uid = (m.text or "").strip()
    try:
        await bot.delete_message(chat_id=tg_id, message_id=m.message_id)
    except Exception:
        pass
    await _check_premium(tg_id, uid, bot, state)


async def _check_premium(tg_id, uid, bot, state):
    user = await db.get_user(tg_id)
    if not user or not user['verified'] or not user['uid']:
        await _open_registration(bot, tg_id, state)
        return
    if user['is_premium']:
        await state.clear()
        await render(bot, tg_id, None, config.MSG_PREMIUM_ALREADY.format(
            limit=config.daily_limit(True)), config.UNLOCK_KB)
        return
    if not UID_RE.fullmatch(uid):
        await db.record_access_check(tg_id, config.VERIFY_INVALID_UID)
        await render(bot, tg_id, None, config.MSG_ACCOUNT_ID_INVALID, config.UNLOCK_KB)
        return
    if uid != str(user['uid']):
        await render(bot, tg_id, None, config.MSG_PREMIUM_ACCOUNT_MISMATCH, config.UNLOCK_KB)
        return

    await render(bot, tg_id, None, config.MSG_PREMIUM_CHECKING, config.UNLOCK_KB)
    try:
        # Always verify the actual deposit, even when VERIFY_MODE=test.
        # No cached deposit, token balance or format-only check can grant Premium.
        status, deposit = await asyncio.wait_for(
            _panel_verdict(tg_id, uid, minimum_deposit=config.PREMIUM_MIN_DEPOSIT),
            timeout=config.UID_VERIFY_TIMEOUT)
    except Exception as exc:
        logging.warning('Premium verification unavailable: %s', type(exc).__name__)
        status, deposit = config.VERIFY_TEMPORARY, None

    if status == config.VERIFY_GRANTED:
        try:
            unlocked = await db.unlock_premium(
                tg_id, uid, deposit, config.PREMIUM_MIN_DEPOSIT)
        except Exception as exc:
            logging.warning('Premium grant unavailable: %s', type(exc).__name__)
            unlocked = False
        if unlocked:
            await state.clear()
            text = config.MSG_PREMIUM_UNLOCKED.format(limit=config.daily_limit(True))
        else:
            text = config.MSG_PREMIUM_RETRY
    elif status == config.VERIFY_NEED_DEPOSIT:
        await _journey(bot, tg_id, journey.premium(deposit), key='premium_deposit_required')
        return
    elif status == config.VERIFY_WRONG_LINK:
        await _journey(bot, tg_id, journey.unlinked(premium=True), key='account_unlinked')
        return
    else:
        await _journey(bot, tg_id, journey.pending(True), key='premium_pending')
        return
    await render(bot, tg_id, None, text, journey.back('dashboard'))

def _clear_inflight(tg_id, task):
    """Drop the in-flight entry, but only if it is still THIS task.

    Guards against a stale callback wiping a newer attempt's entry: if the user
    has already started a fresh verification by the time an old task finishes,
    the newer entry must survive.
    """
    entry = _verify_inflight.get(tg_id)
    if entry is not None and entry.get("task") is task:
        _verify_inflight.pop(tg_id, None)

async def _verify_once(bot, tg_id, uid):
    """Run ONE verification to completion and return its config.VERIFY_* status.

    This is a wrapper, not a replacement: the actual check is still
    _run_verification -> panelbot.lookup_trader, with the same campaign and
    deposit rules. What it adds is a ceiling and a guarantee that no exception
    escapes into a bare task, because the caller's in-flight entry is cleared
    from this task's done callback and a task that dies silently would leave
    the user unable to try again.
    """
    try:
        return await asyncio.wait_for(_run_verification(bot, tg_id, uid),
                                      timeout=config.UID_VERIFY_TIMEOUT)
    except asyncio.TimeoutError:
        # The panel has its own HARD_TIMEOUT; reaching this one means the wait
        # was for panelbot's lock, not the round-trip. Same user-facing verdict
        # as any other transient panel problem.
        logging.warning("VERIFY TIMEOUT tg_id=%s uid=%s exceeded %ss ceiling",
                        tg_id, uid, config.UID_VERIFY_TIMEOUT)
        try:
            await db.record_access_check(tg_id, config.VERIFY_TEMPORARY)
            await _journey(bot, tg_id, journey.pending(), key='verification_pending')
        except Exception:
            logging.exception("could not deliver the delay notice to %s", tg_id)
        return config.VERIFY_TEMPORARY
    except asyncio.CancelledError:
        raise
    except Exception:
        # Log the real exception rather than letting it vanish into the task.
        logging.exception("verification failed for tg_id=%s uid=%s", tg_id, uid)
        try:
            await db.record_access_check(tg_id, config.VERIFY_TEMPORARY)
            await _journey(bot, tg_id, journey.pending(), key='verification_pending')
        except Exception:
            logging.exception("could not deliver the error notice to %s", tg_id)
        return config.VERIFY_TEMPORARY

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
    await _submit_uid(tg_id, uid, bot, state)


async def _submit_uid(tg_id, uid, bot, state):
    """Shared, single-flight account check for typed IDs and the saved-ID button."""
    user = await db.get_user(tg_id)
    if user and user["verified"]:
        # Already verified: never re-run the panel, just put them back on the
        # menu. Same reasoning as /start - a stale flag costs nothing, an
        # avoidable lookup risks a FloodWait that hits everyone.
        await state.clear()
        await _show_menu(bot, tg_id)
        return
    if not user:
        await state.clear()
        await _resume_setup(bot, tg_id)
        return
    if not UID_RE.fullmatch(uid):
        # Numeric but the wrong length. Answered from the format rule alone -
        # the panel is never queried for something that cannot be an account id.
        await bot.send_message(tg_id, "\U00002757 Your account ID must be <b>numbers only</b> "
                               "(5\U0000201315 digits). Example: <b>123456789</b>", parse_mode="HTML")
        await state.set_state(Reg.waiting_uid)
        return
    if elena.checking(tg_id):
        await bot.send_message(tg_id, config.MSG_UID_IN_FLIGHT.format(uid=uid), parse_mode='HTML')
        return
    # --- single flight ------------------------------------------------------
    # Deliberately BEFORE the cooldown check. A duplicate of an ID that is
    # already being checked must not be told to wait and resend - that pairing
    # (resend -> "wait Ns, then resend") is what made one submission take
    # several attempts. Reuse the running check instead; no second lookup.
    inflight = _verify_inflight.get(tg_id)
    if inflight is not None and not inflight["task"].done():
        if inflight["uid"] == uid:
            logging.info("UID IN-FLIGHT tg_id=%s uid=%s - reusing the running check",
                         tg_id, uid)
            await bot.send_message(tg_id, config.MSG_UID_IN_FLIGHT.format(uid=uid),
                                   parse_mode="HTML")
            # Await the SAME task, so this caller ends on the same verdict the
            # first one gets. The task itself sends the verdict message.
            status = await asyncio.shield(inflight["task"])
            if status != config.VERIFY_GRANTED:
                await state.set_state(Reg.waiting_uid)
            return
        # A different ID mid-check. The running check stays authoritative -
        # nothing is cancelled and nothing runs in parallel.
        logging.info("UID BUSY tg_id=%s sent=%s while checking=%s",
                     tg_id, uid, inflight["uid"])
        await bot.send_message(
            tg_id, config.MSG_UID_OTHER_PENDING.format(uid=inflight["uid"]),
            parse_mode="HTML")
        await state.set_state(Reg.waiting_uid)
        return

    wait = config.UID_LOOKUP_COOLDOWN - (time.monotonic() - _uid_lookup_at.get(tg_id, 0.0))
    if wait > 0:
        # Throttled before the shared panel queue is ever touched. With the
        # in-flight guard above, this now only applies BETWEEN attempts.
        logging.info("UID COOLDOWN tg_id=%s uid=%s %.0fs remaining", tg_id, uid, wait)
        await bot.send_message(tg_id, config.MSG_UID_COOLDOWN.format(seconds=int(wait) + 1),
                               parse_mode="HTML")
        await state.set_state(Reg.waiting_uid)
        return
    # No uniqueness gate: a uid already held by another telegram account still
    # goes through the full panel lookup, and access is granted on the campaign
    # + deposit check alone. Sharing is allowed by design.
    await db.save_uid_only(tg_id, uid)
    await activity_stats.record(tg_id, 'uid_submitted')
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
    if config.TEST_MODE and _is_admin(tg_id):
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
    # Submitted immediately, as a task, so duplicates arriving while it runs
    # can find and reuse it instead of queueing a second lookup. The entry is
    # published BEFORE the first await on the task, so there is no window in
    # which a duplicate could slip past the guard above.
    task = asyncio.create_task(_verify_once(bot, tg_id, uid))
    _verify_inflight[tg_id] = {"uid": uid, "task": task}
    # Cleanup is bound to the task, not to this coroutine: if this handler is
    # cancelled mid-await the check still finishes and still clears its entry.
    task.add_done_callback(lambda t, k=tg_id: _clear_inflight(k, t))
    status = await asyncio.shield(task)
    if status != config.VERIFY_GRANTED:
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
                status, dep = await _panel_verdict(r['tg_id'], r['uid'])
                if status == config.VERIFY_TEMPORARY:
                    break
                if status == config.VERIFY_GRANTED:
                    await db.set_verified(r["tg_id"], dep)
                    await db.record_access_check(r['tg_id'], status, dep)
                    await db.set_setup_stage(r['tg_id'], 'active')
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
    async with db.pool.acquire() as conn:
        await conn.execute(broadcast.SCHEMA)
        await conn.execute(verification_reminders.SCHEMA)
        await conn.execute(elena.store.SCHEMA)
        await conn.execute(activity_stats.SCHEMA)
        await conn.execute(analytics.SCHEMA)
        await conn.execute(chart_signals.api.SCHEMA)
        await conn.execute(chart_signals.trial.SCHEMA)
    # This fixed, transactional migration runs once, before updates or workers.
    # It clears all old access; subsequent boots retain newly completed setup.
    import account_reset
    await account_reset.apply(db.pool)
    logging.info('Candle chart analysis ready; provider=%s; API configured=%s; model=%s; strategy=%s',
                 chart_signals.api.provider(), chart_signals.api.configured(), chart_signals.api.model(),
                 chart_signals.chart_strategy.VERSION)
    logging.info('Premium deposit verification ready; minimum=$%s; daily AI signals=%s',
                 config.PREMIUM_MIN_DEPOSIT, config.daily_limit(True))
    logging.info('Verified chart quota ready; Start=%s/day; Premium=%s/day; reset=00:00 UTC',
                 config.daily_limit(False), config.daily_limit(True))
    logging.info('Asset analysis ready; provider=%s; configured=%s; public enabled=%s',
                 market_data.provider(), market_data.configured(), market_data.public_enabled())
    # Validate dashboard queries against the live schema before accepting commands.
    await activity_stats.report()
    for report_name in analytics_reports.QUERIES:
        await analytics_reports.fetch(report_name, 30, limit=0)
    detailed_activity = analytics.InboundMiddleware()
    dp.message.outer_middleware(detailed_activity)
    dp.callback_query.outer_middleware(detailed_activity)
    activity_middleware = activity_stats.ActivityMiddleware()
    dp.message.outer_middleware(activity_middleware)
    dp.callback_query.outer_middleware(activity_middleware)
    elena_activity = elena.ActivityMiddleware()
    dp.message.outer_middleware(elena_activity)
    dp.callback_query.outer_middleware(elena_activity)
    logging.info('Admin activity statistics ready; /stats enabled')
    logging.info('Broadcast schema ready; admin drafting and delivery queue enabled')
    bot = Bot(os.environ["BOT_TOKEN"])
    bot.session.middleware(analytics.OutboundMiddleware())
    logging.info('Detailed analytics ready; /analytics and /userstats enabled; link tracking=%s',
                 bool(config.ANALYTICS_LINK_BASE))
    language_context = localization.UserContextMiddleware()
    dp.message.outer_middleware(language_context)
    dp.callback_query.outer_middleware(language_context)
    dp.business_message.outer_middleware(language_context)
    activation_gate = activation.GateMiddleware()
    dp.message.outer_middleware(activation_gate)
    dp.callback_query.outer_middleware(activation_gate)
    logging.info('Elena activation gate ready; enabled=%s; required version=%s; all earlier access requires reactivation',
                 activation.enabled(), elena.store.ACTIVATION_VERSION)
    bot.session.middleware(localization.OutboundMiddleware())
    logging.info('Localization ready: 12 languages; saved per user')
    if config.TEST_MODE:
        logging.warning("VERIFY_MODE=test - admin-only verification bypass is enabled; "
                        "all other users still require live verification.")
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
    tasks = [server.serve(), dp.start_polling(bot), broadcast.worker(bot),
             verification_reminders.worker(bot), elena.worker(bot), _refresh_saved_welcomes(bot), market_data.startup_check(),
             bot_profile.sync(bot)]
    if config.ENABLE_AUTO_RETRY:
        logging.info("ENABLE_AUTO_RETRY on - background re-check every 30 min")
        tasks.append(retry_worker(bot))
    else:
        logging.info("ENABLE_AUTO_RETRY off - no background re-check; users "
                     "re-send their account ID after depositing to verify")
    try:
        await asyncio.gather(*tasks)
    finally:
        await chart_signals.api.close_provider_client()
        await market_data.close()

asyncio.run(main())

