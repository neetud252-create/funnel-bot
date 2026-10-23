"""Best-effort Telegram loading animation, scoped to one analysis message."""
import asyncio
from contextlib import suppress

from aiogram.exceptions import TelegramRetryAfter
import localization

TITLE = 'Pocket IQ Chart Analysis'
RECEIVED = 'Chart received'
ANALYSING = 'Analysing your chart…'
RETRYING = 'Reconnecting to AI…'
PENDING = 'Result pending'
COMPLETE = 'Analysis complete'
SOURCES = (TITLE, RECEIVED, ANALYSING, RETRYING, PENDING, COMPLETE)
FRAME_SECONDS = 3.0
EDIT_TIMEOUT = 4.0


def frame(language, tick=0, *, retrying=False):
    translate = lambda text: localization.translate_parts(text, language)
    # Indeterminate scanner: Gemini does not expose percentage progress.
    position = tick % 9
    bar = ''.join('▰' if i in (position, position + 1) else '▱' for i in range(10))
    spinner = ('◐', '◓', '◑', '◒')[tick % 4]
    return (f'🤖 <b>{translate(TITLE)}</b>\n\n'
            f'{bar}\n\n'
            f'✅ {translate(RECEIVED)}\n'
            f'{spinner} {translate(RETRYING if retrying else ANALYSING)}\n'
            f'▫️ {translate(PENDING)}')


def completed(language):
    return f'✅ <b>{localization.translate_parts(COMPLETE, language)}</b>\n▰▰▰▰▰▰▰▰▰▰\n\n'


class Progress:
    def __init__(self, bot, tg_id, message, language, active):
        self.bot, self.tg_id = bot, tg_id
        self.message_id = getattr(message, 'message_id', None)
        self.markup = getattr(message, 'reply_markup', None)
        self.language, self.active = language, active
        self.retrying = False
        self.task = None

    def start(self):
        # A failed initial render must never stop the provider request.
        if type(self.message_id) is int:
            self.task = asyncio.create_task(self._run())

    async def _run(self):
        delay, tick = FRAME_SECONDS, 1
        try:
            while True:
                await asyncio.sleep(delay)
                if not await self.active():
                    return
                try:
                    async with asyncio.timeout(EDIT_TIMEOUT):
                        await self.bot.edit_message_text(
                            chat_id=self.tg_id, message_id=self.message_id,
                            text=frame(self.language, tick, retrying=self.retrying),
                            parse_mode='HTML', reply_markup=self.markup)
                except TelegramRetryAfter as exc:
                    delay = max(FRAME_SECONDS, exc.retry_after)
                else:
                    delay = FRAME_SECONDS
                    tick += 1
        except asyncio.CancelledError:
            raise
        except Exception:
            # Deleted message, network error or navigation: animation is optional.
            # Never recreate the message or affect analysis/quota accounting.
            return

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None
