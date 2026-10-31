"""Trust-first Pocket IQ screens. Pure builders; account state stays in Postgres."""
from decimal import Decimal
from html import escape
from urllib.parse import urlsplit
import config

REGISTER_PHOTO = 'register_account'
RECOVERY_PHOTO = 'account_recovery'
DEPOSIT_PHOTO = 'deposit_account'


def button(label, action, style=None):
    return (label, action, style) if style else (label, action)


def cb(label, action, primary=False):
    return button(label, 'cb:flow:' + action, 'success' if primary else None)


def safe_url(value):
    try:
        parts = urlsplit((value or '').strip())
        if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
                or 'placeholder' in value.lower() or parts.hostname == 'example.com'):
            return None
        return value.strip()
    except (ValueError, AttributeError):
        return None


def url(label, value, primary=False, style=None):
    valid = safe_url(value)
    return button(label, 'url:' + valid, style or ('success' if primary else None)) if valid else cb(label, 'link_unavailable')


def support_url():
    value = config.SUPPORT.strip()
    return value if value.startswith(('http://', 'https://')) else 'https://t.me/' + value.lstrip('@')


def back(target='home'):
    return [[cb('⬅️ Back', target)]]


def dashboard_keyboard():
    return [
        [button('📊 Get Signals', 'cb:menu:signal', 'success')],
        [button('📺 Make Strategy', 'cb:strategy:open', 'primary')],
        [cb('👤 My Access & Usage', 'usage'), cb('📖 Guide', 'how')],
        [cb('🧰 Trading Tools', 'tools'), cb('💬 Support', 'support')],
        [cb('📢 Community', 'community'), cb('⚙️ Settings', 'settings')],
    ]


RISK = ('AI predictions can be wrong. You can lose your entire stake. '
        'Past results and testimonials do not guarantee future returns.')
DISCLOSURE = 'We may earn a commission if you use our referral link.'
ACCOUNT_QUESTION = ('👋 <b>Let’s set up your Pocket IQ access.</b>\n\n'
    'Do you already have a Pocket Option account?')
ACCOUNT_UNSURE = ('❓ <b>Let’s help you check.</b>\n\n'
    'Have you registered on Pocket Option before, even just to use its demo account? '
    'If so, choose Yes and use that account.\n\n'
    'If you cannot remember, check your registration emails or use the broker’s '
    'official account recovery. Support can help explain the next step.')
REQUIREMENTS = ('ℹ️ <b>Pocket IQ account requirement</b>\n\n'
    'To continue Pocket IQ setup, you must create a <b>new Pocket Option account '
    'through our bot link</b>.\n\n'
    'After registering, return here and send your <b>Pocket Option UID</b> '
    'to verify your account.\n\n'
    'Already have an account? Contact <b>Support</b> before creating another.\n\n'
    '<i>' + DISCLOSURE + '</i>')
REGISTER = ('🔑 <b>Let’s activate your Pocket IQ access!</b>\n\n'
    '1️⃣ Register for a <b>new Pocket Option account</b> using our bot link below:\n\n'
    '👉 {ref}\n\n'
    '2️⃣ Send your <b>new account ID (UID)</b> in this chat below 👇\n\n'
    '❗ Your ID must contain <b>numbers only</b>, without spaces or symbols.\n\n'
    'Example: <code>123456789</code>')
EXISTING = REGISTER
REFERRAL_HELP = ('🔗 <b>Let’s check your referral options.</b>\n\n'
    'Pocket IQ access requires registration through our referral link. '
    'Our bot cannot change the referral attached to an existing trading account.\n\n'
    'Ask support about your options, or check your current Trader ID if you’re unsure. '
    'Pocket Option’s rules allow only one trading account per client; do not create a duplicate.')
UID = ('🔍 <b>What is your Pocket Option Trader ID?</b>\n\n'
    'Send your Pocket Option Trader ID as a message here. It contains 5–15 digits.\n'
    'Example: <code>123456789</code>\n\n'
    'Never send a password, login code, recovery code, or payment details.')
