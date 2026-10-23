"""Keep the bot's public Telegram name and introduction in sync with the UI."""
import asyncio
import html
import logging
import re

import home_menu
import localization

NAME = 'Pocket IQ'


def description(language='en'):
    text = html.unescape(re.sub(r'<[^>]+>', '', localization.translate_message(home_menu.TEXT, language)))
    # Telegram's profile description has a smaller limit than message captions.
    # Remove complete trailing paragraphs rather than cutting a translated sentence.
    parts = text.split('\n\n')
    while len('\n\n'.join(parts)) > 512 and len(parts) > 1:
        parts.pop()
    return '\n\n'.join(parts)


def short_description(language='en'):
    return description(language).split('\n')[1]


async def sync(bot):
    try:
        async with asyncio.timeout(45):
            for code in ('', *localization.CODES):
                await bot.set_my_name(name=NAME, language_code=code)
                await bot.set_my_description(description=description(code or 'en'), language_code=code)
                await bot.set_my_short_description(description=short_description(code or 'en'), language_code=code)
        logging.info('Bot profile ready: Pocket IQ; 12 translated descriptions')
    except Exception as error:
        # Profile settings must not prevent normal messages or long polling.
        logging.warning('Bot profile update incomplete: %s', type(error).__name__)
