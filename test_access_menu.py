"""Verification test for the access screen's button menu.

SCREENS["access"] used to carry a single "Activate Bot" button. It now carries
a six-button menu, and the ONE thing that must not have changed is where the
activation button goes:

    OLD:  Activate Bot   -> cb:go:register -> nav() -> register flow
    NEW:  Get Bot Access -> cb:go:register -> nav() -> register flow

The label changed; the callback did not. That is the whole guarantee, and it is
asserted here against bot.py's own handler registration rather than against a
copy of the string, so a later edit to either side fails this file.

Every other button reuses a destination that already existed in the project:

    Quick Setup Guide -> the "How to Register" video, the same URL the register
                         screen's own button opens
    Review            -> cb:results, the reviews-album handler in bot.py
    Support           -> SUPPORT_URL, as on the register and menu screens
    YouTube           -> YOUTUBE_URL, as on the menu screen
    Forex Tips        -> FOREX_TIPS_URL, the one destination this project did
                         not already have

Layout and colour are pinned to the specified design: two full-width rows, then
two rows of two. Telegram accepts exactly three button styles - success
(green), primary (blue), danger (red) - so those are what "green", "blue" and
"orange/red" mean here.

This test reads config and builds keyboards. It runs no verification, no panel
lookup, no database call and no bot flow.

Run from the repo root:  python test_access_menu.py
"""

import os
import re
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


# The activation callback, as it has always been. Not a new constant: this is
# the exact string the old Activate Bot button carried.
ACTIVATION_CALLBACK = "go:register"

# label -> (callback_data or None, url or None, style)
EXPECTED = {
    "Get Bot Access": ("go:register", None, "success"),
    "Quick Setup Guide": (None, "https://youtu.be/uJHBwXZVnNI?si=bhC7oMFLvoJfiQy",
                          "primary"),
    "\U00002B50 Review": ("results", None, "danger"),
    "Support": (None, config.SUPPORT_URL, "danger"),
    "YouTube": (None, config.YOUTUBE_URL, "primary"),
    "\U0001F4A1 Forex Tips": (None, config.FOREX_TIPS_URL, "primary"),
}

EXPECTED_ROWS = [
    ["Get Bot Access"],
    ["Quick Setup Guide"],
    ["\U00002B50 Review", "Support"],
    ["YouTube", "\U0001F4A1 Forex Tips"],
]


