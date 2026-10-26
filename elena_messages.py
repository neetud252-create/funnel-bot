"""Short chat bubbles. Trusted HTML links are kept intact.

Replies are split on blank lines and long sentences so they arrive as 2-3 short
messages (never one big block). At most 3 bubbles per reply.
"""
import re
from html import escape
import config
import elena_copy as copy

MAX_BUBBLES = 3
MAX_BUBBLE_CHARS = 180

# ---- first-touch copy (kept for compatibility) ----
NO_UID = 'Hey 👋 you still have 2 free Pocket IQ analyses — want to try them?'
UID_HELP = 'You haven’t submitted your Trader ID yet — takes 2 minutes. Need a hand?'
WRONG_UID = 'That Trader ID didn’t verify. Copy it straight from your Pocket Option profile and send it again.'
NEW_ACCOUNT = 'New here? Register through our bot link, then send your new Trader ID in Pocket IQ.'
EXISTING_ACCOUNT = 'Already on Pocket Option? Check with Pocket Option support before making a second account.'
DEPOSIT = 'Your account is linked 🎉 One step left: at least ${amount} in qualifying deposits unlocks full access.'
DEPOSIT_CHECK = 'Already deposited? Hit recheck in Pocket IQ so we confirm it. Need help with this step?'

# ---- escalation copy: touch 1..4 ----
INACTIVE_1 = 'Hey 👋 your 2 free analyses are ready — don’t leave them unused.'
INACTIVE_2 = 'Setup is 2 minutes: register through our link, then send your Trader ID in Pocket IQ.'
INACTIVE_3 = 'Quick one — full access just needs an account registered through our link. Let’s go 🚀'
INACTIVE_4 = 'Last nudge 👋 finish setup today and your free analyses are yours.'

UID_1 = 'Trader ID step giving you trouble? Copy the number straight from your Pocket Option profile.'
UID_2 = 'It’s the account registered through our link that unlocks full access — 2 minutes.'
UID_3 = 'Two minutes to fix it: use our registration link, then submit your new Trader ID in Pocket IQ.'
UID_4 = 'Last check 👋 submit your Trader ID and full access is one step away.'

DEPOSIT_1 = 'Heads up — add promo code {code} on Pocket Option for a {pct}% bonus on deposits of ${amount} or more.'
DEPOSIT_2 = 'A qualifying deposit is real money in your Pocket Option account, not a fee to us. Recheck once done.'
DEPOSIT_3 = 'With {code} your ${amount} becomes ${total} to trade — same risk, more capital to start.'
DEPOSIT_4 = 'Last reminder 👋 finish your qualifying deposit and full Pocket IQ access is yours.'

SOURCES = (NO_UID, UID_HELP, WRONG_UID, NEW_ACCOUNT, EXISTING_ACCOUNT, DEPOSIT, DEPOSIT_CHECK,
           INACTIVE_1, INACTIVE_2, INACTIVE_3, INACTIVE_4,
           UID_1, UID_2, UID_3, UID_4,
           DEPOSIT_1, DEPOSIT_2, DEPOSIT_3, DEPOSIT_4)

# delays in seconds per touch; index 0 == wait_seconds(reason) in elena.py
FOLLOWUP_DELAYS = {
    'inactive': [600, 1800, 3 * 3600, 24 * 3600, 48 * 3600],
    'uid':      [300, 3600, 6 * 3600, 24 * 3600, 48 * 3600],
    'deposit':  [900, 3600, 6 * 3600, 24 * 3600, 72 * 3600],
}


def _split_sentences(paragraph):
    """Group a long paragraph into <=180-char chunks on sentence boundaries."""
    parts = re.split(r'(?<=[.!?。！？…])\s+', paragraph.strip())
    out = []
    for part in filter(None, parts):
        if out and len(out[-1]) + len(part) + 1 <= MAX_BUBBLE_CHARS:
            out[-1] += ' ' + part
        else:
            out.append(part)
    return out


def bubbles(text):
    """Split a reply into at most 3 short bubbles.

    Blank lines force a new bubble (so the AI's 2-3 short lines arrive as
    separate messages); a long paragraph is broken on sentence boundaries.
    """
    text = (text or '').strip()
    if not text:
        return []
    chunks = []
    for paragraph in re.split(r'\n\s*\n', text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= MAX_BUBBLE_CHARS or '<' in paragraph:
            chunks.append(paragraph)
        else:
            chunks.extend(_split_sentences(paragraph))
    if len(chunks) <= MAX_BUBBLES:
        return chunks
    return chunks[:MAX_BUBBLES - 1] + [' '.join(chunks[MAX_BUBBLES - 1:])]


def _amount():
    return (format(config.MIN_DEPOSIT, 'f').rstrip('0').rstrip('.')
            if config.MIN_DEPOSIT % 1 else str(int(config.MIN_DEPOSIT)))


def _money(value):
    return (format(value, 'f').rstrip('0').rstrip('.') if value % 1 else str(int(value)))


def followup(user, reason, registration_url, bot_url, touch=0):
    language = user.get('language', 'en')
    tr = lambda source: copy.text(source, language)
    bot_link = '<a href="' + escape(bot_url, quote=True) + '">' + tr(copy.LINK_OPEN) + '</a>'
    reg_link = ('<a href="' + escape(registration_url, quote=True) + '">' + tr(copy.LINK_REGISTER) + '</a>'
                if registration_url else bot_link)
    bonus = copy.BONUS_MIN_DEPOSIT
    total = bonus + bonus * copy.BONUS_PERCENT // 100
    dep1 = lambda: tr(DEPOSIT_1).format(code=copy.PROMO_CODE, pct=copy.BONUS_PERCENT, amount=_money(bonus))

    if reason == 'inactive':
        if touch == 0:
            return [tr(NO_UID if user.get('free_analyses_remaining') == 2 else copy.HELLO),
                    tr(UID_HELP) + '\n\n' + bot_link]
        body = [INACTIVE_1, INACTIVE_2, INACTIVE_3, INACTIVE_4][min(touch, 4) - 1]
        return [tr(body), reg_link + '\n' + bot_link if touch % 2 else reg_link]

    if reason == 'uid':
        if touch == 0:
            if user.get('access_check_status') == config.VERIFY_INVALID_UID:
                return [tr(WRONG_UID), tr(copy.CHAT_UID) + '\n\n' + bot_link]
            if not registration_url or user.get('account_kind') == 'existing':
                return [tr(WRONG_UID), tr(copy.CHAT_EXISTING) + '\n\n' + bot_link]
            import journey
            return [tr(WRONG_UID), tr(NEW_ACCOUNT) + '\n' + reg_link + '\n' + tr(journey.DISCLOSURE),
                    tr(EXISTING_ACCOUNT) + '\n\n' + bot_link]
        body = [UID_1, UID_2, UID_3, UID_4][min(touch, 4) - 1]
        return [tr(body), reg_link + '\n' + bot_link if touch % 2 else bot_link]

    if reason == 'deposit':
        if touch == 0:
            return [tr(DEPOSIT).format(amount=_amount()), tr(DEPOSIT_CHECK) + '\n\n' + bot_link]
        body = {1: dep1, 2: lambda: tr(DEPOSIT_2), 3: lambda: tr(DEPOSIT_3).format(
            code=copy.PROMO_CODE, amount=_money(bonus), total=_money(total)), 4: lambda: tr(DEPOSIT_4)}[min(touch, 4)]
        return [body(), bot_link]
    return []
