"""Public /start menu: one video card containing the welcome caption and buttons."""
import config

PHOTO = 'home_banner'
PHOTO_EXT = 'png'
ANIMATION = 'home_banner_loop'
# Separate Telegram cache key: video.file_id cannot reuse animation.file_id.
VIDEO = 'home_banner_video'

TEXT = (
    "👋 <b>Welcome to Go+ — Your Personal AI Trading Assistant!</b>\n\n"
    "🤖 Explore <b>AI-powered market analysis, trading signals, and easy-to-follow guides</b>"
    "—all in one place.\n\n"
    "⚡️ Get started below, view the guide, or contact our support team."
)


SHEET_CAPTION = "📊 <b>Money Management Sheet</b>\n\nDownload your Excel sheet below."
SHEET_UNAVAILABLE = "The money management sheet is temporarily unavailable. Please try again shortly or contact support."


def keyboard():
    # SUPPORT accepts either a Telegram handle or a full URL.
    support = config.SUPPORT.strip()
    support = support if support.startswith(('https://', 'http://')) else 'https://t.me/' + support.lstrip('@')
    return [
        [('💎 Get Bot Access', 'cb:home:access', 'success')],
        [('📖 Guide', 'cb:go:guide', 'primary'),
         ('🎯 Test Signals', 'cb:home:test', 'primary')],
        [('⭐ Review', 'url:https://t.me/goplustrade_reviews', 'danger'),
         ('☎️ Support', 'url:' + support, 'danger')],
        [('▶️ YouTube', 'url:' + config.YOUTUBE_URL, 'primary'),
         ('📢 Channel', 'url:https://t.me/goplusaitrader', 'primary')],
        [('📊 Money Management Sheet', 'cb:home:sheet', 'primary')],
        [('🌐 Change Language', 'cb:home:language', 'danger')],
    ]


PENDING = {
    'tips': 'Forex Tips will be added in a later update.',
}
