"""Trust-first Go Plus screens. Pure builders; account state stays in Postgres."""
from decimal import Decimal
from urllib.parse import urlsplit
import config

REGISTER_PHOTO = 'register_account'


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


def url(label, value, primary=False):
    valid = safe_url(value)
    return button(label, 'url:' + valid, 'success' if primary else None) if valid else cb(label, 'link_unavailable')


def support_url():
    value = config.SUPPORT.strip()
    return value if value.startswith(('http://', 'https://')) else 'https://t.me/' + value.lstrip('@')


def back(target='home'):
    return [[cb('⬅️ Back', target)]]


def dashboard_keyboard():
    return [
        [button('📸 Analyse My Chart', 'cb:menu:signal', 'success')],
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
REGISTRATION_INTRO = ('🆕 <b>Let’s set up your account first.</b>\n\n'
    'Create your Pocket Option account using our link, then return here to verify your Trader ID.\n\n'
    'Full access also requires qualifying deposits. You can read Access Requirements '
    'below before registering. We’ll show the deposit step after your Trader ID is confirmed.\n\n'
    + DISCLOSURE + '\n\nReady to continue?')
REQUIREMENTS = ('ℹ️ <b>Pocket IQ access requirements</b>\n\n'
    'Your account must be registered through our referral link and its Trader ID verified.\n\n'
    'Standard access: <b>{limit} analyses per day</b>.\n'
    'Required qualifying deposits: <b>${required}</b>.\n\n'
    'This is the Pocket IQ access requirement, not the broker’s minimum deposit. '
    'Payments remain in your broker account; Pocket IQ does not collect them.\n\n'
    + DISCLOSURE + '\n\nYou do not need to deposit before your Trader ID is checked.')
ELIGIBILITY = ('🌍 <b>Before you continue</b>\n\n'
    'Read the broker’s eligibility and restricted-location rules below. Only continue '
    'if you are 18 or older, meet the legal age requirement where you live, and '
    'the service is available to you under those rules.\n\n'
    + RISK + '\n\nYour confirmation is a self-declaration, not broker identity verification.')
REGISTER = ('🔗 <b>Let’s create your account.</b>\n\n'
    'Tap Create New Account and register on Pocket Option using our link.\n\n'
    'When you’re done, return here and tap <b>I’ve Created My Account</b>. '
    'We’ll ask for your Trader ID and check your access.\n\n'
    'Full access has a qualifying-deposit requirement. See Access Requirements below; '
    'we’ll guide you through that step after checking your Trader ID. ' + DISCLOSURE + '\n\n'
    'Already have an account? Use the existing-account path. Do not create a duplicate.')
EXISTING = ('🔗 <b>Create an account through the Pocket IQ bot link</b>\n\n'
    'Pocket IQ access requires an account registered through our referral link. '
    'If your current account wasn’t registered through this link, you need an eligible '
    'new registration to use this offer.\n\n'
    '<b>1.</b> Tap Create New Account below and register using the bot link.\n'
    '<b>2.</b> Return here, tap Verify My Trader ID, and send your new numeric UID.\n\n'
    'Already have an account? Confirm with Pocket Option that a replacement is allowed '
    'before registering. Its terms permit only one trading account per client.\n\n'
    'Full access also has a deposit requirement; see Access Requirements. '
    'The deposit step follows UID verification. ' + DISCLOSURE)
REFERRAL_HELP = ('🔗 <b>Let’s check your referral options.</b>\n\n'
    'Pocket IQ access requires registration through our referral link. '
    'Our bot cannot change the referral attached to an existing trading account.\n\n'
    'Ask support about your options, or check your current Trader ID if you’re unsure. '
    'Pocket Option’s rules allow only one trading account per client; do not create a duplicate.')
UID = ('🔍 <b>What is your Pocket Option Trader ID?</b>\n\n'
    'Send your Pocket Option Trader ID as a message here. It contains 5–15 digits.\n'
    'Example: <code>123456789</code>\n\n'
    'Never send a password, login code, recovery code, or payment details.')
DEPOSIT = ('✅ <b>Your Trader ID is confirmed under our link.</b>\n\n'
    '💳 <b>A deposit is needed to activate Pocket IQ access.</b>\n\n'
    'Minimum qualifying deposits for access: <b>${required}</b>\n'
    'Confirmed qualifying deposits: <b>${confirmed}</b>\n'
    'Remaining requirement: <b>${remaining}</b>\n\n'
    'To activate access, deposit <b>${remaining} or more</b> into the same linked '
    'Pocket Option account. Payments stay in your broker account. '
    'Depositing does not guarantee trading returns.\n\n'
    'After depositing, tap I’ve Deposited — Check Again. We will reuse your saved '
    'Trader ID. You can also finish later; your progress is saved.')
READY = ('✅ <b>Your Pocket IQ access is ready!</b>\n\n'
    'Your account and qualifying deposits are confirmed.\n\n'
    'What would you like to do first?')
PENDING = ('⏳ <b>We cannot confirm your status yet</b>\n\n'
    'The verification service is unavailable or still processing your request. '
    'This does not mean your deposit is missing. Please check again later or contact support.')
UNLINKED = ('👤 <b>We could not confirm access for this account</b>\n\n'
    'The account was not found under this referral offer. Check your ID or ask support. '
    'Do not open another trading account to change referral eligibility.')
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
    'how': ('📖 <b>How Go Plus works</b>\n\n'
        '1. Upload a clear chart screenshot.\n2. Read the trend, momentum, and analysis.\n'
        '3. Check the exact asset and chart interval yourself.\n4. Decide whether to act or wait.\n\n'
        'The bot analyses screenshots. It does not execute trades. The timer is the chart '
        'interval, not a guaranteed entry or expiry.\n\n' + RISK),
    'reviews': ('⭐ <b>Community reviews</b>\n\n'
        'Read posts in the existing Go Plus review channel. These are individual experiences, '
        'not independently audited proof of typical returns.\n\n' + RISK),
    'results': ('📊 <b>Signal results</b>\n\n'
        'A complete, verified performance record is not currently published here. '
        'We do not infer a win rate from selected screenshots.\n\n'
        'A useful record includes the original signals, timestamps, wins, losses, '
        'skipped setups, payouts, and measurement period.'),
    'community': '📢 <b>Go Plus community</b>\n\nExplore the channel and tutorials. Joining is optional.',
    'tools': '🧰 <b>Tools & FAQ</b>\n\nLearn the format, download the worksheet, or find an answer.',
    'support': ('💬 <b>How can we help?</b>\n\nChoose a topic or contact the Go Plus team. '
        'Never share passwords, OTPs, or full payment details.'),
    'account_help': ('👤 <b>Account help</b>\n\nYour Trader ID is in your Pocket Option profile. '
        'Existing accounts should be checked as they are; do not create a duplicate. '
        'Support can explain referral eligibility and access.'),
    'payment_help': ('💳 <b>Deposit & withdrawal help</b>\n\n'
        'Payment processing, fees, methods, identity checks, and withdrawals are handled by Pocket Option. '
        'Review the current terms in your broker account. Go Plus checks access; it cannot move your funds.\n\n'
        'For a payment problem, contact the broker through its official account interface.'),
    'analysis_help': ('📸 <b>Analysis help</b>\n\nSend one JPG, PNG, or WebP under 8 MB. '
        'Keep the asset, chart interval, and enough candle history visible. Crop out account details. '
        'Unreadable images and failed analyses do not use your trial allowance.'),
    'screenshot': ('📖 <b>Screenshot guide</b>\n\n'
        '• Show the asset name and market type.\n• Keep the candle interval visible.\n'
        '• Include recent and earlier candles.\n• Send one clear image under 8 MB.\n'
        '• Crop out your balance, account ID, and personal details.\n\n'
        'The example is an illustration, not live market data.'),
    'explain': ('📖 <b>Understand your result</b>\n\n'
        '<b>Trend:</b> the visible price direction.\n<b>Momentum:</b> the strength seen in the screenshot.\n'
        '<b>Prediction:</b> an uncertain interpretation, not a guarantee.\n'
        '<b>Timer:</b> the candle interval shown in the screenshot, not a trade expiry.\n\n'
        'A tentative result means the setup was not confirmed. Waiting is an option.\n\n' + RISK),
    'faq': ('❓ <b>Frequently asked questions</b>\n\n'
        '<b>Do I need a deposit to try it?</b> No. Two free lifetime chart analyses are available.\n\n'
        '<b>Does the bot trade for me?</b> No. It analyses your screenshot.\n\n'
        '<b>Are returns guaranteed?</b> No. Predictions can lose.\n\n'
        '<b>How is access checked?</b> Your Trader ID is checked against the referral offer '
        'and qualifying deposits. Clicking a link is not verification.\n\n'
        '<b>When do daily limits reset?</b> Midnight UTC.\n\n'
        '<b>Already registered?</b> Use your existing account and ask support about access.'),
    'risks': 'ℹ️ <b>Risks & referrals</b>\n\n' + RISK + '\n\n' + DISCLOSURE + '\n\n'
        'Only use a service where you are eligible. Read the broker’s payment, withdrawal, '
        'and bonus terms before depositing. Go Plus does not receive or hold your trading funds.',
    'privacy': ('🔒 <b>Your privacy</b>\n\n'
        'Go Plus stores your Telegram ID, selected language, setup progress, linked Trader ID, '
        'access checks, button activity, and usage in its database. Eligibility is self-declared.\n\n'
        'Chart screenshots and your caption are sent to the configured AI provider for analysis. '
        'Crop out personal details before uploading. Telegram and the provider apply their own policies.\n\n'
        'The bot does not need your trading password, login codes, or payment details. '
        'Contact support about privacy or deletion requests.'),
    'settings': '⚙️ <b>Settings</b>\n\nChoose your language or control promotional notifications.',
    'ineligible': ('🌍 <b>Please do not continue with registration</b>\n\n'
        'If you do not meet the age or location requirements, do not create or fund an account. '
        'You can read the guide or ask support about the rules.'),
    'link_unavailable': ('💬 <b>This link is not available</b>\n\n'
        'Please contact support for the current destination. No registration or payment has been completed.'),
}
SOURCES = (RISK, DISCLOSURE, ACCOUNT_QUESTION, ACCOUNT_UNSURE, REGISTRATION_INTRO, REQUIREMENTS,
           ELIGIBILITY, REGISTER, EXISTING, REFERRAL_HELP, UID, DEPOSIT, READY,
           PENDING, UNLINKED, PREMIUM, PREMIUM_SHORT, *TEXTS.values())


