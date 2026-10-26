"""Short chat bubbles. Trusted HTML links are kept intact.

Replies are split on blank lines and long sentences so they arrive as 2-3 short
messages (never one big block). At most 3 bubbles per reply, with light random
variation so the rhythm never feels scripted.

Follow-up copy applies persuasion levers: reciprocity, commitment, social proof,
authority, scarcity and loss aversion.
"""
import re
import random
from html import escape
import config
import elena_copy as copy

MAX_BUBBLES = 3
MAX_BUBBLE_CHARS = 180

# ---- first-touch copy (kept for compatibility) ----
NO_UID = 'Hey 👋 you’ve got 2 free Pocket IQ analyses waiting — they’re on us. Want them?'
UID_HELP = 'You’re about 2 minutes from done: register through our link, then drop your Trader ID in Pocket IQ.'
WRONG_UID = 'That Trader ID didn’t verify — easy fix. Copy it straight from your Pocket Option profile and resend.'
NEW_ACCOUNT = 'New here? Register through our link, then send your new Trader ID in Pocket IQ — most people finish in 2 minutes.'
EXISTING_ACCOUNT = 'Already on Pocket Option? Send your current Trader ID in Pocket IQ and I’ll check the link for you.'
DEPOSIT = 'Your account’s linked 🎉 One step left: at least ${amount} in qualifying deposits unlocks full access.'
DEPOSIT_CHECK = 'Already deposited? Hit recheck in Pocket IQ and I’ll confirm it — don’t leave it hanging.'

# ---- escalation copy: touch 1..4 ----
INACTIVE_1 = 'Hey 👋 most people finish this in 2 minutes — you’re nearly there.'
INACTIVE_2 = 'Your 2 free analyses are still unused. One quick step and they’re yours.'
INACTIVE_3 = 'Quick one — signals go out daily, don’t miss today’s. 2 minutes to set up.'
INACTIVE_4 = 'Last nudge 👋 finish today so your free analyses don’t go to waste.'

UID_1 = 'Trader ID giving you trouble? Copy it straight from your Pocket Option profile — 10 seconds.'
UID_2 = 'It’s only the account made through our link that unlocks full access. You’re one step away.'
UID_3 = 'Two minutes to fix it: use our link, then send the new Trader ID. Most people get it first try.'
UID_4 = 'Last check 👋 you’ve done the hard part — just resend the ID and you’re in.'

DEPOSIT_1 = 'Heads up — promo code {code} gives you a {pct}% bonus on deposits of ${amount}+. Same deposit, more capital.'
DEPOSIT_2 = 'A qualifying deposit is real money in YOUR Pocket Option account — not a fee paid to us.'
DEPOSIT_3 = 'With {code} your ${amount} becomes ${total} to trade. That’s free capital you’d otherwise miss.'
DEPOSIT_4 = 'Last reminder 👋 traders with access are taking signals right now — don’t leave yours locked.'

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
    """Split a reply into at most 3 short bubbles, with light variation.

    Blank lines force a new bubble (so the AI's 2-3 short lines arrive as
    separate messages); a long paragraph is broken on sentence boundaries. A
    small random merge keeps the shape from feeling mechanical.
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
    # Occasional variation: sometimes collapse the last two into one.
    if len(chunks) > 1 and random.random() < 0.25:
        merged = chunks[-2] + ' ' + chunks[-1]
        if len(merged) <= MAX_BUBBLE_CHARS + 60:
            chunks = chunks[:-2] + [merged]
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
            return [tr(WRONG_UID), tr(NEW_ACCOUNT) + '\n' + reg_link,
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