DEPOSIT = ('✅ <b>Great! Your Pocket IQ registration is complete!</b>\n\n'
    '🧾 Just one step left — make a <b>deposit of at least ${required}</b> into your Pocket Option account.\n\n'
    '🎁 Use promo code YEB789 to get a 50% bonus on your first deposit.\n\n'
    '🚀 Once your deposit is confirmed, Pocket IQ will be ready to activate.\n\n'
    '👇 Already deposited? Click the <b>“I’ve deposited”</b> button below.')
# Temporary preview for administrators only; never advertise invented stock to customers.
DEPOSIT_TEST_URGENCY = ('⚠️ There are only <b>1 activations</b> available for today. '
    'Hurry up and activate the bot 🚀')
DEPOSIT_HELP = ('💳 <b>How to Deposit Funds</b>\n\n'
    '1️⃣ Open your linked Pocket Option account and select <b>Deposit</b>.\n\n'
    '2️⃣ Enter your deposit amount and promo code <code>YEB789</code>. Check the bonus terms shown in your account.\n\n'
    '3️⃣ Choose an available payment method and follow the instructions.\n\n'
    'Return to Pocket IQ and tap <b>“I’ve deposited”</b> to check your saved account ID.')
READY = ('✅ <b>Your Pocket IQ access is ready!</b>\n\n'
    'Your account and qualifying deposits are confirmed.\n\n'
    'What would you like to do first?')
PENDING = ('⏳ <b>We cannot confirm your status yet</b>\n\n'
    'The verification service is unavailable or still processing your request. '
    'This does not mean your deposit is missing. Please check again later or contact support.')
UNLINKED = ('👤 <b>We could not confirm access for this account</b>\n\n'
    'The account was not found under this referral offer. Check your ID or ask support. '
    'Do not open another trading account to change referral eligibility.')
REGISTRATION_RECOVERY = ('❌ <b>Let’s fix your Pocket IQ registration!</b>\n\n'
    'Your Pocket Option account wasn’t registered using my link.\n\n'
    '<tg-emoji emoji-id="5382322671679708881">1️⃣</tg-emoji> Delete your current account by following the instructions above.\n\n'
    '<tg-emoji emoji-id="5381990043642502553">2️⃣</tg-emoji> Register for a new Pocket Option account using the link below:\n\n'
    '<tg-emoji emoji-id="5436166670919953550">➡️</tg-emoji> {ref}\n\n'
    '<tg-emoji emoji-id="5381879959335738545">3️⃣</tg-emoji> <b>Enter your new account ID</b> in the field below 👇\n\n'
    '⚠️ Your ID must contain numbers only, without spaces or symbols.\n\n'
    'Example: 123456789')
PREMIUM = ('👑 <b>Premium access</b>\n\n'
    'Standard: <b>{standard} analyses/day</b>.\n'
    'Premium: <b>{premium} analyses/day</b>.\n'
    'Premium requires <b>${required}</b> in total qualifying deposits on your linked account.\n\n'
    'This is a higher usage allowance, not a promise of better predictions or returns. '
    'You can keep your current plan.')
PREMIUM_SHORT = ('👑 <b>Premium requirement not met yet</b>\n\n'
    'Required qualifying deposits: <b>${required}</b>\n'
    'Confirmed qualifying deposits: <b>${confirmed}</b>\n'
    'Remaining requirement: <b>${remaining}</b>\n\n'
    'You can keep your current plan. Depositing does not guarantee trading returns.')
