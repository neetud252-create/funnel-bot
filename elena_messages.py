"""Short chat bubbles. Trusted HTML links are kept intact."""
import re
from html import escape
import config
import elena_copy as copy

# ---- first-touch copy (kept for compatibility) ----
NO_UID = 'Hi! Want to try your two free Pocket IQ analyses today?'
UID_HELP = 'You haven’t submitted a Trader ID yet. Need a hand finding it or setting up your account?'
WRONG_UID = 'I couldn’t verify the Trader ID you submitted. Please check it against your Pocket Option profile.'
NEW_ACCOUNT = 'If you’re new to Pocket Option, register through our bot link, then send your new Trader ID in Pocket IQ.'
EXISTING_ACCOUNT = 'Already have an account? Check with Pocket Option support before creating another one.'
DEPOSIT = 'Your account is linked. Standard bot access requires at least ${amount} in qualifying deposits with Pocket Option.'
DEPOSIT_CHECK = 'Already deposited? Recheck your Trader ID in Pocket IQ so we can confirm it. Need help with this step?'

# ---- escalation copy: touch 1..4 ----
INACTIVE_1 = 'Still with us? Your two free Pocket IQ analyses are ready whenever you are.'
INACTIVE_2 = 'Setup takes about two minutes: register through our link, then send your Trader ID in Pocket IQ.'
INACTIVE_3 = 'Quick reminder — full access needs a Pocket Option account registered through our bot link.'
INACTIVE_4 = 'Last nudge 👋 Finish setup today and your free analyses are waiting.'

UID_1 = 'Did the Trader ID step give you trouble? Try copying the number straight from your Pocket Option profile.'
UID_2 = 'If you already had a Pocket Option account, it’s an account registered through our link that unlocks full access.'
UID_3 = 'Two minutes to fix it: use our registration link, then submit the new Trader ID in Pocket IQ.'
UID_4 = 'Last check 👋 Submit your Trader ID in Pocket IQ and full access is one step away.'

DEPOSIT_1 = 'Heads up — you can add promo code {code} on Pocket Option for a {pct}% bonus on deposits of ${amount} or more.'
DEPOSIT_2 = 'A qualifying deposit is real money in your Pocket Option account, not a fee paid to us. Recheck in Pocket IQ once done.'
DEPOSIT_3 = 'With {code} your ${amount} becomes ${total} to trade. Same risk, more capital to start.'
DEPOSIT_4 = 'Last reminder 👋 Complete your qualifying deposit and full Pocket IQ access is yours.'

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


def bubbles(text):
    """One short greeting; at most three bubbles for a longer explanation."""
    parts = []
    for paragraph in re.split(r'\n\s*\n', text.strip()):
        if len(paragraph) > 180 and '<' not in paragraph:
            parts.extend(re.split(r'(?<=[.!?。])\s+', paragraph))
        else:
            parts.append(paragraph)
    result = []
    for part in filter(None, parts):
        if result and len(result[-1]) + len(part) < 180:
            result[-1] += '\n\n' + part
        else:
            result.append(part)
    return result[:2] + ['\n\n'.join(result[2:])] if len(result) > 3 else result


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
