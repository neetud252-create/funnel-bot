import os

_ch = os.getenv("CHANNEL_ID", "@apextraderrr")
CHANNEL_ID  = int(_ch) if _ch.lstrip("-").isdigit() else _ch
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/apextraderrr")
REF_LINK    = os.getenv("REF_LINK", "https://your-referral-link-here")
SUPPORT     = os.getenv("SUPPORT", "@go_plus_supportbot")
ADMIN_IDS   = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

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

SCREENS = {
    "gate": {
        "photo": "gate",
        "text": "To continue, subscribe to the best Telegram channel about trading:\n\n" + T_POINT + " " + CHANNEL_URL + "\n\n\U0001F464 Once subscribed, click the \u201cCheck Subscription\u201d button below \U0001F447",
        "kb": [[("Subscribe to Channel", "url:" + CHANNEL_URL, "primary", E_POINT)],
               [("Check Subscription", "cb:check_sub", "success", E_INFO)]],
    },
    "welcome": {
        "photo": "welcome",
        "text": "\U0001F590\uFE0F <b>Hello! I am Go+, your personal trading bot.</b>\n\n" + T_MONEY + " I help you approach trading with clear, data-driven insights - without stress and without complex analysis.",
        "kb": [[("Start", "cb:go:how", "success", E_FLASH)]],
    },
    "how": {
        "photo": "how",
        "text": T_STAR + "<b>Why traders choose Go+:</b>\n\n100+ trading assets\nOTC and exchange trading\n2 trading modes for every style\nInstant chart analysis\nAvailable 24/7 and compatible with any device\n\n" + T_GEM + " <b>You get a tool that is always one step ahead of the market.</b>",
        "kb": [[("How Does It Work", "cb:go:tech", "primary", E_QMARK)],
               [("Back", "cb:go:welcome", None, E_BACK)]],
    },
    "tech": {
        "photo": "tech",
        "text": T_ROBOT + " <b>It is simple:</b>\n\n" + T_N1 + " Select an asset\n" + T_N2 + " Choose the expiration time\n" + T_N3 + " Get a signal: BUY " + T_GREEN + " / SELL " + T_DOWN + "\n" + T_N4 + " " + T_CLOCK + " Open a trade\n" + T_N5 + " Track the result\n\n" + T_SHAKE + " I take care of the market analysis for you - all you have to do is act.",
        "kb": [[("See the technology", "cb:go:ai", "primary", E_GEAR)],
               [("Back", "cb:go:how", None, E_BACK)]],
    },
    "ai": {
        "photo": "ai",
        "text": T_GEAR + "<b>I am powered by advanced AI,</b>\nwhich processes huge amounts of data in seconds.\n\n" + T_CHART + " <b>I analyze charts using hundreds of indicators, price patterns, and technical analysis tools.</b>\n\n" + T_LENS + " <b>I detect patterns that humans often miss.</b>\n\n" + T_BOLT + " <b>Every signal I generate is based on precise calculations - not guesswork.</b>",
        "kb": [[("See real results", "cb:gallery:0", "primary")],
               [("Back", "cb:go:tech", None, E_BACK)]],
    },
    "final": {
        "photo": "final",
        "text": "<b>Ready to start?</b>\n\nRegister below, then send me your account ID to unlock access.",
        "kb": [[("Register & Get Access", "cb:register", "success", E_POINT)],
               [("How to register", "cb:go:howto", "primary", E_INFO)],
               [("Support", "cb:go:support")]],
    },
    "howto": {
        "photo": "register",
        "text": T_INFO + " <b>How to register</b>\n\n1. Open the registration link\n2. Create a <b>new</b> account\n3. Copy your account ID\n4. Send it to me here\n\n\u26A0\uFE0F Numbers only - no extra symbols.",
        "kb": [[("Open registration", "url:" + REF_LINK, "success", E_POINT)],
               [("Back", "cb:go:final", None, E_BACK)]],
    },
    "support": {
        "photo": "support",
        "text": "\U0001F3A7 <b>Support</b>\n\nNeed help? Message " + SUPPORT,
        "kb": [[("Back", "cb:go:final", None, E_BACK)]],
    },
    "register": {
        "photo": "register",
        "text": "\U0001F510 Register through the link below, then <b>send me your account ID</b>.\n\n\u26A0\uFE0F Your ID must contain numbers only.\nExample: <code>123456789</code>",
        "kb": [[("Register & Get Access", "url:" + REF_LINK, "success", E_POINT)],
               [("Back", "cb:go:final", None, E_BACK)]],
    },
}

REVIEWS = ["reviews1", "reviews2", "reviews3"]
