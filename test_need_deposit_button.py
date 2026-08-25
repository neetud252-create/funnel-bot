"""Verification test for the "Almost there" deposit-verdict button.

MSG_NEED_DEPOSIT is the verdict shown when an account ID verifies but the
deposit is under MIN_DEPOSIT. Its keyboard comes from _register_btn() in
bot.py, which is SHARED with the wrong-link verdict (MSG_WRONG_LINK).

Only the deposit verdict was restyled. The helper keeps its original label and
icon as defaults, and the deposit call site passes an override, so:

    MSG_NEED_DEPOSIT -> "Register & Get Access" + 5836690092306992715
    MSG_WRONG_LINK   -> unchanged, still the helper's defaults

Both are asserted here. The wrong-link payload is pinned against the values
captured before the change, so folding the override back into the helper's
default - which would move both screens at once - fails this file.

URL, style and position are not overridable and are checked to be identical on
both paths. The label must stay bare text: the icon supplies the padlock, and a
unicode key in the label would render a second glyph beside it.

This test verifies presentation only. It builds keyboards and reads config; it
runs no verification, no panel lookup and no database call.

Run from the repo root:  python test_need_deposit_button.py
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


# Captured from the live helper BEFORE the change. The wrong-link verdict must
# still produce exactly this.
BASELINE = {
    "text": "\U0001F511 Register & Get Access",
    "icon_custom_emoji_id": "5307843983102204243",
    "style": "success",
    "url": "url-placeholder",          # filled from config.REF_LINK below
}

EXPECTED_DEPOSIT = {
    "text": "Register & Get Access",
    "icon_custom_emoji_id": "5836690092306992715",
    "style": "success",
    "url": "url-placeholder",
}


def main():
    H._install_stub_modules()
    bot_mod = H._load_bot()

    ref = config.REF_LINK
    BASELINE["url"] = ref
    EXPECTED_DEPOSIT["url"] = ref

    def payload(rows):
        kb = bot_mod.build_kb(rows)
        return [b.model_dump(exclude_none=True)
                for row in kb.inline_keyboard for b in row]

    deposit = payload(bot_mod._register_btn(
        label="Register & Get Access", icon=config.E_NEED_DEP_REG))
    wrong = payload(bot_mod._register_btn())

    # --- the changed screen --------------------------------------------------
    print("[need_deposit] the Almost-there button")
    check("exactly one button", len(deposit) == 1, str(len(deposit)))
    if deposit:
        p = deposit[0]
        check("full payload is exactly as specified",
              p == EXPECTED_DEPOSIT, str(p))
        check("text is exactly 'Register & Get Access'",
              p.get("text") == "Register & Get Access", repr(p.get("text")))
        check("icon_custom_emoji_id is 5836690092306992715",
              p.get("icon_custom_emoji_id") == "5836690092306992715",
              repr(p.get("icon_custom_emoji_id")))
        check("label carries no unicode key emoji",
              "\U0001F511" not in p.get("text", ""), repr(p.get("text")))
        check("label is bare ASCII, so only one glyph renders",
              all(ord(c) < 128 for c in p.get("text", "")), repr(p.get("text")))
        check("style unchanged (success)",
              p.get("style") == "success", repr(p.get("style")))
        check("url unchanged (REF_LINK)", p.get("url") == ref, repr(p.get("url")))
        check("still a URL button, no callback_data",
              p.get("callback_data") is None, repr(p.get("callback_data")))
        check("the icon comes from the screen-scoped constant",
              config.E_NEED_DEP_REG == "5836690092306992715",
              config.E_NEED_DEP_REG)

    # --- the screen that must NOT have changed -------------------------------
    print("\n[wrong_link] the shared verdict is untouched")
    check("exactly one button", len(wrong) == 1, str(len(wrong)))
    if wrong:
        w = wrong[0]
        check("payload is byte-identical to the pre-change baseline",
              w == BASELINE, str(w))
        check("it still carries the original icon",
              w.get("icon_custom_emoji_id") == "5307843983102204243",
              repr(w.get("icon_custom_emoji_id")))
        check("it still carries the original label, key emoji and all",
              w.get("text") == "\U0001F511 Register & Get Access",
              repr(w.get("text")))
    check("the two verdicts now render different icons",
          deposit and wrong
          and deposit[0]["icon_custom_emoji_id"]
          != wrong[0]["icon_custom_emoji_id"],
          "icons are the same - the override did not take")
    check("but they still share URL and style",
          deposit and wrong
          and deposit[0]["url"] == wrong[0]["url"]
          and deposit[0]["style"] == wrong[0]["style"])

    # --- nothing else moved --------------------------------------------------
    print("\n[scope] nothing outside this button changed")
    check("the caption itself is unchanged",
          "Almost there." in config.MSG_NEED_DEPOSIT
          and "top up your balance" in config.MSG_NEED_DEPOSIT,
          repr(config.MSG_NEED_DEPOSIT[:60]))
    check("MSG_WRONG_LINK copy is unchanged",
          isinstance(config.MSG_WRONG_LINK, str) and config.MSG_WRONG_LINK,
          repr(config.MSG_WRONG_LINK[:40]))
    # The Premium/token "Almost there" screen is a DIFFERENT screen and has
    # only a Back button. It must not have gained one.
    check("the Premium 'Almost there' screen still has only Back",
          [b[1] for row in config.UNLOCK_KB for b in row] == ["cb:go:menu"],
          str(config.UNLOCK_KB))
    check("the Premium screen did not gain a Register button",
          not any("Register" in b[0] for row in config.UNLOCK_KB for b in row),
          str(config.UNLOCK_KB))
    # The register screen owns the same icon id; it must still have it.
    check("the register screen keeps its own Register button icon",
          config.SCREENS["register"]["kb"][0][0][3] == config.E_REG_BTN_REG,
          str(config.SCREENS["register"]["kb"][0][0]))
    check("register screen button label is unchanged",
          config.SCREENS["register"]["kb"][0][0][0] == "Register & Get Access",
          str(config.SCREENS["register"]["kb"][0][0]))
    check("REF_LINK is untouched", config.REF_LINK == ref, ref)

    print("\n%d checks, %d failed" % (CHECKS[0], len(FAILURES)))
    if FAILURES:
        for f in FAILURES:
            print("  FAILED: " + f)
        return 1
    print("PASS - only the deposit verdict's button was restyled.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
