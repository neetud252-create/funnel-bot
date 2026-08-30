import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import re
from decimal import Decimal

_ch = os.getenv("CHANNEL_ID", "@apexxtraderz")
CHANNEL_ID  = int(_ch) if _ch.lstrip("-").isdigit() else _ch
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/apexxtraderz")

def _channel_mention(url):
    # "https://t.me/apexxtraderz" -> "@apexxtraderz", which Telegram auto-links
    # as a mention with no markup needed. Derived from CHANNEL_URL rather than
    # written out again, so the handle on the gate screen cannot drift from the
    # channel the Join button actually opens.
    #
    # Anything that is not a public t.me handle - a private invite link
    # (t.me/+hash), a joinchat URL, an override pointing elsewhere - falls back
    # to the raw URL. That is what this screen displayed before, and it still
    # auto-links, so an unusual CHANNEL_URL degrades instead of showing a
    # mention that does not resolve.
    m = re.match(r"https?://t\.me/([A-Za-z][A-Za-z0-9_]{3,31})/?$",
                 (url or "").strip())
    return "@" + m.group(1) if m else (url or "")

CHANNEL_MENTION = _channel_mention(CHANNEL_URL)
# Link defaults MUST stay valid http(s) URLs even when unset: Telegram rejects
# the whole message if any inline button URL is malformed, which takes down the
# entire screen (this is what broke the menu after verification).
# TODO: swap the four PLACEHOLDER links below for the real ones (set REF_LINK,
# SUPPORT, VIP_LINK, YOUTUBE_URL, FOREX_TIPS_URL in the Railway service
# variables).
REF_LINK    = os.getenv("REF_LINK", "https://example.com/PLACEHOLDER_REF")

SUPPORT     = os.getenv("SUPPORT", "https://t.me/flashhher")   # TODO: real support handle (was @go_plus_supportbot)
SUPPORT_URL = "https://t.me/" + SUPPORT.lstrip("@")
VIP_LINK    = os.getenv("VIP_LINK", "https://t.me/PLACEHOLDER_VIP")          # TODO: real VIP team invite
YOUTUBE_URL = os.getenv("YOUTUBE_URL", "https://youtube.com/@pocketoption?si=gb2BpGjz2SzhMOH6s")  # TODO: real YouTube channel
# Forex Tips button on the access screen. The project had NO existing forex-tips
# destination, so this is a configuration slot rather than a link anyone chose:
# the placeholder keeps the button renderable (build_kb drops a button whose URL
# fails _URL_OK, which would silently remove it) until the real URL is set.
FOREX_TIPS_URL = os.getenv("FOREX_TIPS_URL", "https://t.me/PLACEHOLDER_FOREX_TIPS")  # TODO: real Forex Tips link

# Query parameter the affiliate panel reads back as the sub-ID. CONFIRMED
# against the Pocket Option panel: it echoes this value into its postbacks as
# click_id, so that is the name we must send. This is the outbound half of the
# join - change it and server.py's SUBID_KEYS together, or the probe there
# stops finding rows. Still env-overridable so a panel-side rename can be
# absorbed without a code deploy.
REF_SUB_PARAM = os.getenv("REF_SUB_PARAM", "click_id")

# The one definition of a tracking-id's shape, used by BOTH halves: bot.py
# validates the /start deep-link payload with it, and server.py validates the
# /click endpoint's cid with it. They must agree - a cid the landing page can
# POST but the bot would reject (or vice versa) is a click that can never be
# joined to a user - so neither module gets its own copy.
# 1-64 characters of A-Za-z0-9_- is exactly what Telegram itself will carry in
# a deep-link payload, which is the tighter of the two constraints.
REF_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# --- /click endpoint (server.py) --------------------------------------------
# Origins allowed to POST /click, comma-separated. The landing page is a
# browser, so without its origin here the request never leaves it. Empty means
# no browser may post - server-to-server callers are unaffected.
CLICK_ORIGINS = [o.strip() for o in os.getenv("CLICK_ORIGIN", "").split(",")
                 if o.strip()]
# Per-IP fixed window. /click is public and unauthenticated, so this is the
# only thing standing between it and an open write endpoint.
CLICK_RATE_MAX = int(os.getenv("CLICK_RATE_MAX", "20"))
CLICK_RATE_WINDOW = float(os.getenv("CLICK_RATE_WINDOW", "60"))
# Ceiling on the in-memory rate-limit table, so rotating source IPs cannot grow
# it without bound. Expired entries are pruned first; a full table then evicts
# the oldest window rather than refusing traffic.
CLICK_RATE_MAX_IPS = int(os.getenv("CLICK_RATE_MAX_IPS", "20000"))
# Bytes. A beacon carrying the documented fields is a few hundred; this is the
# point past which a body is not worth reading, let alone parsing.
CLICK_MAX_BYTES = int(os.getenv("CLICK_MAX_BYTES", "4096"))

# --- Meta Conversions API (server.py) ---------------------------------------
# Server-side CompleteRegistration, sent when an affiliate registration
# postback joins back to a user we have a click for. The browser-side pixel and
# this share event_id so Meta deduplicates them into one event.
#
# The dataset is the Go+ pixel. The token is a system-user access token and is
# the ONE secret in this feature: it is sent in the request body, never in a
# URL, and never logged. Empty token means the integration is off - no event is
# built and nothing is sent - which is the correct default for a deploy that
# has not been given one.
META_DATASET_ID = os.getenv("META_DATASET_ID", "2016650609225629")
META_CAPI_TOKEN = os.getenv("META_CAPI_TOKEN", "").strip()
# Graph API version. Pinned rather than left unversioned, because an
# unversioned call silently follows Meta's rollouts; set it to whatever version
# the app targets in Events Manager.
META_API_VERSION = os.getenv("META_API_VERSION", "v21.0")
# FALLBACK event_source_url. Meta requires one for action_source "website",
# and the real value is per click: the landing page sends its own location.href
# as page_url and it is stored on the clicks row, which is what the sender
# prefers. This covers the clicks that have none - everything captured before
# the beacon started sending it, and any beacon that omits it.
# Note clicks.referrer is NOT a candidate: that column holds where the visitor
# came FROM (facebook.com), not the page they landed on.
META_EVENT_SOURCE_URL = (os.getenv("META_EVENT_SOURCE_URL", "").strip()
                         or (CLICK_ORIGINS[0] if CLICK_ORIGINS else ""))
# Seconds. The send is fired as a background task and never blocks the 200 to
# the affiliate system, so this only bounds how long that task may linger.
META_CAPI_TIMEOUT = float(os.getenv("META_CAPI_TIMEOUT", "10"))
# Set while validating in Events Manager's Test Events tab; unset in production.
META_TEST_EVENT_CODE = os.getenv("META_TEST_EVENT_CODE", "").strip()


def ref_url(sub_id, base=None):
    """REF_LINK with the sub-ID attached as REF_SUB_PARAM.

    Parsed rather than concatenated: REF_LINK may already carry a query string
    (utm_*, campaign ids), so a bare "?" + param would corrupt it. Any existing
    value for the same parameter is dropped rather than duplicated, which keeps
    the function idempotent - passing an already-subbed URL back in returns the
    same URL with the new sub-ID, not two of them.
    """
    parts = urlsplit(REF_LINK if base is None else base)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k != REF_SUB_PARAM]
    query.append((REF_SUB_PARAM, str(sub_id)))
    return urlunsplit(parts._replace(query=urlencode(query)))
ADMIN_IDS   = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

# Group F verification thresholds.
MIN_DEPOSIT = Decimal(os.getenv("MIN_DEPOSIT", "50"))
CAMPAIGN_ID = os.getenv("CAMPAIGN_ID", "969716")

# Verification mode. "live" (default) runs the real @AffiliatePocketBot lookup.
# "test" skips the panel entirely and verifies any numeric UID on the spot.
# TODO: VERIFY_MODE MUST be set back to "live" (or unset) before real users
# reach the bot - in test mode ANY account ID gets full access with no check
# against the campaign or the deposit minimum.
VERIFY_MODE = os.getenv("VERIFY_MODE", "live").strip().lower()
TEST_MODE = VERIFY_MODE == "test"

# Background re-check of pending UIDs (bot.py retry_worker). OFF by default:
# after depositing, the user re-sends their account ID to trigger a fresh
# lookup, which is what MSG_NEED_DEPOSIT now instructs. Set ENABLE_AUTO_RETRY=1
# to restore the 30-minute sweep that used to complete verification on its own.
ENABLE_AUTO_RETRY = os.getenv("ENABLE_AUTO_RETRY", "").strip().lower() in ("1", "true", "yes", "on")

# Minimum seconds between panel lookups for ONE user. Lookups are serialised
# behind a lock with SPACING between them, so a handful of users re-sending
# account IDs can stack into a FloodWait that disables verification for
# everyone. This throttles per user before the shared queue is ever touched.
UID_LOOKUP_COOLDOWN = int(os.getenv("UID_LOOKUP_COOLDOWN", "20"))

