"""Public /start welcome menu. Additional features are enabled separately."""
import config

PHOTO = 'home_banner'
PHOTO_EXT = 'png'
ANIMATION = 'home_banner_loop'

TEXT = (
    "👋 <b>Welcome to Go+ — Your Personal AI Trading Assistant!</b>\n\n"
    "🤖 Explore <b>AI-powered market analysis, trading signals, and easy-to-follow guides</b>"
    "—all in one place.\n\n"
    "⚡️ Get started below, view the guide, or contact our support team."
)


def keyboard():
    # SUPPORT accepts either a Telegram handle or a full URL.
    support = config.SUPPORT.strip()
    support = support if support.startswith(('https://', 'http://')) else 'https://t.me/' + support.lstrip('@')
    tips = config.FOREX_TIPS_URL.strip()
    tips_action = ('url:' + tips if tips and 'PLACEHOLDER' not in tips
                   else 'cb:home:tips')
    return [
        [('💎 Get Bot Access', 'cb:home:access', 'success')],
        [('📖 Guide', 'url:https://youtu.be/uJHBwXZVnNI?si=bhC7oMFLvoJfiQy', 'primary'),
         ('🎯 Test Signals', 'cb:home:test', 'primary')],
        [('⭐ Review', 'url:https://t.me/goplustrade_reviews', 'danger'),
         ('☎️ Support', 'url:' + support, 'danger')],
        [('▶️ YouTube', 'url:' + config.YOUTUBE_URL, 'primary'),
         ('💡 Forex Tips', tips_action, 'primary')],
        [('🌐 Change Language', 'cb:home:language', 'danger')],
    ]


PENDING = {
    'tips': 'Forex Tips will be added in a later update.',
}
