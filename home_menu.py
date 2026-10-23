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
    "Your AI-powered chart analysis assistant.\n\n"
    "📊 <b>Choose an asset. Explore the setup.</b>\n"
    "Select your market, asset, and timeframe to view a candlestick chart and market analysis when live data is available.\n\n"
    "🎁 <b>Your first two analyses are free.</b>\n"
    "Try Pocket IQ before deciding whether to set up access.\n\n"
    "📖 New here? Open <b>How It Works</b>, or reach out to <b>Support</b> whenever you need help.\n\n"
    "👇 <b>Tap “Try 2 Free Analyses” to get started.</b>"
)


SHEET_CAPTION = "📊 <b>Money Management Sheet</b>\n\nDownload your Excel sheet below."
SHEET_UNAVAILABLE = "The money management sheet is temporarily unavailable. Please try again shortly or contact support."


def keyboard(user=None):
    user = user or {}
    access = ('🏠 Open My Dashboard' if user.get('verified') else
              '🚀 Continue Setup' if user.get('uid') or user.get('setup_stage') not in (None, 'new')
              else '🚀 Set Up My Account')
    trial = '📊 Get Signals' if user.get('verified') else '🎯 Try 2 Free Analyses'
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