# Outer ceiling on ONE verification attempt, measured around the whole
# _run_verification call. panelbot already caps the panel round-trip itself
# (HARD_TIMEOUT), so this is the backstop for the part that timeout does not
# cover: waiting for panelbot's shared lock when other lookups are queued
# ahead. Without it a wedged holder would keep a user's in-flight entry alive
# forever and block their next attempt. Generous on purpose - it must not fire
# on a merely slow panel.
UID_VERIFY_TIMEOUT = int(os.getenv("UID_VERIFY_TIMEOUT", "90"))

# --- Verification outcomes --------------------------------------------------
# _run_verification used to answer a bare True/False, which made "the panel is
# unreachable" indistinguishable from "this account does not qualify". Only the
# first of those is worth retrying; retrying the second would re-query the panel
# on a settled answer. These four names are what keeps them apart.
VERIFY_GRANTED      = "granted"        # campaign matched and the deposit clears
VERIFY_NEED_DEPOSIT = "need_deposit"   # matched, deposit under MIN_DEPOSIT
VERIFY_WRONG_LINK   = "wrong_link"     # not found, or a different campaign
VERIFY_TEMPORARY    = "temporary"      # panel unavailable / timeout / flood etc

# The two settled refusals. A settled answer is never retried: the panel has
# told us something true about the account, and asking again cannot change it.
VERIFY_NOT_ELIGIBLE = (VERIFY_NEED_DEPOSIT, VERIFY_WRONG_LINK)

# Internal retry for VERIFY_TEMPORARY only, inside the one verification task.
# The user never resends: retries happen behind the same in-flight entry, so a
# transient panel problem costs them nothing.
#
# RETRIES is EXTRA attempts after the first, so 2 means at most 3 lookups.
# Backoff doubles per attempt. "floodwait" gets its own, much longer floor:
# that reason means the panel has ALREADY rate-limited us, and retrying it
# quickly is what turns a short block into a long one.
UID_VERIFY_RETRIES = int(os.getenv("UID_VERIFY_RETRIES", "2"))
UID_VERIFY_BACKOFF = float(os.getenv("UID_VERIFY_BACKOFF", "3"))
UID_VERIFY_FLOOD_BACKOFF = float(os.getenv("UID_VERIFY_FLOOD_BACKOFF", "30"))

# Chat the boot-time media warm sends throwaway uploads to (and deletes again).
# Telegram issues a file_id only in response to an actual send, so warming needs
# somewhere to send. Falls back to the first admin; unset means warming is
# skipped and the first user on each screen pays for that upload once.
_warm = os.getenv("MEDIA_WARM_CHAT", "").strip()
MEDIA_WARM_CHAT = (int(_warm) if _warm.lstrip("-").isdigit()
                   else (_warm or (ADMIN_IDS[0] if ADMIN_IDS else None)))

E_INFO  = "5334544901428229844"
E_POINT = "5415758949129404605"
E_BACK  = "5305522282695768654"
E_MONEY = "5224257782013769471"
E_FLASH = "5823347218056221496"
E_STAR  = "5463297803235113601"
E_GEM   = "5427168083074628963"
E_QMARK = "5436113877181941026"
E_ROBOT = "5287684458881756303"
E_GREEN = "5188234920639632382"
E_DOWN  = "5447183459602669338"
E_SHAKE = "5357122032674818130"
E_GEAR  = "5341715473882955310"
E_N1    = "5778373820930858379"
E_N2    = "5778382698628256004"
E_N3    = "5778338052443213984"
E_N4    = "5778346006722646362"
E_N5    = "5778205144680239810"
E_CLOCK = "5258095024725321202"
E_CHART = "5231200819986047254"
E_LENS  = "5348544647977254780"
E_BOLT  = "5895638385300606573"
# Signal loading screen only. Deliberately separate IDs from E_CHART / E_LENS
# above: those belong to other screens and must not shift if this one is
# restyled.
E_SIG_CHART = "5451882707875276247"
E_SIG_LENS  = "5188217332748527444"
# Activation nudge (REGISTER_NUDGE) only. Swap these to change its emoji
# without touching the copy. Both are rendered through pe(), which embeds the
# plain-emoji fallback that clients without premium emoji show instead - an
# invalid ID makes Telegram reject the whole message, so keep the fallbacks.
E_NUDGE_WARN          = "5420323339723881652"
E_NUDGE_ROCKET        = "5188481279963715781"
NUDGE_WARN_FALLBACK   = "\U000026A0\U0000FE0F"   # warning sign
NUDGE_ROCKET_FALLBACK = "\U0001F680"             # rocket
E_SIG_GLASS = "5386367538735104399"
# Expiration-time screen (SCREENS["test_menu"]) only. Swap these to restyle it
# without touching the copy; each is rendered through pe(), which keeps the
# plain-emoji fallback that non-Premium clients see.
E_EXP_CLOCK = "5382194935057372936"
E_EXP_BULB  = "5258216851472654189"
E_EXP_DOWN  = "5406745015365943482"

