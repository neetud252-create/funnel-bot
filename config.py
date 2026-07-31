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

def pe(emoji_id, fallback):
    return '<tg-emoji emoji-id="' + emoji_id + '">' + fallback + '</tg-emoji>'

T_INFO  = pe(E_INFO, "\u2139\uFE0F")
T_POINT = pe(E_POINT, "\U0001F449")
T_MONEY = pe(E_MONEY, "\U0001F4B0")
T_STAR  = pe(E_STAR, "\u2728")
T_GEM   = pe(E_GEM, "\U0001F48E")

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
        "text": "<b>The technology</b>\n\n\U0001F50D Smart analysis across multiple assets\n\U0001F4CA Real-time market data\n\U0001F512 Secure and reliable\n\u26A1 Available 24/7 on any device",
        "kb": [[("See the results", "cb:gallery:0", "primary")],
               [("Back", "cb:go:how", None, E_BACK)]],
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
