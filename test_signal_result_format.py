"""Signal caption, owner-supplied banners, and two-message delivery lifecycle."""
import os
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

os.environ.setdefault('DATABASE_URL', 'postgresql://unused')
from aiogram.types import BufferedInputFile
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendPhoto
import test_signal_flow as H
import market_data
import market_signals as flow
import localization
from test_market_data import ASSET, payload
from test_chart_signals import VALID


class ResultTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = H._install_stub_modules()
        self.module = H._load_bot()
        self.bot = H.FakeBot()
        self.bot.edit_message_reply_markup = AsyncMock()
        async def attach(tg_id, chart_id, ids):
            if (await self.db.get_user(tg_id))['ui_msg_id'] != chart_id:
                return False
            await self.db.set_album(tg_id, ids)
            return True
        self.db.set_album_for_ui = AsyncMock(side_effect=attach)
        self.snapshot = market_data.parse_candles(payload(now=time.time()), ASSET, 60)
        self.rows = [[('🔄 New Analysis','cb:market:expire_s:asset:5','success')]]

    async def render(self, direction='BUY'):
        result = VALID if direction=='BUY' else dict(VALID,direction='SELL',setup='resistance_rejection',
            reason='Resistance held and the completed candle closed lower.')
        caption = flow.result_text(self.snapshot,result,5,expiry_unit='s')
        return await self.module.render(self.bot,11,BufferedInputFile(b'PNG',filename='chart.png'),
            caption,self.rows,raise_on_error=True,screen_key='market_result',
            companion_key=flow.DIRECTION_BANNERS[direction])

    async def test_both_banners_follow_correct_caption_and_cleanup_together(self):
        for direction in ('BUY','SELL'):
            self.bot.calls.clear()
            chart = await self.render(direction)
            photos = [c for c in self.bot.calls if c['kind']=='photo']
            self.assertEqual(len(photos),2)
            self.assertIn('Your Signal Is Ready!',photos[0]['body'])
            self.assertTrue(photos[0]['body'].endswith(f'<b>{direction}</b>'))
            self.assertIn('Expiration: <b>S5</b>',photos[0]['body'])
            self.assertEqual(photos[1]['asset'],f'assets/signal_{direction.lower()}.png')
            self.assertFalse(photos[1]['body'])
            self.assertEqual(photos[1]['markup'].inline_keyboard[0][0].callback_data,
                             'market:expire_s:asset:5')
            self.bot.edit_message_reply_markup.assert_awaited_with(
                chat_id=11,message_id=chart.message_id,reply_markup=None)
            user = await self.db.get_user(11)
            self.assertEqual(user['ui_msg_id'],chart.message_id)
            self.assertEqual(user['album_ids'],str(photos[1]['id']))
            await self.module.render(self.bot,11,None,'Next screen',[])
            removed={c['id'] for c in self.bot.calls if c['kind']=='delete'}
            self.assertTrue({chart.message_id,photos[1]['id']} <= removed)
            self.assertIsNone((await self.db.get_user(11))['album_ids'])

    async def test_banner_rejection_keeps_delivered_signal_and_controls(self):
        original=self.bot.send_photo
        async def reject_banner(chat_id, photo, **kwargs):
            if str(getattr(photo,'path','')).startswith('assets/signal_'):
                raise TelegramBadRequest(method=SendPhoto(chat_id=11,photo='test'),message='rejected')
            return await original(chat_id,photo,**kwargs)
        self.bot.send_photo=reject_banner
        chart=await self.render()
        photos=[c for c in self.bot.calls if c['kind']=='photo']
        self.assertEqual(len(photos),1)
        self.assertIsNotNone(photos[0]['markup'])
        self.assertEqual((await self.db.get_user(11))['ui_msg_id'],chart.message_id)
        self.assertIsNone((await self.db.get_user(11))['album_ids'])
        self.bot.edit_message_reply_markup.assert_not_awaited()
        self.assertFalse(any(c['kind']=='text' for c in self.bot.calls))

    async def test_chart_rejection_never_sends_banner(self):
        self.bot.send_photo=AsyncMock(side_effect=TelegramBadRequest(
            method=SendPhoto(chat_id=11,photo='test'),message='rejected'))
        with self.assertRaises(TelegramBadRequest):
            await self.render()
        self.bot.send_photo.assert_awaited_once()
        self.db.set_album_for_ui.assert_not_awaited()

    async def test_navigation_during_banner_upload_does_not_take_over_new_menu(self):
        original=self.bot.send_photo
        async def navigate(chat_id,photo,**kwargs):
            if str(getattr(photo,'path','')).startswith('assets/signal_'):
                await self.db.set_ui_msg(11,9000)
                await self.db.set_album(11,'9001')
            return await original(chat_id,photo,**kwargs)
        self.bot.send_photo=navigate
        await self.render()
        banner=[c for c in self.bot.calls if c['kind']=='photo'][-1]
        self.assertIn(banner['id'],[c['id'] for c in self.bot.calls if c['kind']=='delete'])
        user=await self.db.get_user(11)
        self.assertEqual((user['ui_msg_id'],user['album_ids']),(9000,'9001'))
        self.bot.edit_message_reply_markup.assert_not_awaited()

    def test_short_escaped_reason_and_asset_identity_in_every_language(self):
        reason='Resistance & <rejection> supports a downward bias. '+('Visible evidence. '*40)
        asset=market_data.Asset('AAPL_otc','Apple OTC',True,None,'stocks')
        snapshot=replace(self.snapshot,asset=asset)
        for code in localization.CODES:
            result=flow.result_text(snapshot,dict(VALID,reason=reason),10,code)
            self.assertIn('<b>Apple OTC</b>',result)
            self.assertIn('Resistance &amp; &lt;rejection&gt;',result)
            self.assertIn('…',result)
            self.assertIn('<b>M10</b>',result)
            self.assertLess(len(result.encode('utf-16-le'))//2,1024)
            self.assertNotIn('Currency pair:',result)
            self.assertTrue(result.endswith('📈 <b>BUY</b>'))
        for reason in ('',None,'  '):
            with self.assertRaises(flow.api.AnalysisError):
                flow.result_text(snapshot,dict(VALID,reason=reason))

    def test_owner_artwork_is_shipped_as_original_png(self):
        for key in flow.DIRECTION_BANNERS.values():
            path=Path(self.module.asset_path(key,'jpg'))
            data=path.read_bytes()
            self.assertEqual(data[:8],b'\x89PNG\r\n\x1a\n')
            self.assertEqual((int.from_bytes(data[16:20],'big'),int.from_bytes(data[20:24],'big')),
                             (2151,731))


if __name__=='__main__':
    unittest.main()
