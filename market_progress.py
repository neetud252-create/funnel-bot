"""Standalone animated candles and a short notice for real market analysis."""
import asyncio
from contextlib import suppress

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import config
import localization

POLL_SECONDS = 1.0
REQUEST_TIMEOUT = 5.0
ANALYSING = "I'm analyzing the market."
DETAIL = 'Checking completed candles and your strategy. Your signal will appear here.'
RECONNECTING = 'Reconnecting to the analysis service…'


class Loading:
    def __init__(self, bot, tg_id, language, active):
        self.bot, self.tg_id, self.language, self.active = bot, tg_id, language, active
        self.chart_id = self.notice_id = self.task = None
        self._cleanup_lock = asyncio.Lock()
        self.markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text='⬅️ Home', callback_data='home:back', style='danger')]])

    def text(self, retrying=False):
        translate = lambda text: localization.translate_parts(text, self.language)
        return (config.T_SIG_LENS + ' <b>' + translate(ANALYSING) + '</b>\n\n'
                + config.T_SIG_GLASS + ' ' + translate(RECONNECTING if retrying else DETAIL))

    async def start(self, render):
        import db
        try:
            if not await self.active():
                return
            # Emoji-only message gives Telegram its large animated-emoji layout.
            chart = await render(self.bot, self.tg_id, None, config.SIGNAL_CHART, [],
                                 screen_key='market_animation')
            if type(getattr(chart, 'message_id', None)) is int:
                self.chart_id = chart.message_id
            if not self.chart_id or not await self.active():
                await self._cleanup()
                return
            async with asyncio.timeout(REQUEST_TIMEOUT):
                notice = await self.bot.send_message(
                    self.tg_id, self.text(), parse_mode='HTML',
                    reply_markup=self.markup)
            self.notice_id = notice.message_id
            # Navigation can replace the chart during send_message. A guarded
            # write must never overwrite the newer screen's companion IDs.
            owned = await db.set_album_for_ui(self.tg_id, self.chart_id, str(self.notice_id))
            if not owned or not await self.active():
                await self._cleanup()
                return
            self.task = asyncio.create_task(self._watch())
        except Exception:
            # An optional animation failure must not spend or block an analysis.
            await self._cleanup()

    async def _watch(self):
        try:
            while await self.active():
                await asyncio.sleep(POLL_SECONDS)
        except Exception:
            pass  # State-store trouble must not fail the actual analysis.
        finally:
            await self._cleanup()

    async def reconnecting(self):
        if self.notice_id and await self.active():
            with suppress(Exception):
                async with asyncio.timeout(REQUEST_TIMEOUT):
                    await self.bot.edit_message_text(
                        chat_id=self.tg_id, message_id=self.notice_id,
                        text=self.text(retrying=True), parse_mode='HTML', reply_markup=self.markup)

    async def _cleanup(self):
        import db
        async with self._cleanup_lock:
            for mid in (self.chart_id, self.notice_id):
                if mid is not None:
                    with suppress(Exception):
                        async with asyncio.timeout(REQUEST_TIMEOUT):
                            await self.bot.delete_message(chat_id=self.tg_id, message_id=mid)
            if self.chart_id:
                with suppress(Exception):
                    await db.set_album_for_ui(self.tg_id, self.chart_id, None)
            self.chart_id = self.notice_id = None

    async def stop(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        await self._cleanup()
