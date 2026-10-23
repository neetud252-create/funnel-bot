"""Persistent Start Here card, separate from the full home menu."""
import hashlib
import json
import emoji_theme
import home_menu


TEXT = (
    "📌 <b>POCKET IQ — START HERE</b>\n\n"
    "Your AI-powered chart analysis assistant.\n\n"
    "🎯 <b>Explore first</b>\n"
    "Try two free analyses by uploading a clear chart screenshot.\n\n"
    "📖 <b>Understand the results</b>\n"
    "Open the guide to learn how the bot works.\n\n"
    "🔓 <b>Continue when you’re ready</b>\n"
    "Get Bot Access explains registration, access requirements, and deposit verification.\n\n"
    "💬 <b>Need help?</b>\n"
    "Use the official Support button below."
)


def keyboard(user=None):
    return home_menu.keyboard(user)


def signature(user):
    # Include language because the outbound middleware localizes button labels.
    # Custom-emoji-only updates must refresh already-saved pins as well.
    content = [TEXT, keyboard(user), user.get('language', 'en'),
               emoji_theme.ICONS, emoji_theme.GLYPHS, emoji_theme.LABELS]
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
