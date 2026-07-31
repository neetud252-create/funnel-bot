import os

_ch = os.getenv("CHANNEL_ID", "@apextraderrr")
CHANNEL_ID  = int(_ch) if _ch.lstrip("-").isdigit() else _ch
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/apextraderrr")
REF_LINK    = os.getenv("REF_LINK", "https://your-referral-link-here")
SUPPORT     = os.getenv("SUPPORT", "@go_plus_supportbot")
ADMIN_IDS   = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

SCREENS = {
    "gate": {
        "photo": "welcome",
        "text": "To continue, subscribe to the best Telegram channel about trading:\n\U0001F449 " + CHANNEL_URL + "\n\n\U0001F464 Once subscribed, click the Check subscription button below \U0001F447",
        "kb": [[("\U0001F517 Subscribe to Channel", "url:" + CHANNEL_URL)],
               [("\u2705 Check Subscription", "cb:check_sub")]],
    },
    "welcome": {
        "photo": "welcome",
        "text": "\U0001F44B <b>Welcome!</b>\n\nI am your personal trading assistant.\nI help you make smarter decisions with clear, simple insights.",
        "kb": [[("\U0001F680 Start", "cb:go:how")]],
    },
    "how": {
        "photo": "how",
        "text": "<b>How does it work?</b>\n\n1. Connect your account\n2. Get real-time market insights\n3. Act with clear, data-driven signals",
        "kb": [[("\u2699\uFE0F See the technology", "cb:go:tech")],
               [("\u2B05\uFE0F Back", "cb:go:welcome")]],
    },
    "tech": {
        "photo": "tech",
        "text": "<b>The technology</b>\n\n\U0001F50D Smart analysis across multiple assets\n\U0001F4CA Real-time market data\n\U0001F512 Secure and reliable\n\u26A1 Available 24/7 on any device",
        "kb": [[("\U0001F4CA See the results", "cb:gallery:0")],
               [("\u2B05\uFE0F Back", "cb:go:how")]],
    },
    "final": {
        "photo": "welcome",
        "text": "<b>Ready to start?</b>\n\nRegister below, then send me your account ID to unlock access.",
        "kb": [[("\U0001F511 Register", "cb:register")],
               [("\U0001F464 How to register", "cb:go:howto")],
               [("\U0001F3A7 Support", "cb:go:support")]],
    },
    "howto": {
        "photo": "register",
        "text": "<b>How to register</b>\n\n1. Open the registration link\n2. Create a <b>new</b> account\n3. Copy your account ID\n4. Send it to me here\n\n\u26A0\uFE0F Numbers only - no extra symbols.",
        "kb": [[("\U0001F511 Open registration", "url:" + REF_LINK)],
               [("\u2B05\uFE0F Back", "cb:go:final")]],
    },
    "support": {
        "photo": "welcome",
        "text": "\U0001F3A7 <b>Support</b>\n\nNeed help? Message " + SUPPORT,
        "kb": [[("\u2B05\uFE0F Back", "cb:go:final")]],
    },
    "register": {
        "photo": "register",
        "text": "\U0001F510 Register through the link below, then <b>send me your account ID</b>.\n\n\u26A0\uFE0F Your ID must contain numbers only.\nExample: <code>123456789</code>",
        "kb": [[("\U0001F511 Register & Get Access", "url:" + REF_LINK)],
               [("\u2B05\uFE0F Back", "cb:go:final")]],
    },
}

REVIEWS = ["reviews1", "reviews2", "reviews3"]
