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

def pe(emoji_id, fallback):
    return '<tg-emoji emoji-id="' + emoji_id + '">' + fallback + '</tg-emoji>'

T_INFO  = pe(E_INFO, "\u2139\uFE0F")
T_POINT = pe(E_POINT, "\U0001F449")

SCREENS = {
    "gate": {
        "photo": "welcome",
        "text": T_INFO + " To continue, subscribe to the best Telegram channel about trading:\n\n" + T_POINT + " " + CHANNEL_URL + "\n\nOnce subscribed, tap Check Subscription below.",
        "kb": [[("Subscribe to Channel", "url:" + CHANNEL_URL, "primary", E_POINT)],
               [("Check Subscription", "cb:check_sub", "success")]],
    },
    "welcome": {
        "photo": "welcome",
        "text": "\U0001F44B <b>Welcome!</b>\n\nI am your personal trading assistant.\nI help you make smarter decisions with clear, simple insights.",
        "kb": [[("Start", "cb:go:how", "success", E_POINT)]],
    },
    "how": {
        "photo": "how",
        "text": T_INFO + " <b>How does it work?</b>\n\n1. Connect your account\n2. Get real-time market insights\n3. Act with clear, data-driven signals",
        "kb": [[("See the technology", "cb:go:tech", "primary")],
               [("Back", "cb:go:welcome", None, E_BACK)]],
    },
    "tech": {
        "photo": "tech",
        "text": "<b>The technology</b>\n\n\U0001F50D Smart analysis across multiple assets\n\U0001F4CA Real-time market data\n\U0001F512 Secure and reliable\n\u26A1 Available 24/7 on any device",
        "kb": [[("See the results", "cb:gallery:0", "primary")],
               [("Back", "cb:go:how", None, E_BACK)]],
    },
    "final": {
        "photo": "welcome",
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
        "photo": "welcome",
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
