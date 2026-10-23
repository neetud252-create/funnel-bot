"""Persistent Start Here card, separate from the full home menu."""
import hashlib
import json
import emoji_theme


TEXT = (
    "📌 <b>POCKET IQ — START HERE</b>\n\n"
    "Your AI-powered chart analysis assistant.\n\n"
    "🎯 <b>Explore first</b>\n"
    "Try two free analyses by uploading a clear chart screenshot.\n\n"
    "📖 <b>Understand the results</b>\n"
    "Open the guide to learn how the bot works.\n\n"
    "🔓 <b>Continue when you’re ready</b>\n"
    "Account Setup explains registration, access requirements, and deposit verification.\n\n"
    "💬 <b>Need help?</b>\n"
    "Use the official Support button below."
)


def keyboard(user=None):
    verified = bool((user or {}).get('verified'))
    return [
        [('📸 Analyse My Chart' if verified else '🎯 Try Free Analysis', 'cb:home:test', 'success')],
        [('🏠 My Dashboard' if verified else '🚀 Account Setup', 'cb:home:access', 'primary')],
        [('📖 How It Works', 'cb:flow:how', 'primary'), ('📢 Community', 'cb:flow:community', 'primary')],
        [('⭐ Reviews', 'cb:flow:reviews', 'danger'), ('💬 Support', 'cb:flow:support', 'danger')],
    ]


def signature(user):
    # Include language because the outbound middleware localizes button labels.
    # Custom-emoji-only updates must refresh already-saved pins as well.
    content = [TEXT, keyboard(user), user.get('language', 'en'),
               emoji_theme.ICONS, emoji_theme.GLYPHS, emoji_theme.LABELS]
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