TEXTS = {
    'how': ('📖 <b>How Pocket IQ works</b>\n\n'
        '1. Choose OTC or FIN, then an asset category.\n2. Select an asset and expiration time (S5–S55 / M1–M10).\n'
        '3. View its chart, trend, and momentum when live data is available.\n4. Decide whether to act or wait.\n\n'
        'Assets without connected data remain browsable. The bot does not execute trades. '
        'Analysis uses completed M1 candles; your chosen expiration is shown separately.\n\n' + RISK),
    'reviews': ('⭐ <b>Community reviews</b>\n\n'
        'Read posts in the existing Pocket IQ review channel. These are individual experiences, '
        'not independently audited proof of typical returns.\n\n' + RISK),
    'results': ('📊 <b>Signal results</b>\n\n'
        'A complete, verified performance record is not currently published here. '
        'We do not infer a win rate from selected screenshots.\n\n'
        'A useful record includes the original signals, timestamps, wins, losses, '
        'skipped setups, payouts, and measurement period.'),
    'community': '📢 <b>Pocket IQ community</b>\n\nExplore the channel and tutorials. Joining is optional.',
    'tools': '🧰 <b>Tools & FAQ</b>\n\nLearn the format, download the worksheet, or find an answer.',
    'support': ('💬 <b>How can we help?</b>\n\nChoose a topic or contact the Pocket IQ team. '
        'Never share passwords, OTPs, or full payment details.'),
    'account_help': ('👤 <b>Account help</b>\n\nYour Trader ID is in your Pocket Option profile. '
        'Existing accounts should be checked as they are; do not create a duplicate. '
        'Support can explain referral eligibility and access.'),
    'payment_help': ('💳 <b>Deposit & withdrawal help</b>\n\n'
        'Payment processing, fees, methods, identity checks, and withdrawals are handled by Pocket Option. '
        'Review the current terms in your broker account. Pocket IQ checks access; it cannot move your funds.\n\n'
        'For a payment problem, contact the broker through its official account interface.'),
    'analysis_help': ('📊 <b>Analysis help</b>\n\n'
        'Choose a market, category, and asset, then select an expiration from S5 to M10. '
        'The bot loads completed candles and generates a chart for analysis.\n\n'
        'If live data is unavailable, choose another asset or try later. '
        'Unavailable data and failed analyses do not use your allowance.'),
    'screenshot': ('📖 <b>Asset guide</b>\n\n'
        '1. Open Choose Asset or send /assets.\n'
        '2. Select OTC or FIN and a category.\n'
        '3. Browse with ‹ and ›, or type an asset name to search.\n'
        '4. Select an asset with connected data and choose an expiration from S5 to M10.\n\n'
        'You will receive a chart and BUY or SELL analysis with your chosen expiration. '
        'Incomplete confirmation is marked tentative.'),
    'explain': ('📖 <b>Understand your result</b>\n\n'
        '<b>Trend:</b> the visible price direction.\n<b>Momentum:</b> the strength seen in the completed candles.\n'
        '<b>Prediction:</b> an uncertain interpretation, not a guarantee.\n'
        '<b>Expiration:</b> your selected duration in seconds or minutes. Chart candles are shown separately.\n'
        '<b>Tentative:</b> confirmation is incomplete or evidence is mixed.\n\n'
        'A tentative result means the setup was not confirmed. Waiting is an option.\n\n' + RISK),
    'faq': ('❓ <b>Frequently asked questions</b>\n\n'
        '<b>Do I need a deposit to try it?</b> No. The demo includes 1 free lifetime signal on assets with connected data. Follow the account setup instructions for full access.\n\n'
        '<b>Does the bot trade for me?</b> No. It analyses market candles and shows a chart.\n\n'
        '<b>How do I get started?</b> Tap Try Demo and choose your market, asset, and expiration time.\n\n'
        '<b>When do daily limits reset?</b> Midnight UTC.\n\n'
        '<b>Already registered?</b> Use your existing account and ask support about access.'),
    'risks': 'ℹ️ <b>Risks & referrals</b>\n\n' + RISK + '\n\n' + DISCLOSURE + '\n\n'
        'Only use a service where you are eligible. Read the broker’s payment, withdrawal, '
        'and bonus terms before depositing. Pocket IQ does not receive or hold your trading funds.',
    'privacy': ('🔒 <b>Your privacy</b>\n\n'
        'Pocket IQ stores your Telegram ID, selected language, setup progress, linked Trader ID, '
        'access checks, button activity, and usage in its database.\n\n'
        'The selected asset, candle data, and generated chart are sent to the configured AI provider for analysis. '
        'Telegram and the provider apply their own policies.\n\n'
        'The bot does not need your trading password, login codes, or payment details. '
        'Contact support about privacy or deletion requests.'),
    'settings': '⚙️ <b>Settings</b>\n\nChoose your language or control promotional notifications.',
    'ineligible': ('🌍 <b>Please do not continue with registration</b>\n\n'
        'If you do not meet the age or location requirements, do not create or fund an account. '
        'You can read the guide or ask support about the rules.'),
    'link_unavailable': ('💬 <b>This link is not available</b>\n\n'
        'Please contact support for the current destination. No registration or payment has been completed.'),
}
SOURCES = (RISK, DISCLOSURE, ACCOUNT_QUESTION, ACCOUNT_UNSURE, REQUIREMENTS,
           REGISTER, EXISTING, REFERRAL_HELP, UID, DEPOSIT, DEPOSIT_TEST_URGENCY, DEPOSIT_HELP, READY,
           PENDING, UNLINKED, REGISTRATION_RECOVERY, PREMIUM, PREMIUM_SHORT, *TEXTS.values())


