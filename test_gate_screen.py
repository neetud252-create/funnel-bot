"""Verification test for the subscription gate screen.

The gate is the screen /start lands an unverified user on: join the channel,
then tap Check Subscription. Its caption and its two button icons are premium
custom emoji, and the two mechanisms are different in a way that is easy to get
wrong:

  * caption emoji are <tg-emoji> entities, rendered by pe() into the message
    body, and only resolve when the message is sent with parse_mode="HTML"
  * button emoji are InlineKeyboardButton.icon_custom_emoji_id, the 4th element
    of a button tuple, which Telegram draws BEFORE the label

A button label cannot carry an entity - it is plain text with no parse_mode -
so putting the unicode glyph in the label is NOT the same thing and is the
mistake this file exists to catch. The button assertions therefore run against
the payload bot.build_kb actually produces, not against the config tuple.

What must not move is asserted too: the channel URL, the check_sub callback,
the button order, the styles, and the fact that there are exactly two buttons.

Run from the repo root:  python test_gate_screen.py

No network and no database: db and panelbot are stubbed through the helpers in
test_signal_flow, which are also what let bot.py be imported at all.
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

import config
import test_signal_flow as H


FAILURES = []
CHECKS = [0]


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if condition:
        print("  ok   " + label)
    else:
        print("  FAIL " + label + ((" -- " + detail) if detail else ""))
        FAILURES.append(label)


# The four caption entities, in the order they appear.
CAPTION_EMOJI = [
    ("lock",    "5296369303661067030", "\U0001F512"),
    ("mega",    "5983400750594658672", "\U0001F4E3"),
    ("speaker", "5247187233722607160", "\U0001F50A"),
    ("point",   "5305522282695768654", "\U0001F447"),
]

# callback/url -> (expected label, expected icon id, expected style)
BUTTONS = [
    ("https://t.me/apexxtraderz", "Join Channel", "5397916757333654639", "primary"),
    ("check_sub", "Check Subscription", "5260463209562776385", "success"),
]


def main():
    gate = config.SCREENS["gate"]
    text = gate["text"]

    # --- the caption --------------------------------------------------------
    print("[gate] caption text")
    check("headline reads ONE STEP TO UNLOCK GO+",
          "ONE STEP TO UNLOCK GO+" in text, repr(text))
    check("the padlock sits directly against the headline, no space",
          "</tg-emoji>ONE STEP" in text, repr(text[:120]))
    check("invites the user to join the free channel",
          "Join our free trading channel to activate the bot:" in text, repr(text))
    check("names the channel as an @mention",
          "@apexxtraderz" in text, repr(text))
    check("the mention is bare, so Telegram auto-links it",
          "<a " not in text and "](" not in text, repr(text))
    check("closes by pointing at the buttons",
          "Then tap Check Subscription below " in text, repr(text))
    check("no unfilled placeholder", "{" not in text and "}" not in text, repr(text))

    # The handle must be the channel the Join button opens, not a second
    # hardcoded copy that could drift from CHANNEL_URL.
    check("the mention is derived from CHANNEL_URL",
          config.CHANNEL_MENTION in text, config.CHANNEL_MENTION)
    check("CHANNEL_URL and the mention name the same channel",
          config.CHANNEL_URL.rsplit("/", 1)[-1] == config.CHANNEL_MENTION.lstrip("@"),
          "%s vs %s" % (config.CHANNEL_URL, config.CHANNEL_MENTION))
    # A private invite link has no @handle; it must degrade to the raw URL
    # rather than emit a mention that resolves to nothing.
    check("a private invite link falls back to the raw URL",
          config._channel_mention("https://t.me/+AbCdEf123") == "https://t.me/+AbCdEf123")
    check("an empty CHANNEL_URL does not produce a bare '@'",
          config._channel_mention("") == "")

    # --- caption custom emoji ------------------------------------------------
    print("\n[gate] caption custom emoji entities")
    for name, emoji_id, glyph in CAPTION_EMOJI:
        entity = config.pe(emoji_id, glyph)
        check("%s: entity with id %s is present" % (name, emoji_id),
              entity in text, entity)
        check("%s: wraps its own glyph as the non-premium fallback" % name,
              (">" + glyph + "</tg-emoji>") in text, glyph)
    check("exactly four custom emoji in the caption",
          text.count("<tg-emoji") == 4, str(text.count("<tg-emoji")))
    # Order matters: the megaphone and speaker must precede the handle.
    positions = [text.index(config.pe(i, g)) for _, i, g in CAPTION_EMOJI]
    check("the four entities appear in the specified order",
          positions == sorted(positions), str(positions))
    check("megaphone and speaker come immediately before the handle",
          config.pe("5983400750594658672", "\U0001F4E3")
          + config.pe("5247187233722607160", "\U0001F50A")
          + config.CHANNEL_MENTION in text, repr(text))

    # --- the buttons, as actually built -------------------------------------
    print("\n[gate] button payload as built by bot.build_kb")
    H._install_stub_modules()
    bot_mod = H._load_bot()
    kb = bot_mod.build_kb(gate["kb"])
    flat = [b for row in kb.inline_keyboard for b in row]

    check("exactly two buttons on this screen", len(flat) == 2, str(len(flat)))
    check("each button is on its own row",
          [len(r) for r in kb.inline_keyboard] == [1, 1],
          str([len(r) for r in kb.inline_keyboard]))
    check("no button was dropped for a bad URL", len(flat) == len(gate["kb"]))

    for i, (target, want_text, want_icon, want_style) in enumerate(BUTTONS):
        if i >= len(flat):
            continue
        b = flat[i]
        payload = b.model_dump(exclude_none=True)
        check("button %d: text is %r" % (i + 1, want_text),
              payload.get("text") == want_text, repr(payload.get("text")))
        # The assertion this file is really for.
        check("button %d: icon_custom_emoji_id is %s" % (i + 1, want_icon),
              payload.get("icon_custom_emoji_id") == want_icon,
              "got " + repr(payload.get("icon_custom_emoji_id", "<ABSENT>")))
        check("button %d: label is a bare word, no unicode emoji" % (i + 1),
              all(ord(ch) < 128 for ch in payload.get("text", "")),
              repr(payload.get("text")))
        check("button %d: style is unchanged (%s)" % (i + 1, want_style),
              payload.get("style") == want_style, repr(payload.get("style")))

    # Order, URL and callback are behaviour, not styling - they must not move.
    check("first button opens the channel URL",
          flat[0].url == config.CHANNEL_URL, repr(getattr(flat[0], "url", None)))
    check("first button is a URL button, not a callback",
          flat[0].callback_data is None, repr(flat[0].callback_data))
    check("second button keeps the check_sub callback",
          flat[1].callback_data == "check_sub", repr(flat[1].callback_data))
    check("second button is a callback button, not a URL",
          flat[1].url is None, repr(getattr(flat[1], "url", None)))
    check("order is Join Channel then Check Subscription",
          [b.text for b in flat] == ["Join Channel", "Check Subscription"],
          str([b.text for b in flat]))
    check("the two button icons are different ids",
          flat[0].icon_custom_emoji_id != flat[1].icon_custom_emoji_id)
    check("the gate still renders its own artwork",
          gate["photo"] == "gate", repr(gate.get("photo")))

    # --- nothing else moved --------------------------------------------------
    # The gate's ids must not have leaked onto another screen, and the screens
    # either side of it in the funnel must still be intact.
    print("\n[gate] no other screen or button changed")
    gate_ids = {i for _, i, _ in CAPTION_EMOJI} | {"5397916757333654639",
                                                  "5260463209562776385"}
    for key, screen in config.SCREENS.items():
        if key == "gate":
            continue
        for row in (screen.get("kb") or []):
            for item in row:
                icon = item[3] if len(item) > 3 else None
                check("screen %r button %r did not take a gate icon"
                      % (key, item[0][:18]),
                      icon not in gate_ids, str(icon))

    check("the welcome screen still has its single Start button",
          [b[1] for row in config.SCREENS["welcome"]["kb"] for b in row]
          == ["cb:go:how"], str(config.SCREENS["welcome"]["kb"]))
    check("the main menu still has six buttons",
          sum(len(r) for r in config.SCREENS["menu"]["kb"]) == 6,
          str(sum(len(r) for r in config.SCREENS["menu"]["kb"])))
    check("the mode screen buttons are untouched",
          [b[1] for row in config.SCREENS["mode"]["kb"] for b in row]
          == ["cb:mode:manual", "cb:mode:auto", "cb:go:menu"],
          str(config.SCREENS["mode"]["kb"]))
    check("Manual still carries its own icon",
          config.SCREENS["mode"]["kb"][0][0][3] == config.E_MODE_MANUAL)
    check("E_BACK is unchanged even though the gate reuses its id",
          config.E_BACK == "5305522282695768654")

    print("\n%d checks, %d failed" % (CHECKS[0], len(FAILURES)))
    if FAILURES:
        for f in FAILURES:
            print("  FAILED: " + f)
        return 1
    print("PASS - the gate screen renders the specified copy, emoji and buttons.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
