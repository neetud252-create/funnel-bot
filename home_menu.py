"""Public /start menu: one looping animation with welcome caption and buttons."""
import config
import journey

PHOTO = 'home_banner'
PHOTO_EXT = 'png'
ANIMATION = 'home_banner_loop'
# Separate Telegram cache key: video.file_id cannot reuse animation.file_id.
VIDEO = 'home_banner_video'

TEXT = (
    "👋 <b>Welcome to Pocket IQ</b>\n\n"
    "Upload a chart screenshot and explore <b>two free analyses</b> before deciding "
    "whether to set up access.\n\n"
    "See how it works, read community feedback, or speak to support.\n\n"
    "<i>Predictions can be wrong. Returns are not guaranteed.</i>"
)


SHEET_CAPTION = "📊 <b>Money Management Sheet</b>\n\nDownload your Excel sheet below."
SHEET_UNAVAILABLE = "The money management sheet is temporarily unavailable. Please try again shortly or contact support."


def _link(label, address, style):
    """Keep the configured destination or the existing unavailable-link help."""
    button = journey.url(label, address)
    return (button[0], button[1], style)


def keyboard(user=None):
    verified = bool((user or {}).get('verified'))
    access = '🏠 My Dashboard' if verified else '💎 Get Bot Access'
    trial = '📸 Analyse My Chart' if verified else '🎯 Test Signals'
    return [
        [(access, 'cb:home:access', 'success')],
        [('📖 Guide', 'cb:flow:how', 'primary'), (trial, 'cb:home:test', 'primary')],
        [_link('⭐ Review', config.REVIEWS_URL, 'danger'),
         _link('💬 Support', journey.support_url(), 'danger')],
        [_link('▶️ YouTube', config.YOUTUBE_URL, 'primary'),
         _link('📢 Channel', config.COMMUNITY_URL, 'primary')],
        [('📊 Money Management Sheet', 'cb:home:sheet', 'primary')],
        [('🌐 Change Language', 'cb:home:language', 'danger')],
    ]


PENDING = {
    'tips': 'Forex Tips will be added in a later update.',
}