def screen(name, user=None):
    """Return text and rows. Every action points to a handled callback or checked URL."""
    user = user or {}
    help_button = cb('💬 Support', 'support')
    contact = url('💬 Contact Support', support_url(), True)
    rows = {
        'how': [[button('▶️ Watch Tutorial', 'cb:go:guide')],
                [button('📊 Get Signals' if user.get('verified') else '🎯 Try Demo',
                        'cb:home:test', 'success')], [cb('❓ FAQs', 'faq')]],
        'reviews': [[url('⭐ Read Community Reviews', config.REVIEWS_URL, True)],
                    [cb('📊 Signal Results', 'results')], [cb('💬 Ask a Question', 'support')]],
        'results': [[cb('⭐ Community Reviews', 'reviews')], [help_button]],
        'community': [[url('📢 Telegram Channel', config.COMMUNITY_URL, True)],
                      [url('▶️ YouTube Channel', config.YOUTUBE_URL)], [cb('⭐ Reviews', 'reviews')]],
        'tools': [[button('📊 Money Management Sheet', 'cb:home:sheet')],
                  [cb('📖 Asset Guide', 'screenshot')], [cb('❓ FAQs', 'faq')]],
        'support': [[cb('👤 Account Help', 'account_help')], [cb('💳 Deposit/Withdrawal Help', 'payment_help')],
                    [cb('📊 Analysis Help', 'analysis_help')], [contact]],
        'account_help': [[cb('🔍 Check My Existing Account', 'existing', True)],
                         [cb('📍 Where Is My Account ID?', 'id_guide')], [contact]],
        'payment_help': [[url('💳 Open Pocket Option Account', config.BROKER_ACCOUNT_URL, True)],
                         [url('📖 Payment & Withdrawal Terms', config.BROKER_PAYMENT_URL)], [contact]],
        'analysis_help': [[cb('📖 Asset Guide', 'screenshot')], [contact]],
        'screenshot': [[button('📊 Choose Asset', 'cb:market:open', 'success')], [help_button]],
        'explain': [[button('📊 Choose Another Asset', 'cb:market:open', 'success')], [help_button]],
        'faq': [[cb('🚀 View Access Requirements', 'requirements', True)], [help_button]],
        'risks': [[url('📖 Read Broker Terms', config.BROKER_TERMS_URL)], [help_button]],
        'privacy': [[contact]],
        'settings': [[button('🌐 Language', 'cb:home:language')],
                     [cb('🔔 Notification Preferences', 'notifications')], [cb('🔒 Privacy', 'privacy')]],
        'ineligible': [[cb('📖 Keep Learning', 'how')], [help_button]],
        'link_unavailable': [[contact]],
    }
    return TEXTS[name], rows[name] + back('dashboard' if user.get('verified') else 'home')


def setup():
    return ACCOUNT_QUESTION, [
        [cb('👤 Yes, I Have an Account', 'existing')],
        [cb('🆕 No, I’m New', 'new')],
        [cb('❓ I’m Not Sure', 'not_sure')], *back()]


def account_help():
    return ACCOUNT_UNSURE, [
        [cb('👤 Yes, I Have an Account', 'existing')],
        [cb('🆕 No, I’m New', 'new')],
        [cb('💬 Support', 'support')], *back('setup')]


def requirements():
    return REQUIREMENTS, [
        [button('🚀 Continue Setup', 'cb:home:access', 'success')],
        [cb('💬 Support', 'support')]]


def referral_link(ref):
    valid = safe_url(ref)
    if valid:
        # Keep long tracking URLs clickable without overflowing Telegram's caption.
        label = valid if len(valid) <= 120 else 'Open Pocket Option registration'
        link = f'<a href="{escape(valid, quote=True)}">{escape(label)}</a>'
    else:
        link = 'Link unavailable — tap Support below.'
    return link