def screen(name, user=None):
    """Return text and rows. Every action points to a handled callback or checked URL."""
    user = user or {}
    help_button = cb('💬 Support', 'support')
    contact = url('💬 Contact Support', support_url(), True)
    rows = {
        'how': [[button('▶️ Watch Tutorial', 'cb:go:guide')],
                [button('📸 Analyse My Chart' if user.get('verified') else '📸 Try Free Analysis',
                        'cb:home:test', 'success')], [cb('❓ FAQs', 'faq')]],
        'reviews': [[url('⭐ Read Community Reviews', config.REVIEWS_URL, True)],
                    [cb('📊 Signal Results', 'results')], [cb('💬 Ask a Question', 'support')]],
        'results': [[cb('⭐ Community Reviews', 'reviews')], [help_button]],
        'community': [[url('📢 Telegram Channel', config.COMMUNITY_URL, True)],
                      [url('▶️ YouTube Channel', config.YOUTUBE_URL)], [cb('⭐ Reviews', 'reviews')]],
        'tools': [[button('📊 Money Management Sheet', 'cb:home:sheet')],
                  [cb('📖 Screenshot Guide', 'screenshot')], [cb('❓ FAQs', 'faq')],
                  [cb('ℹ️ Risks & Referral Info', 'risks')]],
        'support': [[cb('👤 Account Help', 'account_help')], [cb('💳 Deposit/Withdrawal Help', 'payment_help')],
                    [cb('📸 Analysis Help', 'analysis_help')], [contact]],
        'account_help': [[cb('🔍 Check My Existing Account', 'existing', True)],
                         [cb('📍 Where Is My Account ID?', 'id_guide')], [contact]],
        'payment_help': [[url('💳 Open Pocket Option Account', config.BROKER_ACCOUNT_URL, True)],
                         [url('📖 Payment & Withdrawal Terms', config.BROKER_PAYMENT_URL)], [contact]],
        'analysis_help': [[cb('📖 Screenshot Guide', 'screenshot')], [contact]],
        'screenshot': [[cb('🖼 See Example Screenshot', 'example')],
                       [button('📸 Upload Chart', 'cb:chart:upload', 'success')], [help_button]],
        'explain': [[button('📸 Analyse Another Chart', 'cb:chart:upload', 'success')], [help_button]],
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


def new_account():
    return REGISTRATION_INTRO, [
        [cb('🚀 Continue Setup', 'eligibility', True)],
        [cb('ℹ️ Access Requirements', 'requirements')],
        [cb('👤 I Already Have an Account', 'existing')],
        [cb('❓ Questions About Access', 'support')], *back('setup')]


def requirements():
    return REQUIREMENTS.format(limit=config.daily_limit(False), required=money(config.MIN_DEPOSIT)), [
        [button('🚀 Continue Setup', 'cb:home:access', 'success')],
        [cb('💬 Support', 'support')]]


def eligibility():
    return ELIGIBILITY, [[url('🌍 Read Eligibility & Restricted Locations', config.BROKER_TERMS_URL)],
        [cb('✅ I’m 18+ and Meet the Requirements', 'eligible', True)],
        [cb('I Do Not Meet the Requirements', 'ineligible')], *back('setup')]


def registration(ref):
    return REGISTER, [
        [url('🔗 Create New Account', ref, True)],
        [button('✅ I’ve Created My Account', 'cb:flow:enter_id', 'primary')],
        [url('▶️ Watch Registration Guide', config.SETUP_GUIDE_URL)],
        [cb('ℹ️ Access Requirements', 'requirements')],
        [cb('👤 I Already Have an Account', 'existing')], [cb('💬 Support', 'support'), cb('⬅️ Back', 'setup')]]


def existing(ref=None):
    register = url('🔗 Create New Account', ref, True) if ref else cb('🔗 Create New Account', 'register_existing', True)
    return EXISTING, [[register],
        [button('✅ Verify My Trader ID', 'cb:flow:enter_id', 'primary')],
        [cb('📍 Where Is My Trader ID?', 'id_guide')],
        [cb('ℹ️ Access Requirements', 'requirements')],
        [cb('💬 Support', 'support'), cb('⬅️ Back', 'setup')]]


def referral_help():
    return REFERRAL_HELP, [[url('💬 Contact Support', support_url(), True)],
        [cb('🔍 Verify My Trader ID', 'enter_id')],
        [url('📖 Read Broker Terms', config.BROKER_TERMS_URL)], *back('existing')]


def uid():
    return UID, [[cb('📍 Where Is My Trader ID?', 'id_guide')],
                 [cb('💬 Support', 'support'), cb('❌ Cancel', 'home')]]


def money(value):
    return f'{Decimal(str(value)):.2f}'


def deposit(amount):
    required = config.MIN_DEPOSIT
    return DEPOSIT.format(required=money(required), confirmed=money(amount),
        remaining=money(max(required - Decimal(str(amount)), Decimal(0)))), deposit_keyboard()


def deposit_keyboard(premium=False):
    return [[url('💳 Open Deposit Page', config.BROKER_ACCOUNT_URL, True)],
        [cb('✅ I’ve Deposited — Check Again', 'premium_check' if premium else 'recheck')],
        [cb('📖 Deposit Help', 'payment_help')],
        [cb('💬 Support', 'support'), cb('⏸ Keep Current Plan' if premium else '⏸ Finish Later', 'dashboard' if premium else 'home')]]


def ready():
    return READY, [
        [button('📸 Analyse My Chart', 'cb:menu:signal', 'success')],
        [cb('🏠 Open My Dashboard', 'dashboard')],
        [cb('📖 How It Works', 'how')]]


def pending(premium=False):
    return PENDING, [[cb('🔄 Check Again', 'premium_check' if premium else 'recheck', True)],
                     [cb('💬 Support', 'support'), cb('⏸ Later', 'dashboard' if premium else 'home')]]


def unlinked(premium=False):
    if premium:
        return UNLINKED, [[cb('🔄 Check My Linked Account Again', 'premium_check', True)],
                          [cb('💬 Ask Support About Access', 'support')], *back('dashboard')]
    return UNLINKED, [[cb('🔍 Check a Corrected ID', 'enter_id', True)],
                      [cb('💬 Ask Support About Access', 'support')], *back()]


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