# Main-menu and trading-mode MESSAGE emoji - the ones inside a screen's caption,
# as opposed to the button icons further down. These go through pe() into
# <tg-emoji> entities, so they render only on a message sent with
# parse_mode="HTML"; render() always does.
E_MENU_HEADER   = "5188678912883827293"   # the robot on "Go+ main menu"
E_MENU_SIGNALS  = "5429633836684157942"   # the bolt on the "Signals" line
E_MENU_LEVEL_HD = "5370688996844249600"   # the battery on "Your level:"
E_LEVEL_START   = "5274026806477857971"   # the star on the Start tier itself
E_MODE_MANUAL = "5258011929993026890"     # the person after "Manual"
E_MODE_AUTO   = "4943239162758169437"     # the star-struck face after "Automatic"
# Subscription gate (SCREENS["gate"]) only. The first four are CAPTION entities
# rendered through pe(); the last two are BUTTON icons and ride on the 4th
# tuple element (icon_custom_emoji_id), never inside the button label.
#
# NOTE: E_GATE_DOWN carries the same id as E_BACK above. It is declared
# separately rather than aliased, so restyling the Back arrow cannot silently
# change this screen, and vice versa.
E_GATE_LOCK  = "5296369303661067030"      # the padlock on the headline
E_GATE_MEGA  = "5983400750594658672"      # the megaphone before the handle
E_GATE_SOUND = "5247187233722607160"      # the speaker before the handle
E_GATE_DOWN  = "5305522282695768654"      # the finger pointing at the buttons
E_GATE_JOIN  = "5397916757333654639"      # Join Channel button icon
E_GATE_CHECK = "5260463209562776385"      # Check Subscription button icon
# Welcome screen (SCREENS["welcome"]) only. The first two are CAPTION entities
# rendered through pe(); E_WELCOME_START is the Start BUTTON icon and rides on
# the 4th tuple element, never inside the label.
#
# NOTE: two of these repeat ids already used elsewhere - E_WELCOME_DOWN matches
# E_BACK / E_GATE_DOWN, and E_WELCOME_START matches E_NUDGE_ROCKET and
# E_MENU_SIGNAL. Declared separately on purpose: restyling the menu's "Get a
# signal" icon or the Back arrow must not silently change this screen.
E_WELCOME_BOT   = "5924946082487341386"   # the robot on the headline
E_WELCOME_DOWN  = "5305522282695768654"   # the finger pointing at Start
E_WELCOME_START = "5188481279963715781"   # Start button icon
# "Why traders choose Go+" screen (SCREENS["how"]) only. All seven are CAPTION
# entities rendered through pe(); this screen's BUTTON icon is E_QMARK above,
# which already carries the required id and is used nowhere else.
#
# NOTE: three repeat ids used elsewhere - E_HOW_CHART matches E_CHART,
# E_HOW_HOUR matches E_SIG_GLASS, and E_HOW_DOWN matches E_BACK /
# E_GATE_DOWN / E_WELCOME_DOWN. Declared separately on purpose, so restyling
# any of those cannot silently change this screen.
E_HOW_SPARK  = "5325547803936572038"      # sparkles on the headline
E_HOW_CHART  = "5231200819986047254"      # bar chart, assets line
E_HOW_GLOBE  = "5447410659077661506"      # globe, OTC line
E_HOW_TARGET = "5461009483314517035"      # target, trading modes line
E_HOW_BOLT   = "5992366958681527437"      # bolt, chart analysis line
E_HOW_HOUR   = "5386367538735104399"      # hourglass, availability line
E_HOW_DOWN   = "5305522282695768654"      # the finger pointing at the button
# "It is simple" screen (SCREENS["tech"]) only - the step list reached from the
# How Does It Work button. All nine are CAPTION entities rendered through pe();
# this screen's BUTTON icon is E_GEAR and is not touched here.
#
# NOTE: seven repeat ids used elsewhere - the five keycaps match E_N1..E_N5
# (also used by the mode, type and asset screens), E_TECH_GREEN matches
# E_GREEN, and E_TECH_ROBOT matches E_MENU_HEADER. Declared separately on
# purpose: restyling the numbered lists on another screen must not silently
# renumber this one.
E_TECH_CLIP  = "5352765106180610755"      # clipboard on the headline
E_TECH_N1    = "5778373820930858379"
E_TECH_N2    = "5778382698628256004"
E_TECH_N3    = "5778338052443213984"
E_TECH_N4    = "5778346006722646362"
E_TECH_N5    = "5778205144680239810"
E_TECH_GREEN = "5188234920639632382"      # the BUY circle
E_TECH_RED   = "5411225014148014586"      # the SELL circle
E_TECH_ROBOT = "5188678912883827293"      # robot on the closing line
# "The technology behind Go+" screen (SCREENS["ai"]) only. The first six are
# CAPTION entities rendered through pe(); E_AI_RESULTS is the BUTTON icon and
# rides on the 4th tuple element, never inside the label.
#
# NOTE: four repeat ids used elsewhere - E_AI_GEAR matches E_GEAR (also this
# screen's own previous caption emoji and the tech screen's button icon),
# E_AI_CHART matches E_CHART / E_HOW_CHART, E_AI_DOWN matches E_BACK and the
# gate/welcome/how screens, and E_AI_RESULTS matches E_MENU_LEVEL (the main
# menu's "My level" icon). Declared separately on purpose, so restyling any of
# those cannot silently change this screen.
E_AI_GEAR    = "5341715473882955310"      # gear on the headline
E_AI_CAT     = "5796185041717433060"      # cat, the AI line
E_AI_CHART   = "5231200819986047254"      # bar chart, indicators line
E_AI_LENS    = "5231012545799666522"      # magnifier, setups line
E_AI_BOLT    = "5274182275704039686"      # bolt, calculations line
E_AI_DOWN    = "5305522282695768654"      # the finger pointing at the button
E_AI_RESULTS = "5244837092042750681"      # See real results button icon
# Results / real feedback screen (SCREENS["results"]) only. The first four are
# CAPTION entities rendered through pe(); E_RES_LOCK is the first BUTTON's icon
# and rides on the 4th tuple element, never inside the label.
#
# NOTE: three repeat ids used elsewhere - E_RES_POINT matches E_POINT,
# E_RES_SOUND matches E_GATE_SOUND, and E_RES_LOCK matches E_GATE_LOCK (the
# gate headline's padlock). Declared separately on purpose, so restyling the
# gate cannot silently change this screen.
E_RES_UP    = "5370740716840425754"       # index finger, screenshots line
E_RES_SHAKE = "5451876269719308814"       # handshake, channel line
E_RES_POINT = "5415758949129404605"       # pointing finger, handle line
E_RES_SOUND = "5247187233722607160"       # speaker before the handle
E_RES_LOCK  = "5296369303661067030"       # Get access button icon
# Activation screen (SCREENS["access"]) only - all CAPTION entities rendered
# through pe(). This screen's button icon is inline in its tuple and is not
# touched here.
#
# NOTE: three repeat ids used elsewhere - E_ACC_MONEY matches E_MONEY,
# E_ACC_ROBOT matches E_MENU_HEADER / E_TECH_ROBOT, and E_ACC_DOWN matches
# E_BACK and the gate/welcome/how/ai screens. Declared separately on purpose.
# E_ACC_UP is NOT the same id as E_RES_UP on the results screen - different
# stickers for the same-looking glyph, so do not merge them.
E_ACC_SIREN = "5395695537687123235"       # siren on the headline
E_ACC_MONEY = "5224257782013769471"       # money bag after the headline
E_ACC_ROBOT = "5188678912883827293"       # robot before "Go Plus"
E_ACC_CHECK = "5206607081334906820"       # check mark ending the signal line
E_ACC_SPOCK = "5364297939478921851"       # raised hand, dream car line
E_ACC_OK    = "5364237234411160303"       # ok hand, dream car line
E_ACC_WATCH = "5240379491515126100"       # watch, dream watch line
E_ACC_HOUSE = "5416041192905265756"       # house, dream life line
E_ACC_UP    = "5019759554234156094"       # index finger, "All one click closer"
E_ACC_BOLT  = "5303488362278050480"       # bolt, "Stop watching others win"
E_ACC_DOWN  = "5305522282695768654"       # the fingers around the closing line
# Registration screen (SCREENS["register"]) only. The first six are CAPTION
# entities rendered through pe(); the last three are BUTTON icons and ride on
# the 4th tuple element, never inside the label.
#
# NOTE: E_REG_WARN and E_REG_DOWNARR carry the SAME id, as supplied. One
# sticker cannot render as both a warning triangle and a down arrow, so the
# two positions will show the same glyph - see the report for this change.
# That id is also E_EXP_DOWN (the expiration screen's down arrow).
# E_REG_DOWN repeats the id used by MSG_WRONG_LINK / MSG_UID_ERROR, and
# E_REG_BTN_SUP repeats E_MENU_SUPPORT. Declared separately on purpose.
E_REG_LOCK    = "5350619413533958825"     # padlock opening the caption
E_REG_LINK    = "5271604874419647061"     # link before the URL
E_REG_ARROW   = "5435955998479102657"     # arrow on the "once you register" line
E_REG_DOWN    = "5447644880824181073"     # finger closing that line
E_REG_WARN    = "5406745015365943482"     # warning on the "please note" line
E_REG_DOWNARR = "5406745015365943482"     # down arrow closing that line
E_REG_BTN_REG = "5836690092306992715"     # Register & Get Access button icon
E_REG_BTN_HOW = "5222444124698853913"     # How to Register button icon
E_REG_BTN_SUP = "5443038326535759644"     # Support button icon
# "Almost there" deposit verdict (MSG_NEED_DEPOSIT) ONLY.
#
# _register_btn() in bot.py is shared: the wrong-link verdict renders the same
# button. This id is passed as a per-screen override at that one call site, so
# MSG_WRONG_LINK keeps the icon and label it already had. Do not fold this back
# into the helper's default or both verdicts move together.
E_NEED_DEP_REG = "5836690092306992715"

# Main-menu button icons. Unlike the constants above these are NOT rendered
# through pe(): they go in the 4th slot of a button tuple, which build_kb passes
# as InlineKeyboardButton.icon_custom_emoji_id - the emoji Telegram draws before
# the button's label.
#
# TWO Bot API limits shape how these are used, and neither is ours to change:
#   * icon_custom_emoji_id is a single string. ONE custom emoji per button.
#   * InlineKeyboardButton.text is a plain label with no entities and no
#     parse_mode, so a custom emoji cannot be placed inside the text itself -
#     <tg-emoji> would render as literal markup. pe() is for message bodies only.
# Between them, a button can carry exactly one custom emoji, always leading.
E_MENU_SIGNAL   = "5188481279963715781"
E_MENU_LEVEL    = "5244837092042750681"
E_MENU_SUPPORT  = "5443038326535759644"
E_MENU_PREMIUM  = "5431684550424011313"
E_MENU_CHANNEL  = "5231489647946768652"
E_YOUTUBE       = "5897969921182142023"

def pe(emoji_id, fallback):
    return '<tg-emoji emoji-id="' + emoji_id + '">' + fallback + '</tg-emoji>'

T_INFO  = pe(E_INFO, "\u2139\uFE0F")
T_POINT = pe(E_POINT, "\U0001F449")
T_MONEY = pe(E_MONEY, "\U0001F4B0")
T_STAR  = pe(E_STAR, "\u2728")
T_GEM   = pe(E_GEM, "\U0001F48E")
T_ROBOT = pe(E_ROBOT, "\U0001F916")
T_GREEN = pe(E_GREEN, "\U0001F7E2")
T_DOWN  = pe(E_DOWN, "\U0001F53D")
T_SHAKE = pe(E_SHAKE, "\U0001F91D")
T_GEAR  = pe(E_GEAR, "\u2699\uFE0F")
T_N1    = pe(E_N1, "1\uFE0F\u20E3")
T_N2    = pe(E_N2, "2\uFE0F\u20E3")
T_N3    = pe(E_N3, "3\uFE0F\u20E3")
T_N4    = pe(E_N4, "4\uFE0F\u20E3")
T_N5    = pe(E_N5, "5\uFE0F\u20E3")
T_CLOCK = pe(E_CLOCK, "\u23F0")
T_CHART = pe(E_CHART, "\U0001F4CA")
T_LENS  = pe(E_LENS, "\U0001F50D")
T_BOLT  = pe(E_BOLT, "\u26A1")
T_SIG_CHART = pe(E_SIG_CHART, "\U0001F4CA")
T_SIG_LENS  = pe(E_SIG_LENS, "\U0001F50D")
# Fallback glyph is the spiral the reference shows. The entity ID is unchanged -
# it is still the one supplied for this slot - so premium clients see exactly
# what they saw before; only the non-premium fallback character moved.
T_SIG_GLASS = pe(E_SIG_GLASS, "\U0001F300")

# Main-menu caption and trading-mode screen. The fallback glyph inside each tag
# is what a client that cannot show custom emoji displays instead, so it is the
# emoji the entity depicts, not a stand-in.
T_MENU_HEADER   = pe(E_MENU_HEADER, "\U0001F916")      # robot
T_MENU_SIGNALS  = pe(E_MENU_SIGNALS, "\U000026A1")     # high voltage
T_MENU_LEVEL_HD = pe(E_MENU_LEVEL_HD, "\U0001FAAB")    # low battery
T_LEVEL_START   = pe(E_LEVEL_START, "\U00002B50")      # star
# E_CHART is reused deliberately: the supplied "Trading mode" id is byte for
# byte the one already defined for it, so this is the same entity, not a new one.
T_MODE_HEADER   = pe(E_CHART, "\U0001F4CA")            # bar chart
# The glyph inside a <tg-emoji> tag MUST be a single valid emoji: Telegram
# rejects the whole message with "Bad Request: ENTITY_TEXT_INVALID" otherwise,
# and an em dash is not an emoji. These two therefore carry the emoji their
# sticker actually depicts (confirmed via getCustomEmojiStickers): 👤 and 🤩.
T_MODE_MANUAL   = pe(E_MODE_MANUAL, "\U0001F464")      # bust in silhouette
T_MODE_AUTO     = pe(E_MODE_AUTO, "\U0001F929")        # star-struck