def main():
    H._install_stub_modules()
    bot_mod = H._load_bot()
    bot_src = open(os.path.join(ROOT, "bot.py"), encoding="utf-8").read()
    config_src = open(os.path.join(ROOT, "config.py"), encoding="utf-8").read()

    screen = config.SCREENS["access"]
    kb = bot_mod.build_kb(screen["kb"])
    rows = kb.inline_keyboard
    flat = [b for row in rows for b in row]
    labels = [b.text for b in flat]

    # --- 1. the old button is gone -----------------------------------------
    print("[access-menu] Activate Bot is gone")
    check("no button is labelled 'Activate Bot'",
          "Activate Bot" not in labels, str(labels))
    check("the label does not survive anywhere in the screen's keyboard",
          "Activate Bot" not in str(screen["kb"]), str(screen["kb"]))
    # Labels across every screen, not raw source: a comment in config.py that
    # explains what this button used to be called is documentation, not a
    # button, and must not fail this check.
    every_label = [item[0] for other in config.SCREENS.values()
                   for row in other.get("kb", []) for item in row]
    check("no screen anywhere still has an 'Activate Bot' button",
          "Activate Bot" not in every_label, str(every_label))

    # --- 2 & 3. Get Bot Access, on the SAME action -------------------------
    print("\n[access-menu] Get Bot Access reaches the same handler")
    check("a button is labelled 'Get Bot Access'",
          "Get Bot Access" in labels, str(labels))
    access_btn = next((b for b in flat if b.text == "Get Bot Access"), None)
    if access_btn is None:
        print("\n%d checks, %d failed" % (CHECKS[0], len(FAILURES)))
        return 1
    check("it carries the original activation callback, not a new one",
          access_btn.callback_data == ACTIVATION_CALLBACK,
          repr(access_btn.callback_data))
    check("it is a callback button, never a URL",
          access_btn.url is None, repr(access_btn.url))
    # The destination is the callback string, and this is the handler that
    # matches it. Asserted against bot.py so renaming either side fails here.
    check("bot.py still routes that callback through nav()",
          'F.data.startswith("go:")' in bot_src and "async def nav(" in bot_src)
    check("nav() still arms UID capture for the register key",
          'if key == "register":' in bot_src
          and "await state.set_state(Reg.waiting_uid)" in bot_src)
    check("nav() still starts the register nudge",
          "_register_nudge(bot, tg_id, state)" in bot_src)
    check("no second activation handler was introduced",
          bot_src.count('F.data.startswith("go:")') == 1)
    check("the callback still names the register screen, which still exists",
          ACTIVATION_CALLBACK.split(":")[1] in config.SCREENS,
          ACTIVATION_CALLBACK)
    check("the activation icon is the one the old button used",
          access_btn.icon_custom_emoji_id == "6280525956771745921",
          repr(access_btn.icon_custom_emoji_id))

    # --- 4. Quick Setup Guide ----------------------------------------------
    print("\n[access-menu] Quick Setup Guide")
    check("a button is labelled 'Quick Setup Guide'",
          "Quick Setup Guide" in labels, str(labels))
    guide = next((b for b in flat if b.text == "Quick Setup Guide"), None)
    if guide:
        check("it opens the existing How-to-Register video, not a new URL",
              guide.url == "https://youtu.be/uJHBwXZVnNI?si=bhC7oMFLvoJfiQy",
              repr(guide.url))
        check("that URL is the register screen's own, reused verbatim",
              guide.url in str(config.SCREENS["register"]["kb"]),
              str(config.SCREENS["register"]["kb"]))

    # --- 5 & 6. the paired rows --------------------------------------------
    print("\n[access-menu] the layout matches the design")
    check("there are four rows", len(rows) == 4, str(len(rows)))
    check("the rows are 1, 1, 2, 2 buttons",
          [len(r) for r in rows] == [1, 1, 2, 2], str([len(r) for r in rows]))
    actual_rows = [[b.text for b in row] for row in rows]
    check("every row matches the specified order",
          actual_rows == EXPECTED_ROWS, str(actual_rows))
    if len(rows) == 4:
        check("Review and Support share row 3",
              [b.text for b in rows[2]] == ["\U00002B50 Review", "Support"],
              str([b.text for b in rows[2]]))
        check("YouTube and Forex Tips share row 4",
              [b.text for b in rows[3]]
              == ["YouTube", "\U0001F4A1 Forex Tips"],
              str([b.text for b in rows[3]]))
        check("Get Bot Access is alone on row 1",
              len(rows[0]) == 1 and rows[0][0].text == "Get Bot Access",
              str([b.text for b in rows[0]]))
        check("Quick Setup Guide is alone on row 2",
              len(rows[1]) == 1 and rows[1][0].text == "Quick Setup Guide",
              str([b.text for b in rows[1]]))

    # --- destinations and colours ------------------------------------------
    print("\n[access-menu] every destination and colour")
    check("exactly six buttons", len(flat) == 6, str(len(flat)))
    check("no button was dropped for a malformed URL",
          len(flat) == sum(len(r) for r in screen["kb"]),
          "%d rendered of %d configured"
          % (len(flat), sum(len(r) for r in screen["kb"])))
    for label, (cb, url, style) in EXPECTED.items():
        button = next((b for b in flat if b.text == label), None)
        check("%r is present" % label, button is not None, str(labels))
        if button is None:
            continue
        if cb is not None:
            check("%r uses callback %r" % (label, cb),
                  button.callback_data == cb, repr(button.callback_data))
            check("%r is not a URL button" % label, button.url is None,
                  repr(button.url))
        else:
            check("%r opens %s" % (label, url), button.url == url,
                  repr(button.url))
            check("%r is not a callback button" % label,
                  button.callback_data is None, repr(button.callback_data))
        check("%r is styled %s" % (label, style),
              button.model_dump(exclude_none=True).get("style") == style,
              repr(button.model_dump(exclude_none=True).get("style")))

    print("\n[access-menu] the colours are the three Telegram allows")
    styles = [b.model_dump(exclude_none=True).get("style") for b in flat]
    check("every button carries a style", all(styles), str(styles))
    check("only success/primary/danger are used",
          set(styles) <= {"success", "primary", "danger"}, str(set(styles)))
    check("Get Bot Access is the only green button",
          styles.count("success") == 1 and styles[0] == "success", str(styles))
    check("Review and Support are the red pair",
          styles[2] == "danger" and styles[3] == "danger", str(styles))
    check("Quick Setup Guide, YouTube and Forex Tips are blue",
          styles[1] == "primary" and styles[4] == "primary"
          and styles[5] == "primary", str(styles))

    # --- reused, not invented ----------------------------------------------
    print("\n[access-menu] the destinations already existed")
    check("Support reuses SUPPORT_URL, the same constant the menu uses",
          'url:" + SUPPORT_URL' in config_src.split('"access":')[1]
          .split('"register":')[0], "access screen does not use SUPPORT_URL")
    check("YouTube reuses YOUTUBE_URL, the same constant the menu uses",
          'url:" + YOUTUBE_URL' in config_src.split('"access":')[1]
          .split('"register":')[0], "access screen does not use YOUTUBE_URL")
    check("Review reuses the existing results handler",
          '@dp.callback_query(F.data == "results")' in bot_src)
    check("FOREX_TIPS_URL is env-backed like every other link",
          'FOREX_TIPS_URL = os.getenv("FOREX_TIPS_URL"' in config_src)
    check("its placeholder is still a valid URL, so the button renders",
          bot_mod._URL_OK.match(config.FOREX_TIPS_URL) is not None,
          config.FOREX_TIPS_URL)

    # --- 7. nothing unrelated moved ----------------------------------------
    print("\n[access-menu] no other screen or flow changed")
    check("this screen is still a video screen",
          screen.get("video") == "access", repr(screen.get("video")))
    check("its caption was not touched",
          "Activate the bot now" in re.sub(r"<[^>]+>", "", screen["text"]),
          repr(screen["text"][-80:]))
    check("no photo key was introduced", "photo" not in screen,
          str(sorted(screen)))
    check("the register screen's keyboard is unchanged",
          [b[0] for b in config.SCREENS["register"]["kb"][0]]
          == ["Register & Get Access"],
          str(config.SCREENS["register"]["kb"]))
    check("the menu screen's keyboard is unchanged",
          [row[0][0] for row in config.SCREENS["menu"]["kb"]]
          == ["Get a signal", "Unlock Premium", "My level", "Support",
              "Telegram channel", "YouTube channel"],
          str([row[0][0] for row in config.SCREENS["menu"]["kb"]]))
    check("the results screen's keyboard is unchanged",
          [row[0][0] for row in config.SCREENS["results"]["kb"]]
          == ["Get access to Go +", "Open Telegram channel"],
          str([row[0][0] for row in config.SCREENS["results"]["kb"]]))
    check("no other screen gained this menu",
          all("Get Bot Access" not in str(other.get("kb", ""))
              for key, other in config.SCREENS.items() if key != "access"))
    check("the referral link is untouched",
          config.REF_LINK == os.getenv("REF_LINK",
                                       "https://example.com/PLACEHOLDER_REF"),
          config.REF_LINK)
    for untouched in ("MIN_DEPOSIT", "CAMPAIGN_ID", "VERIFY_MODE",
                      "UID_VERIFY_RETRIES", "PREMIUM_UNLOCK_COST"):
        check("%s is still defined and was not moved" % untouched,
              untouched + " " in config_src or untouched + "=" in config_src,
              untouched)

    print("\n%d checks, %d failed" % (CHECKS[0], len(FAILURES)))
    if FAILURES:
        for f in FAILURES:
            print("  FAILED: " + f)
        return 1
    print("PASS - the menu replaced the button and kept its action.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
