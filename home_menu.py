"""Public /start menu: one animated video with welcome caption and buttons."""
import config

PHOTO = 'home_banner'
PHOTO_EXT = 'png'
ANIMATION = 'home_banner_loop'
# Separate Telegram cache key: video.file_id cannot reuse animation.file_id.
# iOS caps GIF cards at image width; a video can fill the available bubble width.
VIDEO = 'home_banner_video'

TEXT = (
    "👋 <b>Welcome to Pocket IQ</b>\n"
    "Your AI-powered chart analysis assistant.\n"
    "📸 Turn a clear chart screenshot into easy-to-follow market analysis.\n"
    "🎁 <b>Explore two free analyses</b> before deciding whether to set up access.\n"
    "📖 Start with <b>Guide</b>, read community <b>Reviews</b>, or speak to <b>Support</b> whenever you need help.\n"
    "👇 Tap <b>Test Signals</b> to try your first analysis."
)


SHEET_CAPTION = "📊 <b>Money Management Sheet</b>\n\nDownload your Excel sheet below."
SHEET_UNAVAILABLE = "The money management sheet is temporarily unavailable. Please try again shortly or contact support."


def keyboard(user=None):
    user = user or {}
    access = ('🏠 Open My Dashboard' if user.get('verified') else
              '🚀 Continue Setup' if user.get('uid') or user.get('setup_stage') not in (None, 'new')
              else '🚀 Set Up My Account')
    trial = '📸 Analyse My Chart' if user.get('verified') else '🎯 Try 2 Free Analyses'
    return [
        [(trial, 'cb:home:test', 'success')],
        [(access, 'cb:home:access', 'primary')],
        [('📖 How It Works', 'cb:flow:how', 'primary'), ('📢 Community', 'cb:flow:community', 'primary')],
        [('⭐ Reviews', 'cb:flow:reviews', 'danger'), ('💬 Support', 'cb:flow:support', 'danger')],
        [('🧰 Tools & FAQ', 'cb:flow:tools', 'primary')],
        [('🌐 Language', 'cb:home:language', 'danger')],
    ]


PENDING = {
    'tips': 'Forex Tips will be added in a later update.',
}