# --- Currency pairs ---------------------------------------------------------
# Single source of truth for the OTC pair list: the keyboard, the pagination,
# the callback codes and the label shown on the signal screen are all derived
# from PAIRS below. Appending here is the only edit needed to add a pair.
# Order is preserved (the first six are the original ones, so page 1 looks
# exactly as it did) and duplicates are dropped by _dedupe_pairs.
PAIRS_RAW = [
    "AUD/CAD OTC", "AUD/CHF OTC", "AUD/NZD OTC", "AUD/USD OTC",
    "CAD/CHF OTC", "CHF/JPY OTC", "EUR/GBP OTC", "EUR/HUF OTC",
    "EUR/NZD OTC", "EUR/TRY OTC", "EUR/USD OTC", "GBP/AUD OTC",
    "GBP/JPY OTC", "JOD/CNY OTC", "MAD/USD OTC", "NZD/JPY OTC",
    "TND/USD OTC", "USD/ARS OTC", "USD/BRL OTC", "USD/CAD OTC",
    "USD/CNH OTC", "USD/DZD OTC", "USD/INR OTC", "USD/MXN OTC",
    "USD/SGD OTC", "USD/VND OTC", "EUR/JPY OTC", "USD/BDT OTC",
    "USD/PKR OTC", "BHD/CNY OTC", "ZAR/USD OTC", "USD/COP OTC",
    "USD/THB OTC", "USD/IDR OTC", "CAD/JPY OTC", "OMR/CNY OTC",
    "CHF/NOK OTC", "NZD/USD OTC", "USD/PHP OTC", "AED/CNY OTC",
    "QAR/CNY OTC", "USD/JPY OTC", "EUR/RUB OTC", "NGN/USD OTC",
    "AUD/JPY OTC", "USD/CLP OTC", "USD/CHF OTC", "UAH/USD OTC",
    "GBP/USD OTC", "USD/EGP OTC", "YER/USD OTC", "SAR/CNY OTC",
    "USD/MYR OTC", "LBP/USD OTC", "USD/RUB OTC", "EUR/CHF OTC",
    "KES/USD OTC",
]

def pair_code(label):
    # "AUD/CAD OTC" -> "audcad". callback_data is capped at 64 bytes and has to
    # stay stable across restarts, so it is derived from the pair itself rather
    # than from a list index (which would shift when a pair is inserted).
    base = str(label).upper().replace("OTC", "")
    return "".join(ch for ch in base if ch.isalnum()).lower()

def _dedupe_pairs(labels):
    # Keyed on the code, so "eur/usd otc" and "EUR/USD  OTC" collapse into one
    # button instead of two that behave identically.
    seen, out = set(), []
    for raw in labels:
        label = " ".join(str(raw).split())
        code = pair_code(label)
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(label)
    return out

PAIRS = _dedupe_pairs(PAIRS_RAW)
# code -> label, for naming the pair on the signal screen after a tap.
PAIR_CODES = {pair_code(p): p for p in PAIRS}

