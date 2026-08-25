"""Verification test for the Welcome screen.

Welcome is the screen shown once the subscription gate is passed: a short
headline, one line of copy, and a single Start button.

Its caption emoji and its button emoji use two different mechanisms, and the
difference is the thing most easily got wrong:

  * caption emoji are <tg-emoji> entities produced by pe(), and resolve only
    when the message is sent with parse_mode="HTML"
  * the Start button emoji is InlineKeyboardButton.icon_custom_emoji_id, the
    4th element of a button tuple, which Telegram draws BEFORE the label

A button label is plain text with no entities, so putting the unicode rocket in
the label is NOT equivalent - and would render a second glyph next to the
custom one. The button assertions therefore run against the payload
bot.build_kb actually produces rather than against the config tuple.

Two of this screen's ids are shared with other constants (the pointing finger
with E_BACK, the rocket with E_NUDGE_ROCKET and E_MENU_SIGNAL). The screen
declares its own, and this file checks the originals still hold their values,
so a future restyle of one cannot quietly move the other.

Run from the repo root:  python test_welcome_screen.py

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


CAPTION_EMOJI = [
    ("robot", "5924946082487341386", "\U0001F916"),
    ("point", "5305522282695768654", "\U0001F447"),
]

START_ICON = "5188481279963715781"

# The caption with its entities stripped - what the user actually reads.
EXPECT_PLAIN = ("\U0001F916 Welcome to Go+\n\n"
                "Your personal trading assistant \U00002014 clear, data-driven "
                "signals without the complex analysis.\n\n"
                "\U0001F447 Tap Start to begin")


def strip_entities(html):
    import re
    return re.sub(r"<tg-emoji [^>]*>(.*?)</tg-emoji>", r"\1", html)


def main():
    screen = config.SCREENS["welcome"]
    text = screen["text"]

    # --- the caption --------------------------------------------------------
    print("[welcome] caption content")
    check("rendered caption matches the specified copy exactly",
          strip_entities(text) == EXPECT_PLAIN,
          repr(strip_entities(text)))
    check("headline reads 'Welcome to Go+'",
          "Welcome to Go+" in text, repr(text))
    check("states the one-line positioning",
          "Your personal trading assistant" in text, repr(text))
    check("mentions data-driven signals",
          "clear, data-driven signals without the complex analysis." in text,
          repr(text))
    check("closes with the Start instruction",
          "Tap Start to begin" in text, repr(text))
    check("uses a real em dash, not a hyphen",
          "\U00002014" in text, repr(text))
    check("no hyphen stands in for the em dash",
          " - " not in strip_entities(text), repr(text))
    check("blank line between headline and body",
          strip_entities(text).split("\n")[1] == "", repr(text))
    check("no unfilled placeholder", "{" not in text and "}" not in text, repr(text))
    check("no bold markup was introduced", "<b>" not in text, repr(text))

    # --- caption custom emoji ------------------------------------------------
    print("\n[welcome] caption custom emoji entities")
    for name, emoji_id, glyph in CAPTION_EMOJI:
        entity = config.pe(emoji_id, glyph)
        check("%s: entity with id %s is present" % (name, emoji_id),
              entity in text, entity)
        check("%s: wraps its own glyph as the non-premium fallback" % name,
              (">" + glyph + "</tg-emoji>") in text, glyph)
    check("exactly two custom emoji in the caption",
          text.count("<tg-emoji") == 2, str(text.count("<tg-emoji")))
    check("the robot leads the caption",
          text.startswith(config.pe("5924946082487341386", "\U0001F916")),
          repr(text[:60]))
    check("the pointing finger is on the last line",
          text.rsplit("\n", 1)[-1].startswith(
              config.pe("5305522282695768654", "\U0001F447")),
          repr(text.rsplit("\n", 1)[-1]))
    check("the two caption ids are different",
          CAPTION_EMOJI[0][1] != CAPTION_EMOJI[1][1])

    # --- the Start button, as actually built --------------------------------
    print("\n[welcome] Start button payload as built by bot.build_kb")
    H._install_stub_modules()
    bot_mod = H._load_bot()
    kb = bot_mod.build_kb(screen["kb"])
    flat = [b for row in kb.inline_keyboard for b in row]

    check("exactly one button on this screen", len(flat) == 1, str(len(flat)))
    check("it is on a single row",
          [len(r) for r in kb.inline_keyboard] == [1],
          str([len(r) for r in kb.inline_keyboard]))
    if flat:
        payload = flat[0].model_dump(exclude_none=True)
        check("text is exactly 'Start'",
              payload.get("text") == "Start", repr(payload.get("text")))
        # The assertion this file is really for.
        check("icon_custom_emoji_id is %s" % START_ICON,
              payload.get("icon_custom_emoji_id") == START_ICON,
              "got " + repr(payload.get("icon_custom_emoji_id", "<ABSENT>")))
        check("the label carries no unicode rocket",
              "\U0001F680" not in payload.get("text", ""),
              repr(payload.get("text")))
        check("the label is pure ASCII, so no glyph doubles up",
              all(ord(ch) < 128 for ch in payload.get("text", "")),
              repr(payload.get("text")))
        check("callback is unchanged (go:how)",
              payload.get("callback_data") == "go:how",
              repr(payload.get("callback_data")))
        check("style is unchanged (success)",
              payload.get("style") == "success", repr(payload.get("style")))
        check("it is a callback button, not a URL",
              flat[0].url is None, repr(getattr(flat[0], "url", None)))
    check("the screen still renders its own artwork",
          screen["photo"] == "welcome", repr(screen.get("photo")))

    # --- nothing outside this screen moved -----------------------------------
    print("\n[welcome] no other screen or button changed")
    # The robot id is new and must appear on this screen alone.
    for key, other in config.SCREENS.items():
        if key == "welcome":
            continue
        check("screen %r does not use the new robot id" % key,
              "5924946082487341386" not in (other.get("text") or ""), key)
        for row in (other.get("kb") or []):
            for item in row:
                icon = item[3] if len(item) > 3 else None
                check("screen %r button %r kept its own icon"
                      % (key, item[0][:18]),
                      icon != "5924946082487341386", str(icon))

    # The shared ids must still hold their original values elsewhere.
    check("E_BACK still holds its id", config.E_BACK == "5305522282695768654")
    check("E_NUDGE_ROCKET still holds its id",
          config.E_NUDGE_ROCKET == "5188481279963715781")
    check("E_MENU_SIGNAL still holds its id",
          config.E_MENU_SIGNAL == "5188481279963715781")

    # Screens either side of Welcome in the funnel are intact.
    check("the gate still has its two buttons, in order",
          [b[1] for row in config.SCREENS["gate"]["kb"] for b in row]
          == ["url:" + config.CHANNEL_URL, "cb:check_sub"],
          str(config.SCREENS["gate"]["kb"]))
    check("the gate's Join Channel icon is untouched",
          config.SCREENS["gate"]["kb"][0][0][3] == config.E_GATE_JOIN)
    check("the gate's Check Subscription icon is untouched",
          config.SCREENS["gate"]["kb"][1][0][3] == config.E_GATE_CHECK)
    check("the 'how' screen Welcome leads to is unchanged",
          [b[1] for row in config.SCREENS["how"]["kb"] for b in row]
          == ["cb:go:tech"], str(config.SCREENS["how"]["kb"]))
    check("the main menu still has six buttons",
          sum(len(r) for r in config.SCREENS["menu"]["kb"]) == 6,
          str(sum(len(r) for r in config.SCREENS["menu"]["kb"])))
    check("the menu's Get a signal button kept its own icon",
          config.SCREENS["menu"]["kb"][0][0][3] == config.E_MENU_SIGNAL,
          str(config.SCREENS["menu"]["kb"][0][0]))
    check("the mode screen buttons are untouched",
          [b[1] for row in config.SCREENS["mode"]["kb"] for b in row]
          == ["cb:mode:manual", "cb:mode:auto", "cb:go:menu"],
          str(config.SCREENS["mode"]["kb"]))

    print("\n%d checks, %d failed" % (CHECKS[0], len(FAILURES)))
    if FAILURES:
        for f in FAILURES:
            print("  FAILED: " + f)
        return 1
    print("PASS - the Welcome screen renders the specified copy, emoji and button.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
