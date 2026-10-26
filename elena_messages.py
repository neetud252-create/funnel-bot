"""Short chat bubbles. Trusted HTML links are kept intact."""
import re
from html import escape
import config
import elena_copy as copy

NO_UID = 'Hi! Want to try your two free Pocket IQ analyses today?'
UID_HELP = 'You haven’t submitted a Trader ID yet. Need a hand finding it or setting up your account?'
WRONG_UID = 'I couldn’t verify the Trader ID you submitted. Please check it against your Pocket Option profile.'
NEW_ACCOUNT = 'If you’re new to Pocket Option, register through our bot link, then send your new Trader ID in Pocket IQ.'
EXISTING_ACCOUNT = 'Already have an account? Check with Pocket Option support before creating another one.'
DEPOSIT = 'Your account is linked. Standard bot access requires at least ${amount} in qualifying deposits with Pocket Option.'
DEPOSIT_CHECK = 'Already deposited? Recheck your Trader ID in Pocket IQ so we can confirm it. Need help with this step?'
SOURCES = (NO_UID, UID_HELP, WRONG_UID, NEW_ACCOUNT, EXISTING_ACCOUNT, DEPOSIT, DEPOSIT_CHECK)


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


def followup(user, reason, registration_url, bot_url):
    language = user.get('language', 'en')
    tr = lambda source: copy.text(source, language)
    bot_link = '<a href="' + escape(bot_url, quote=True) + '">' + tr('Open Pocket IQ') + '</a>'
    if reason == 'inactive':
        return [tr(NO_UID if user.get('free_analyses_remaining') == 2 else copy.HELLO),
                tr(UID_HELP) + '\n\n' + bot_link]
    if reason == 'uid':
        if user.get('access_check_status') == config.VERIFY_INVALID_UID:
            return [tr(WRONG_UID), tr(copy.CHAT_UID) + '\n\n' + bot_link]
        if not registration_url or user.get('account_kind') == 'existing':
            return [tr(WRONG_UID), tr(copy.CHAT_EXISTING) + '\n\n' + bot_link]
        import journey
        referral = '<a href="' + escape(registration_url, quote=True) + '">' + tr('🔗 Registration Link') + '</a>'
        return [tr(WRONG_UID), tr(NEW_ACCOUNT) + '\n' + referral + '\n' + tr(journey.DISCLOSURE),
                tr(EXISTING_ACCOUNT) + '\n\n' + bot_link]
    if reason == 'deposit':
        amount = format(config.MIN_DEPOSIT, 'f').rstrip('0').rstrip('.') if config.MIN_DEPOSIT % 1 else str(int(config.MIN_DEPOSIT))
        return [tr(DEPOSIT).format(amount=amount), tr(DEPOSIT_CHECK) + '\n\n' + bot_link]
    return []