# Grid is unchanged from the original screen: 2 buttons per row, 3 rows of
# pairs, then the page indicator row and Back.
PAIRS_PER_ROW  = 2
PAIR_ROWS      = 3
PAIRS_PER_PAGE = PAIRS_PER_ROW * PAIR_ROWS
# Recalculated from PAIRS on import - never hardcode the page count again.
PAIR_PAGES = max(1, -(-len(PAIRS) // PAIRS_PER_PAGE))

def pairs_kb(page=0):
    # Keyboard for one page. The page number wraps in both directions, so an
    # out-of-range value from a stale button can't produce an empty screen.
    page = page % PAIR_PAGES
    chunk = PAIRS[page * PAIRS_PER_PAGE:(page + 1) * PAIRS_PER_PAGE]
    rows = [[(label, "cb:pair:" + pair_code(label))
             for label in chunk[i:i + PAIRS_PER_ROW]]
            for i in range(0, len(chunk), PAIRS_PER_ROW)]
    # Same two-button pagination row as before - the indicator stays inert and
    # "\u203A" now advances, wrapping past the last page back to the first, so
    # every pair is reachable without adding a button to the layout.
    rows.append([("%d/%d" % (page + 1, PAIR_PAGES), "cb:noop"),
                 ("\U0000203A", "cb:pairpage:%d" % ((page + 1) % PAIR_PAGES))])
    rows.append([("\U000000AB Back", "cb:type:otc")])
    return rows

SCREENS = {
    "gate": {
        "photo": "gate",
        # The handle is CHANNEL_MENTION, derived from CHANNEL_URL, so the text
        # a user reads and the channel the Join button opens are the same one.
        # No markup on it: Telegram auto-links a bare @handle as a mention.
        #
        # Button labels are bare words. Their emoji come from the 4th tuple
        # element, which build_kb passes as icon_custom_emoji_id - Telegram
        # draws that before the label, so a unicode emoji in the text too would
        # render a second glyph beside it. Styles, URL, callback and order are
        # unchanged from the previous version of this screen.
        "text": (pe(E_GATE_LOCK, "\U0001F512") + "ONE STEP TO UNLOCK GO+\n\n"
                 "Join our free trading channel to activate the bot:\n\n"
                 + pe(E_GATE_MEGA, "\U0001F4E3") + pe(E_GATE_SOUND, "\U0001F50A")
                 + CHANNEL_MENTION + "\n\n"
                 "Then tap Check Subscription below "
                 + pe(E_GATE_DOWN, "\U0001F447")),
        "kb": [[("Join Channel", "url:" + CHANNEL_URL, "primary", E_GATE_JOIN)],
               [("Check Subscription", "cb:check_sub", "success", E_GATE_CHECK)]],
    },
    "welcome": {
        "photo": "welcome",
        # Button label is the bare word "Start". Its rocket comes from the 4th
        # tuple element, which build_kb passes as icon_custom_emoji_id and
        # Telegram draws before the label - a unicode rocket in the text too
        # would render a second glyph beside it. Callback, style and order are
        # unchanged from the previous version of this screen.
        "text": (pe(E_WELCOME_BOT, "\U0001F916") + " Welcome to Go+\n\n"
                 "Your personal trading assistant \U00002014 clear, data-driven "
                 "signals without the complex analysis.\n\n"
                 + pe(E_WELCOME_DOWN, "\U0001F447") + " Tap Start to begin"),
        "kb": [[("Start", "cb:go:how", "success", E_WELCOME_START)]],
    },
    "how": {
        "photo": "how",
        # Spacing is deliberate and not a typo: the headline, the assets line
        # and the closing line put a space after their emoji, the four middle
        # feature lines do not. Keep it that way - it is what the supplied copy
        # specifies, and pe() emits the entity with no padding of its own.
        #
        # The button is unchanged: label already plain "How Does It Work" and
        # E_QMARK already carries the required id, so nothing here restyles it.
        "text": (pe(E_HOW_SPARK, "\U00002728") + " WHY TRADERS CHOOSE GO+\n\n"
                 + pe(E_HOW_CHART, "\U0001F4CA") + " 100+ trading assets\n"
                 + pe(E_HOW_GLOBE, "\U0001F310") + "OTC and exchange pairs\n"
                 + pe(E_HOW_TARGET, "\U0001F3AF") + "2 trading modes\n"
                 + pe(E_HOW_BOLT, "\U000026A1") + "Instant chart analysis\n"
                 + pe(E_HOW_HOUR, "\U0000231B") + "Available 24/7, any device\n\n"
                 + pe(E_HOW_DOWN, "\U0001F447") + " See how it works"),
        "kb": [[("How Does It Work", "cb:go:tech", "primary", E_QMARK)]],
    },
    "tech": {
        "photo": "tech",
        # Spacing is deliberate and not a typo: the clipboard and the five
        # keycaps butt straight against their text, and on the signal line the
        # space sits BEFORE the green circle only - "BUY 🟢or SELL🔴". Keep it
        # that way; pe() adds no padding of its own. The button is untouched.
        "text": (pe(E_TECH_CLIP, "\U0001F4CB") + "IT IS SIMPLE\n\n"
                 + pe(E_TECH_N1, "1️⃣") + "Select an asset\n"
                 + pe(E_TECH_N2, "2️⃣") + "Choose the expiration time\n"
                 + pe(E_TECH_N3, "3️⃣") + "Get a signal \U00002014 BUY "
                 + pe(E_TECH_GREEN, "\U0001F7E2") + "or SELL"
                 + pe(E_TECH_RED, "\U0001F534") + "\n"
                 + pe(E_TECH_N4, "4️⃣") + "Open a trade\n"
                 + pe(E_TECH_N5, "5️⃣") + "Track the result\n\n"
                 + pe(E_TECH_ROBOT, "\U0001F916")
                 + " I handle the market analysis \U00002014 you decide when to act."),
        "kb": [[("See the technology", "cb:go:ai", "primary", E_GEAR)]],
    },
    "ai": {
        "photo": "ai",
        # Spacing is deliberate: every emoji here takes one space after it
        # EXCEPT the bar chart, which butts straight against "Hundreds".
        #
        # The button label stays the bare words "See real results"; its chart
        # comes from the 4th tuple element, which build_kb passes as
        # icon_custom_emoji_id. Callback, style and position are unchanged -
        # only the icon is new, since this button carried none before.
        "text": (pe(E_AI_GEAR, "⚙️") + " THE TECHNOLOGY BEHIND GO+\n\n"
                 + pe(E_AI_CAT, "\U0001F63A")
                 + " AI that processes market data in seconds\n"
                 + pe(E_AI_CHART, "\U0001F4CA")
                 + "Hundreds of indicators and price patterns\n"
                 + pe(E_AI_LENS, "\U0001F50D")
                 + " Spots setups that are easy to miss\n"
                 + pe(E_AI_BOLT, "\U000026A1")
                 + " Every signal comes from calculations, not guesswork\n\n"
                 + pe(E_AI_DOWN, "\U0001F447") + " See it in action"),
        "kb": [[("See real results", "cb:results", "primary", E_AI_RESULTS)]],
    },
    "results": {
        "photo": "welcome",
        # All four emoji now go through pe(); the two that were plain unicode
        # literals before are entities like the rest. The handle is
        # CHANNEL_MENTION, derived from CHANNEL_URL, so the text and the second
        # button point at the same channel. It is a bare @mention, which
        # Telegram auto-links - and this screen is a PHOTO caption, which has no
        # link preview either way, so nothing is lost by dropping the raw URL.
        #
        # First button label is the bare words "Get access to Go +"; its padlock
        # comes from the 4th tuple element. Callback and style are unchanged.
        # The second button is untouched.
        "text": ("<b>Real feedback from active Go+ traders.</b>\n\n"
                 + pe(E_RES_UP, "\u261d\ufe0f")
                 + " The screenshots above are just a tiny fraction of the results.\n\n"
                 + pe(E_RES_SHAKE, "\U0001F91D")
                 + " More feedback is published on our trading channel:\n\n"
                 + pe(E_RES_POINT, "\U0001F449") + " "
                 + pe(E_RES_SOUND, "\U0001F50A") + CHANNEL_MENTION),
        "kb": [[("Get access to Go +", "cb:go:access", "success", E_RES_LOCK)],
               [("Open Telegram channel", "url:" + CHANNEL_URL, "primary", "5220069871072583573")]],
    },
    "access": {
        "video": "access",
        # Spacing is deliberate and uneven - see the per-emoji notes below.
        # Only two spans are bold: the headline figure and "Go Plus". Every
        # emoji goes through pe(), so none of them is a bare unicode literal.
        "text": (pe(E_ACC_SIREN, "\U0001F6A8")
                 + " <b>+$18,400 \U00002014 In One Trade.</b> "
                 + pe(E_ACC_MONEY, "\U0001F4B0") + "\n"
                 "No charts. No courses. No stress.\n\n"
                 # robot butts straight against the bold name, no space
                 + pe(E_ACC_ROBOT, "\U0001F916") + "<b>Go Plus</b>"
                 " sends the signal \U00002192 you tap \U00002192 you profit. "
                 + pe(E_ACC_CHECK, "✔️") + "\n\n"
                 # the two hands sit together, then one space
                 + pe(E_ACC_SPOCK, "\U0001F596") + pe(E_ACC_OK, "\U0001F44C")
                 + " Dream car.\n"
                 + pe(E_ACC_WATCH, "⌚️") + " Dream watch.\n"
                 + pe(E_ACC_HOUSE, "\U0001F3E0") + " Dream life.\n\n"
                 # no space after the index finger or the bolt
                 + pe(E_ACC_UP, "☝") + "All one click closer.\n\n"
                 + pe(E_ACC_BOLT, "⚡️") + "Stop watching others win.\n\n"
                 + pe(E_ACC_DOWN, "\U0001F447") + " Activate the bot now "
                 + pe(E_ACC_DOWN, "\U0001F447")),
        # Interim: routes into the UID-capture register flow (Group C adds full verification)
        #
        # "Get Bot Access" carries the SAME callback the old "Activate Bot"
        # button did - cb:go:register, matched by nav() in bot.py - so the
        # activation path, the Reg.waiting_uid arming and the register nudge are
        # reached exactly as before. Renaming the label could not change where
        # it goes: the destination is the callback string, and that is untouched.
        # Its icon is the same custom emoji id the old button used.
        #
        # The five added buttons all reuse destinations that already existed:
        #   Quick Setup Guide - the "How to Register" video, the same URL the
        #                       register screen's own button opens
        #   Review            - the public feedback channel. This one button is
        #                       a URL rather than the cb:results reviews album;
        #                       that handler is untouched and still reached from
        #                       the ai screen, so nothing was orphaned.
        #   Support           - SUPPORT_URL, as on the register and menu screens
        #   YouTube           - YOUTUBE_URL, as on the menu screen
        #   Forex Tips        - FOREX_TIPS_URL, the one destination this project
        #                       did not already have (see the constant above)
        #
        # Styles are Telegram's three: success (green), primary (blue), danger
        # (red). Review and Support carry their emoji in the LABEL rather than
        # as a custom emoji id, because this project has no id for a star or a
        # lightbulb and inventing one would render nothing at all.
        "kb": [[("Get Bot Access", "cb:go:register", "success", "6280525956771745921")],
               [("Quick Setup Guide", "url:https://youtu.be/uJHBwXZVnNI?si=bhC7oMFLvoJfiQy", "primary", E_REG_BTN_HOW)],
               [("⭐ Review", "url:https://t.me/Goplusfeedback", "danger"),
                ("Support", "url:" + SUPPORT_URL, "danger", E_MENU_SUPPORT)],
               [("YouTube", "url:" + YOUTUBE_URL, "primary", E_YOUTUBE),
                ("Channel", "url:https://t.me/apexxtraderz", "primary")]],
    },
    "register": {
        "photo": "register",
        # Spacing is deliberate: the padlock, arrow and warning butt straight
        # against their text, while the link emoji and the two closing arrows
        # take one space. The URL stays a bare link so it auto-links exactly as
        # before - this is a photo caption, which has no link preview either
        # way, so nothing is gained or lost by the bare form.
        #
        # Button labels are bare text; every icon rides on the 4th tuple
        # element. Button 3 passes None for style on purpose: it had no style
        # before, and build_kb skips a falsy one, so the payload keeps style
        # absent while still carrying an icon.
        "text": (pe(E_REG_LOCK, "\U0001F510")
                 + "To access Go+, register for a new Pocket Option account "
                 "using my link:\n\n"
                 + pe(E_REG_LINK, "\U0001F517") + " {ref}\n\n"
                 + pe(E_REG_ARROW, "➡️")
                 + "Once you register, send your new account ID in the text "
                 "box below " + pe(E_REG_DOWN, "\U0001F447") + "\n\n"
                 + pe(E_REG_WARN, "⚠️")
                 + "Please note: Your ID must contain numbers only "
                 "\U00002014 no extra symbols " + pe(E_REG_DOWNARR, "⬇️") + "\n\n"
                 "Example: 123456789"),
        # {ref} in the CAPTION is filled per user by _show_register in bot.py,
        # which is why show() routes this screen there the way it routes the
        # menu. The BUTTON deliberately stores the plain REF_LINK instead of a
        # placeholder: build_kb DROPS a button whose URL fails _URL_OK, so an
        # unformatted "{ref}" here would silently delete the single most
        # important button in the funnel. Storing the real link means the worst
        # case is an untracked click, not a missing button - _show_register
        # swaps in the sub-ID version on the way out.
        "kb": [[("Register & Get Access", "url:" + REF_LINK, "success", E_REG_BTN_REG)],
               [("How to Register", "url:https://youtu.be/uJHBwXZVnNI?si=bhC7oMFLvoJfiQy", "primary", E_REG_BTN_HOW)],
               [("Support", "url:" + SUPPORT_URL, None, E_REG_BTN_SUP)]],
    },
    # Post-verification home screen (shown once a UID passes the campaign +
    # deposit check). The signal counters are a format template - {limit},
    # {used} and {left} are filled per user by _show_menu in bot.py, which is
    # also why show() routes "menu" through it rather than rendering this text
    # directly (raw braces would leak onto the screen).
    # {level} is filled the same way, from the user's is_premium column, so the
    # level on this screen is per-user state rather than static text.
    "menu": {
        "photo": "menu",
        # Wording, line breaks and the three {placeholders} are unchanged; only
        # the three leading emoji became <tg-emoji> entities.
        "text": (T_MENU_HEADER + " <b>Go+ main menu</b>\n\n"
                 + T_MENU_SIGNALS + " <b>Signals</b>\n"
                 "\U00002014 Available today: {limit} signals\n"
                 "\U00002014 Used: {used}\n"
                 "\U00002014 Left: {left}\n\n"
                 + T_MENU_LEVEL_HD + " <b>Your level:</b> {level}"),
        # Every row carries style "primary", which the Bot API renders blue, so
        # the whole menu reads as one blue block instead of the mixed green /
        # blue / app-default it was. The only other styles Telegram accepts are
        # "success" (green) and "danger" (red); omitting it falls back to the
        # client's own default, which is what the unstyled rows used to do.
        # VIP_LINK and REF_LINK are still read above - REF_LINK is also used by
        # _register_btn() in bot.py, so it must not be removed with the button.
        # The leading unicode emoji are gone from the labels: each button now
        # carries its custom emoji in the 4th slot, and Telegram draws that
        # before the text, so keeping both would show two icons side by side.
        # That applies to "Unlock Premium" too - its single crown is the custom
        # emoji, which is why the label is bare text like all the others.
        # Every row carries style "primary", which the Bot API renders blue, so
        # the whole menu reads as one blue block. Telegram accepts exactly three
        # styles - "success" (green), "primary" (blue), "danger" (red) - and
        # omitting it falls back to the client's own default. Arbitrary colours
        # are not expressible here, so this is the full range available.
        "kb": [[("Get a signal", "cb:menu:signal", "primary", E_MENU_SIGNAL)],
               [("Unlock Premium", "cb:menu:premium", "primary", E_MENU_PREMIUM)],
               [("My level", "cb:menu:level", "primary", E_MENU_LEVEL)],
               [("Support", "url:" + SUPPORT_URL, "primary", E_MENU_SUPPORT)],
               [("Telegram channel", "url:" + CHANNEL_URL, "primary", E_MENU_CHANNEL)],
               [("YouTube channel", "url:" + YOUTUBE_URL, "primary", E_YOUTUBE)]],
    },
    # Trading-mode picker, opened from "Get a signal" on the menu. Both modes are
    # still placeholders - see mode_action in bot.py.
    # NOTE: the speech emoji is plain unicode, not pe() - we have no verified
    # custom emoji ID for it, and an invalid ID makes Telegram reject the whole
    # message. Swap in pe(E_SPEECH, ...) once a real ID is on hand.
    "mode": {
        "photo": "trading_mode",
        # T_N1 / T_N2 / T_DOWN already carried the supplied number, Automatic
        # and "Choose below" ids, so they are untouched. The header emoji is the
        # supplied Trading-mode entity (was a plain unicode speech balloon), and
        # the Manual / Automatic entities sit where the em dash used to, each
        # wrapping the emoji its own sticker depicts rather than the dash - a
        # dash there is what produced ENTITY_TEXT_INVALID and blanked the chat.
        "text": (T_MODE_HEADER + " <b>Select trading mode:</b>\n\n"
                 + T_N1 + " <b>Manual</b> " + T_MODE_MANUAL + " you choose asset &amp; time\n"
                 + T_N2 + " <b>Automatic</b> " + T_MODE_AUTO + " bot chooses everything\n\n"
                 + T_DOWN + " <b>Choose below</b>"),
        "kb": [[("Manual", "cb:mode:manual", "success", E_MODE_MANUAL)],
               [("Automatic", "cb:mode:auto", "primary", E_MODE_AUTO)],
               [("\U000000AB Back", "cb:go:menu")]],
    },
    # Market-type picker, opened from "Manual" on the mode screen. Both types are
    # still placeholders - see type_action in bot.py. Speech emoji is plain
    # unicode for the same reason as the mode screen above.
    "type": {
        "photo": "trading_type",
        "text": "\U0001F4AC <b>Select market type:</b>\n\n" + T_N1 + " <b>OTC</b> \U00002014 available even on weekends\n" + T_N2 + " <b>FIN</b> \U00002014 real exchange market prices\n\n" + T_DOWN + " <b>Choose below</b>",
        "kb": [[("\U0001F539 OTC", "cb:type:otc", "success")],
               [("\U0001F512 FIN", "cb:type:fin", "primary")],
               [("\U000000AB Back", "cb:menu:signal")]],
    },
    # Asset-category picker, opened from "OTC" on the market-type screen. All five
    # categories are still placeholders - see asset_action in bot.py. Speech emoji
    # is plain unicode for the same reason as the mode screen above.
    # TODO: assets/asset.jpg does not exist yet - until it is added, render()
    # falls back to sending this screen as text with its keyboard intact.
    "asset": {
        "photo": "asset_category",
        "text": "\U0001F4AC <b>Select asset category:</b>\n\n" + T_N1 + " <b>Currency pairs</b> \U00002014 classic Forex\n" + T_N2 + " <b>Cryptocurrencies</b> \U00002014 high volatility\n" + T_N3 + " <b>Stocks</b> \U00002014 global companies\n" + T_N4 + " <b>Indices</b> \U00002014 market trends\n" + T_N5 + " <b>Commodities</b> \U00002014 gold, oil &amp; more\n\n" + T_DOWN + " <b>Choose below</b>",
        "kb": [[("\U0001F504 Currency pairs", "cb:asset:forex", "success")],
               [("\U0001F512 Cryptocurrencies", "cb:asset:crypto")],
               [("\U0001F512 Stocks", "cb:asset:stocks")],
               [("\U0001F512 Indices", "cb:asset:indices")],
               [("\U0001F512 Commodities", "cb:asset:commodities")],
               [("\U000000AB Back", "cb:mode:manual")]],
    },
    # Currency-pair picker, opened from "Currency pairs" on the asset screen.
    # Shuffle emoji is plain unicode - no verified custom emoji ID for it, same as
    # the speech emoji above.
    # The keyboard is page 1 only - what show() renders for a plain "pairs"
    # screen. Paging goes through show_pairs() in bot.py, which calls the same
    # pairs_kb() builder, so the two can't drift apart.
    "pairs": {
        "photo": "currency_pair",
        "text": "\U0001F500 <b>Select a currency pair:</b>\n\n" + T_DOWN + " <b>Choose below</b>",
        "kb": pairs_kb(0),
    },
    # The expiration screen, shown after a currency pair is picked. S buttons are
    # locked; every M button starts a signal - see s_action / m_action in bot.py.
    # All emoji here are plain unicode by request, no pe().
    # TODO: S35 and S40 are intentionally absent - the rows jump 30 -> 45.
    # There is no assets/test_menu.jpg and there never was: "test_menu" is the
    # screen key, and the image it renders is assets/expiration_time.jpg. This is
    # the only screen that shows it - tapping an M button here leads straight to
    # the waiting screen, which is text-only.
    "test_menu": {
        "photo": "expiration_time",
        # TODO: "Recommended: S5" is fixed copy - nothing computes it. Note that
        # every S button is locked by s_action, so S5 cannot actually be picked;
        # switch this to an M value or unlock S5 before it reads as truthful.
        "text": (pe(E_EXP_CLOCK, "\U000023F1") + " <b>Choose the expiration time:</b>\n\n"
                 + pe(E_EXP_BULB, "\U0001F4A1") + " <b>Recommended:</b> S5\n\n"
                 + pe(E_EXP_DOWN, "\U00002B07\U0000FE0F") + " Choose below"),
        "kb": [[("\U0001F512 S5", "cb:s:5")],
               [("\U0001F512 S10", "cb:s:10"), ("\U0001F512 S15", "cb:s:15")],
               [("\U0001F512 S20", "cb:s:20"), ("\U0001F512 S25", "cb:s:25"), ("\U0001F512 S30", "cb:s:30")],
               [("\U0001F512 S45", "cb:s:45"), ("\U0001F512 S50", "cb:s:50"), ("\U0001F512 S55", "cb:s:55")],
               [("\U0001F525 M1", "cb:m:1")],
               [("M2", "cb:m:2"), ("M3", "cb:m:3")],
               [("M4", "cb:m:4"), ("M5", "cb:m:5"), ("M6", "cb:m:6")],
               [("M7", "cb:m:7"), ("M8", "cb:m:8"), ("M9", "cb:m:9")],
               [("\U00002705 M10", "cb:m:10")]],
    },
    # TODO(Group C): replace stub with the real step-by-step registration guide
    "howto": {
        "photo": "howto",
        "text": "Step-by-step registration guide coming here.",
        "kb": [[("Back to registration", "cb:go:register", "primary")]],
    },
}

REVIEWS = ["reviews1", "reviews2", "reviews3", "reviews4", "reviews5"]

# --- Signal flow: an unlocked M button on the test menu tears down the tapped
# screen and puts up the waiting screen - two text messages, in this order
# (see _send_wait_screen in bot.py):
#   1. SIGNAL_CHART, the chart emoji alone in its own text message
#   2. SIGNAL_ANALYZING, the two-line analysis notice
# The analysis stage sends NO media at all - no photo, no album, no video. There
# is deliberately no waiting-image constant here to point one at.
# The chart is its own message rather than a caption, which is what puts it on
# its own line at full custom-emoji size - and a caption would require the photo
# this stage must not send.
# All three emoji are premium custom emoji, so these only render correctly with
# parse_mode="HTML" - <tg-emoji> is dropped otherwise, leaving the plain-unicode
# fallback that is written inside each tag. That fallback is what clients which
# cannot show custom emoji display; it is not a replacement for the entity.
# The result screen further down is still plain unicode.
# The one delay for every signal, in seconds, counted from the user's final
# selection. Deliberately independent of the chosen expiration: M1 and M10 both
# land in 30s, and the tapped M value survives only as the {expiry} label on the
# result screen below.
SIGNAL_COUNTDOWN = 30

# Message 1: the chart, alone. No text, no caption, no keyboard, no photo.
SIGNAL_CHART = T_SIG_CHART

# Message 2. Written once and never edited - there is no live timer, so {wait}
# is the full delay ("00:30" from _wait_label in bot.py), not a ticking value.
SIGNAL_ANALYZING = (T_SIG_LENS + " <b>I'm analyzing the chart. It won't take long.</b>\n\n"
                    + T_SIG_GLASS + " Please wait {wait} — the signal is almost there.")

# TODO: the direction is picked at random per signal (see _run_signal in bot.py)
# until the real signal engine lands - it is not derived from any market data.
SIGNAL_RESULT = ("\U00002705 The analysis is complete!\n\n"
                 "\U0001F4B1 Currency pair: {pair}\n"
                 "\U000023F1\U0000FE0F Expiration time: {expiry}\n"
                 "\U0001F514 Signal: {direction}")

# One of these is chosen uniformly at random for every signal. BUY pairs with UP
# and SELL pairs with DOWN on purpose: drawing the two halves independently
# would produce contradictory signals like "SELL ... UP".
# Each direction carries the artwork its result screen shows, so a direction can
# never be drawn without its matching image: assets/buy.jpg is the green BUY
# board, assets/sell.jpg the red SELL one.
SIGNAL_DIRECTIONS = (("BUY \U0001F7E2\U0001F7E2 UP \U00002B06\U0000FE0F", "buy"),
                     ("SELL \U0001F534\U0001F534 DOWN \U00002B07\U0000FE0F", "sell"))

SIGNAL_KB = [[("\U0001F680 New Signal", "cb:new_signal", "success")]]

# --- Levels and the daily signal quota --------------------------------------
# Per user, per day. Stored in users.signals_used_today / users.last_reset_date
# (see db.py), so a restart does not hand anyone a fresh allowance.
#
# Both tier limits come from Railway. The numbers below are only what applies
# when the variable is unset - no tier size is hardcoded in the limit logic:
# bot.py never names a number, it asks daily_limit(), and daily_limit() reads
# the two module globals below. Changing PREMIUM_DAILY_SIGNALS in Railway and
# restarting is therefore the whole procedure for changing the Premium cap.

def _int_env(name, default):
    # A typo'd variable falls back instead of taking the service down on boot -
    # an unparseable limit must not cost every user their signals. Negative
    # values are rejected for the same reason (they would read as "0 left").
    raw = (os.getenv(name) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default

LEVEL_START   = "start"
LEVEL_PREMIUM = "premium"

# START_DAILY_SIGNALS falls back to the legacy DAILY_SIGNAL_LIMIT before the
# built-in 30, so a service that still sets the old name keeps its value and
# nothing regresses at deploy time. The old variable can be deleted afterwards.
START_DAILY_SIGNALS   = _int_env("START_DAILY_SIGNALS", _int_env("DAILY_SIGNAL_LIMIT", 30))
PREMIUM_DAILY_SIGNALS = _int_env("PREMIUM_DAILY_SIGNALS", 70)

# Backward-compatible alias: anything still reading the old constant gets the
# Start limit, which is what it meant before Premium existed.
DAILY_SIGNAL_LIMIT = START_DAILY_SIGNALS

# Price of the Premium unlock, in the bot's own fictional tokens. Read from
# Railway like the two limits above, and passed to db.unlock_premium as the
# amount to deduct, so the number quoted on the locked screen and the number
# actually charged are always the same value. Not money: see the game_tokens
# comment in db.py.
PREMIUM_UNLOCK_COST = _int_env("PREMIUM_UNLOCK_COST", 100)

# Icon and name are kept apart because the two screens compose them
# differently: the menu caption wants "<star> Start" as one label, while the My
# level screen leads with the icon and ends with the name. The LEVEL_LABELS
# tables are derived rather than written out again, so a rename cannot leave the
# two screens disagreeing about what a tier is called.
#
# There are TWO icon tables on purpose, and they are not interchangeable.
#
# LEVEL_ICONS holds plain unicode. LEVEL_ICONS_TG wraps the same glyph in a
# <tg-emoji> entity where an ID has been supplied for that tier. An entity only
# renders on a message sent with parse_mode="HTML"; anywhere else the raw tag
# would be printed verbatim, and MSG_ADMIN_DONE is exactly such a place - the
# /premium confirmation goes out through m.answer() with no parse mode. So the
# screens use the *_tg helpers and the admin reply uses the plain ones.
#
# Only Start has a supplied ID. Premium keeps its plain trophy rather than an
# invented entity, which is why this table is built per tier instead of wrapping
# both blindly.
LEVEL_ICONS = {
    LEVEL_START:   "\U00002B50",
    LEVEL_PREMIUM: "\U0001F3C6",
}
LEVEL_ICONS_TG = {
    LEVEL_START:   T_LEVEL_START,
    LEVEL_PREMIUM: LEVEL_ICONS[LEVEL_PREMIUM],
}
LEVEL_NAMES = {
    LEVEL_START:   "Start",
    LEVEL_PREMIUM: "Premium",
}
LEVEL_LABELS    = {k: LEVEL_ICONS[k] + " " + LEVEL_NAMES[k] for k in LEVEL_NAMES}
LEVEL_LABELS_TG = {k: LEVEL_ICONS_TG[k] + " " + LEVEL_NAMES[k] for k in LEVEL_NAMES}

def level_of(is_premium):
    return LEVEL_PREMIUM if is_premium else LEVEL_START

def daily_limit(is_premium):
    # Reads the globals on every call rather than a precomputed table, so the
    # limit a user is held to always matches what this module currently holds.
    return PREMIUM_DAILY_SIGNALS if is_premium else START_DAILY_SIGNALS

def level_label(is_premium):
    # Plain unicode. Safe anywhere, including messages sent without parse_mode.
    return LEVEL_LABELS[level_of(is_premium)]

def level_label_tg(is_premium):
    # HTML only. Callers must send with parse_mode="HTML".
    return LEVEL_LABELS_TG[level_of(is_premium)]

def level_icon(is_premium):
    return LEVEL_ICONS[level_of(is_premium)]

def level_icon_tg(is_premium):
    return LEVEL_ICONS_TG[level_of(is_premium)]

def level_name(is_premium):
    return LEVEL_NAMES[level_of(is_premium)]

# "My level" screen, opened from the menu. Every field is filled per user by
# menu_level in bot.py - the tier and the limit come from the same lookup the
# enforcement uses, so this screen cannot advertise a number the server would
# refuse to honour.
# {tokens} is the in-game balance, appended rather than replacing the existing
# lines: the used/remaining counters are what the screen was already for.
MSG_LEVEL = ("{icon} <b>Your current level:</b> {name}\n"
             "\U0001F4CA <b>Daily limit:</b> {limit} signals\n"
             "\U0001F4C8 <b>Used today:</b> {used}\n"
             "\U000026A1 <b>Remaining today:</b> {left}\n"
             "\U0001FA99 <b>Game tokens:</b> {tokens}")

LEVEL_KB = [[("\U000000AB Back", "cb:go:menu")]]

# --- Unlock Premium screen --------------------------------------------------
# Opened from the main menu, where it replaced the VIP team link. Informational:
# it states the benefits, the configured Premium allowance and the viewer's own
# current status. {premium_limit} is PREMIUM_DAILY_SIGNALS, so the number quoted
# here is the same one the quota check enforces - it is never written out.
#
# There is deliberately no purchase flow here: Premium is assigned by an admin
# (/premium <tg_id>). Nothing on this screen asks for a deposit - the trading
# account balance gates verification, not the tier.
MSG_PREMIUM = ("\U0001F3C6 <b>Premium Level</b>\n\n"
               "<b>Premium benefits:</b>\n"
               "\U00002014 {premium_limit} signals/day\n"
               "\U00002014 Premium status\n\n"
               "{status}")

MSG_PREMIUM_ACTIVE = ("\U0001F3C6 <b>Your status:</b> Premium is active "
                      "\U00002014 {limit} signals/day.")

MSG_PREMIUM_INACTIVE = ("\U0001F7E2 <b>Your status:</b> Start "
                        "\U00002014 {limit} signals/day.\n\n"
                        "Premium is assigned by our team. Tap below to ask about it.")

def premium_kb(is_premium):
    # A user who already has Premium gets no request button - there is nothing
    # for them to ask for, and offering it would read as though it had lapsed.
    rows = []
    if not is_premium:
        rows.append([("\U0001F9D1 Ask about Premium", "url:" + SUPPORT_URL)])
    rows.append([("\U000000AB Back", "cb:go:menu")])
    return rows

# --- The in-game unlock -----------------------------------------------------
# Shown after tapping Unlock Premium. {cost} is PREMIUM_UNLOCK_COST and {needed}
# is what is still missing, both computed from the balance the atomic UPDATE
# actually saw - so the screen cannot quote a shortfall the database disagrees
# with. Tokens are fictional in-game credits: nothing on these screens asks for
# money, a deposit or a trading account.
MSG_PREMIUM_LOCKED = ("\U0001F451 <b>Premium Locked</b>\n\n"
                      "You need {cost} game tokens to unlock Premium.\n\n"
                      "Your balance: {balance}\n"
                      "Still needed: {needed} tokens")

def tokens_needed(balance):
    # The ONE shortfall calculation. Both the screens and the tests read it
    # from here, so a message can never quote a number the unlock disagrees
    # with. Floors at 0 so a balance above the cost never reads as negative.
    return max(PREMIUM_UNLOCK_COST - int(balance or 0), 0)

# --- Premium verification screens -------------------------------------------
# Shown while the user is in the Premium unlock flow.
#
# WORDING vs BEHAVIOUR - read before editing:
# These screens are written in real-currency terms ($, balance, account ID) by
# request. The code behind them is unchanged and still gates on game_tokens,
# a column written only by the /tokens and /tokenset admin commands, and the
# account-ID check is still format-only (5-15 digits, no panel lookup). So a
# real top-up does NOT move what this screen is actually waiting on. Keep that
# in mind before treating the copy as a description of the mechanism.
#
# The money emoji goes through pe(), which embeds the plain 💰 as the inner
# glyph: clients without premium emoji render that literal rather than nothing.
# E_MONEY is the supplied id and already existed above - not a new constant.
T_PREM_MONEY = pe(E_MONEY, "\U0001F4B0")

# {cost} is PREMIUM_UNLOCK_COST, so the figure quoted is the same one the
# unlock enforces rather than a literal typed into the copy.
MSG_PREMIUM_SHORT = (T_PREM_MONEY + " <b>Almost there.</b>\n\n"
                     "Your account is registered through our link. To unlock "
                     "access, top up your balance with <b>${cost}</b> or more "
                     "\U00002014 then send your account ID here again to "
                     "complete verification.")

# Threshold already met: the only thing left is the account-ID check.
MSG_PREMIUM_READY = (T_PREM_MONEY + " <b>Almost there.</b>\n\n"
                     "Your account is registered through our link. Your balance "
                     "meets the <b>${cost}</b> requirement \U00002014 send your "
                     "account ID here to complete verification.")

# The account ID was accepted but the threshold is still not met. Distinct from
# MSG_PREMIUM_SHORT so the user can tell the ID passed and the balance is what
# is outstanding - one screen for both would read as the ID having failed.
MSG_PREMIUM_STILL_SHORT = (T_PREM_MONEY + " <b>Almost there.</b>\n\n"
                           "Your account ID has been checked. Top up your balance "
                           "with <b>${cost}</b> or more \U00002014 then send your "
                           "account ID here again to complete verification.")

MSG_PREMIUM_UNLOCKED = ("\U0001F451 <b>Premium Unlocked!</b>\n\n"
                        "Your account ID has been verified successfully.\n\n"
                        "You now have <b>{limit} signals per day</b>.")

# The account ID did not pass the check. Says so and invites another attempt -
# there is no attempt limit and no lockout, so the user can send it as often as
# they like and each send is checked again.
MSG_ACCOUNT_ID_INVALID = ("\U00002757 <b>That account ID is not valid.</b>\n\n"
                          "An account ID is numbers only (5\U0000201315 digits). "
                          "Example: <b>123456789</b>\n\n"
                          "Send your account ID again to continue.")

# A second tap once Premium is held. The unlock statement refuses it (its WHERE
# requires is_premium = FALSE), so nothing was deducted and this only says so.
# The token balance line was dropped here: this screen now sits in a flow
# written in real-currency terms, and showing a game-token count next to it
# read as two different currencies on one screen.
MSG_PREMIUM_ALREADY = ("\U0001F451 <b>Premium is already active</b>\n\n"
                       "You have <b>{limit} signals per day</b>.")

# The unlock screens keep the plain Back button and nothing else: there is no
# purchase button because there is nothing to purchase.
UNLOCK_KB = [[("\U000000AB Back", "cb:go:menu")]]

# --- Admin level commands ---------------------------------------------------
# Replies to /premium and /startlevel. Admin-only (ADMIN_IDS); a non-admin gets
# no reply at all, so none of these strings ever reach an ordinary user.
MSG_ADMIN_USAGE   = "Usage: {cmd} <tg_id>"
MSG_ADMIN_NO_USER = "No user with tg_id {tg_id}. They must /start the bot first."
MSG_ADMIN_DONE    = "tg_id {tg_id} is now {level} \U00002014 {limit} signals/day."

# /tokens and /tokenset. Same admin-only rule as above: a non-admin gets no
# reply at all, so these strings never reach an ordinary user and the commands
# stay invisible to them.
MSG_TOKENS_USAGE  = "Usage: {cmd} <tg_id> <amount>"
MSG_TOKENS_DONE   = "tg_id {tg_id} now holds {balance} game tokens."

# Sent when a screen fails to render (see _screen_error in bot.py). Deliberately
# plain: no HTML, no custom emoji, no buttons, so it cannot fail for the same
# reason the screen did. The previous screen is left in place, so "try again"
# means tapping the button that is still on it.
MSG_SCREEN_ERROR = ("Something went wrong opening that screen. "
                    "Please try again in a moment, or send /start.")

# Shown both as the popup on a tap that is over the cap and as the screen text
# if the cap is reached while a signal is already being prepared. Plain text
# with no HTML: it is passed to cb.answer(), which does not parse markup.
MSG_DAILY_LIMIT = "Daily limit reached. Come back tomorrow."

# The limit screen still needs a way back, otherwise the user is stranded on it.
LIMIT_KB = [[("\U000000AB Back", "cb:go:menu")]]

# TODO: the picked pair only lives in memory (bot.py _pair_choice), so a restart
# mid-funnel falls back to this label.
DEFAULT_PAIR = "AUD/CAD OTC"

# Delayed follow-up sent a few seconds after the register screen opens (bot.py).
REGISTER_NUDGE = (pe(E_NUDGE_WARN, NUDGE_WARN_FALLBACK)
                  + " Only 2 Go+ activations left today."
                  "\n\nNo extensions. No second chance.\n\n"
                  + pe(E_NUDGE_ROCKET, NUDGE_ROCKET_FALLBACK)
                  + " Activate Go+ now.")

# --- Group F verification verdict messages (pe() style, factual, no scarcity) ---
_MINDEP = str(int(MIN_DEPOSIT)) if MIN_DEPOSIT == MIN_DEPOSIT.to_integral_value() else str(MIN_DEPOSIT)

# Access granted: campaign matches and deposits meet the minimum -> the "menu"
# screen above is shown instead of a text verdict.

# Campaign matches but deposits are below the minimum.
MSG_NEED_DEPOSIT = T_MONEY + " <b>Almost there.</b>\n\nYour account is registered through our link. To unlock access, top up your balance with <b>$" + _MINDEP + "</b> or more \U00002014 then send your account ID here again to complete verification."

# Account not found, or registered under a different campaign.
MSG_WRONG_LINK = pe("5447644880824181073", "\U000026A0\U0000FE0F") + " <b>Account not linked to us.</b>\n\nTo get access, your Pocket Option account must be created through our official link. Please register with the button below, then send your new account ID."

# Prepended to the menu screen when TEST_MODE is on. Blanked so the banner no
# longer shows; kept defined because bot.py still references it. NOTE: this only
# hides the notice - VERIFY_MODE=test still bypasses verification entirely.
MSG_TEST_MODE = ""

# Panel bot didn't answer in time; the retry worker will keep checking.
MSG_DELAYED = T_CLOCK + " <b>Verification is taking a moment.</b>\n\nWe're still checking your account. You'll be notified here automatically as soon as it's confirmed \U00002014 or you can send your account ID again shortly."

# Sent when a user re-sends their account ID inside UID_LOOKUP_COOLDOWN.
#
# This now fires only BETWEEN attempts. While a check is actually running, the
# in-flight guard in bot.py answers first with MSG_UID_IN_FLIGHT, which does
# not ask for a resend - that pairing was the loop users were stuck in.
MSG_UID_COOLDOWN = T_CLOCK + " <b>One moment.</b>\n\nYour last check is still going through. Please wait about {seconds}s, then send your account ID again."

# Sent when the SAME account ID arrives while its check is still running. The
# point of the wording is that there is nothing for the user to do: no resend,
# no waiting instruction, no second lookup. The verdict lands in this chat on
# its own when the running check finishes.
MSG_UID_IN_FLIGHT = T_CLOCK + " <b>Already checking.</b>\n\nWe are verifying <code>{uid}</code> right now \U00002014 no need to send it again. The result will appear here automatically."

# Sent when a DIFFERENT account ID arrives mid-check. The running check stays
# authoritative; nothing is cancelled and no second lookup is started.
MSG_UID_OTHER_PENDING = T_CLOCK + " <b>One check at a time.</b>\n\nWe are still verifying <code>{uid}</code>. Wait for that result before sending a different account ID."

# Last-resort reply when the UID handler itself fails (see capture_uid). Plain
# text, no buttons, so it can't fail for the same reason the handler did.
MSG_UID_ERROR = pe("5447644880824181073", "\U000026A0\U0000FE0F") + " <b>Something went wrong on our side.</b>\n\nYour account ID wasn't processed. Please send it again in a moment \U00002014 if it keeps failing, contact support: " + SUPPORT