def registration(ref):
    return REGISTER.format(ref=referral_link(ref)), [
        [url('🔗 Register & Get Access', ref, True)],
        [url('📖 How to register', config.SETUP_GUIDE_URL, style='primary')],
        [url('💬 Support', support_url(), style='primary')]]


def existing(ref=None):
    return registration(ref)


def referral_help():
    return REFERRAL_HELP, [[url('💬 Contact Support', support_url(), True)],
        [cb('🔍 Verify My Trader ID', 'enter_id')],
        [url('📖 Read Broker Terms', config.BROKER_TERMS_URL)], *back('existing')]


def registration_button(ref=None):
    return (url('🔗 Registration Link', ref, True) if ref is not None
            else cb('🔗 Registration Link', 'register_again', True))


def uid(ref=None):
    return UID, [[registration_button(ref)], [cb('📍 Where Is My Trader ID?', 'id_guide')],
                 [cb('💬 Support', 'support'), cb('❌ Cancel', 'home')]]


def money(value):
    return f'{Decimal(str(value)):.2f}'


def deposit(amount):
    required = config.MIN_DEPOSIT
    display = str(int(required)) if required == required.to_integral_value() else money(required)
    return DEPOSIT.format(required=display), deposit_keyboard()


def deposit_keyboard(premium=False):
    if not premium:
        return [[url('💳 Make a deposit', config.BROKER_ACCOUNT_URL, style='primary')],
            [button('📖 How to Deposit Funds', 'cb:flow:deposit_guide', 'primary')],
            [url('💬 Support', support_url(), style='primary')],
            [cb('✅ I’ve deposited', 'recheck', True)]]
    return [[url('💳 Open Deposit Page', config.BROKER_ACCOUNT_URL, True)],
        [cb('✅ I’ve Deposited — Check Again', 'premium_check' if premium else 'recheck')],
        [cb('📖 Deposit Help', 'payment_help')],
        [cb('💬 Support', 'support'), cb('⏸ Keep Current Plan' if premium else '⏸ Finish Later', 'dashboard' if premium else 'home')]]


def ready():
    return READY, [
        [button('📊 Get Signals', 'cb:menu:signal', 'success')],
        [cb('🏠 Open My Dashboard', 'dashboard')],
        [cb('📖 How It Works', 'how')]]


def pending(premium=False):
    return PENDING, [[cb('🔄 Check Again', 'premium_check' if premium else 'recheck', True)],
                     [cb('💬 Support', 'support'), cb('⏸ Later', 'dashboard' if premium else 'home')]]


def unlinked(premium=False, ref=None):
    if premium:
        return UNLINKED, [[cb('🔄 Check My Linked Account Again', 'premium_check', True)],
                          [cb('💬 Ask Support About Access', 'support')], *back('dashboard')]
    return REGISTRATION_RECOVERY.format(ref=referral_link(ref)), [
        [url('🔗 Register & Get Access', ref, True)],
        [url('📖 How to register', config.SETUP_GUIDE_URL, style='primary')],
        [url('💬 Support', support_url(), style='primary')]]


def premium(amount=None):
    text = PREMIUM.format(standard=config.daily_limit(False), premium=config.daily_limit(True),
                          required=money(config.PREMIUM_MIN_DEPOSIT))
    if amount is not None:
        text = PREMIUM_SHORT.format(required=money(config.PREMIUM_MIN_DEPOSIT), confirmed=money(amount),
            remaining=money(max(config.PREMIUM_MIN_DEPOSIT - Decimal(str(amount)), Decimal(0))))
    return text, [[cb('📖 Compare Access Levels', 'compare')], *deposit_keyboard(True)]


def notifications(user):
    enabled = not user.get('broadcast_opt_out', False)
    text = ('🔔 <b>Notification preferences</b>\n\n'
            + ('Updates and setup reminders are on.' if enabled else 'Updates and setup reminders are off.')
            + '\n\nChanging this does not affect bot access or replies you request.')
    return text, [[cb('🔕 Turn Off Updates' if enabled else '🔔 Turn On Updates',
                      'notifications_off' if enabled else 'notifications_on', True)], *back('settings')]
