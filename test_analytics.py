"""Offline analytics permissions, tracking, delivery and CSV regression tests."""
import json
import os
import sys
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault('DATABASE_URL','postgresql://unused/unused')
from aiogram.methods import SendMessage, SendVideo, EditMessageMedia
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
import analytics as A
import analytics_reports as R


def connection():
    conn = types.SimpleNamespace(execute=AsyncMock(),executemany=AsyncMock(),fetchrow=AsyncMock(return_value=None))
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = lambda **kw: cm
    return conn, types.SimpleNamespace(acquire=lambda:cm)


def callback(uid=12, chat='private'):
    return types.SimpleNamespace(from_user=types.SimpleNamespace(id=uid,is_bot=False,username='sample'),
        id='unique-callback',data='go:guide',answer=AsyncMock(),
        message=types.SimpleNamespace(message_id=44,chat=types.SimpleNamespace(type=chat),
            reply_markup=None,answer=AsyncMock(),edit_text=AsyncMock(),answer_document=AsyncMock()))


class AnalyticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_edited_menu_updates_click_map_and_tracks_links(self):
        conn, pool = connection()
        method = EditMessageMedia(chat_id=12, message_id=44,
            media=InputMediaPhoto(media='cached-photo', caption='Choose your asset'),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text='Back', callback_data='market:open'),
                InlineKeyboardButton(text='Help', url='https://example.com/help')]]))
        request = AsyncMock(return_value=True)
        token = A._screen.set('market_assets')
        try:
            with patch.object(A.db,'pool',pool), patch.object(A,'record',AsyncMock()) as record, \
                    patch.object(A.config,'ANALYTICS_LINK_BASE','https://tracker.example'):
                await A.OutboundMiddleware()(request,None,method)
                self.assertEqual(request.await_args.args[1].media.media,'cached-photo')
                self.assertTrue(request.await_args.args[1].reply_markup.inline_keyboard[0][1].url.startswith(
                    'https://tracker.example/r/'))
                save = conn.execute.await_args_list[0].args
                self.assertIn('DO UPDATE',save[0])
                self.assertEqual(save[1:4],(12,44,'market_assets'))
                self.assertEqual(json.loads(save[4])[0]['callback'],'market:open')
                self.assertEqual(record.await_args.kwargs['message_id'],44)
                self.assertEqual(record.await_args.kwargs['screen'],'market_assets')
        finally:
            A._screen.reset(token)

    async def test_callbacks_use_delivered_keyboard_and_replay_key(self):
        conn,pool = connection()
        conn.fetchrow.return_value = {'screen':'home','buttons':json.dumps([
            {'action':'go:guide','label':'Guide','callback':'go:guide'}])}
        cb = callback()
        handler = AsyncMock(return_value='done')
        with patch.object(A.db,'pool',pool),patch.object(A.db,'touch_user',AsyncMock()), \
             patch.object(A,'record',AsyncMock()) as record,patch.object(A.config,'ADMIN_IDS',[1]):
            self.assertEqual(await A.InboundMiddleware()(handler,cb,{}),'done')
            record.assert_awaited_once_with(12,'button_click','go:guide',label='Guide',screen='home',
                                           message_id=44,dedupe_key='callback:unique-callback')
            self.assertNotIn('unique-callback',record.await_args.args)

    async def test_messages_never_store_body_or_command_arguments(self):
        conn,pool=connection()
        event=types.SimpleNamespace(from_user=types.SimpleNamespace(id=12,is_bot=False,username='user'),
            message_id=10,chat=types.SimpleNamespace(type='private'),text='/start secret-tracking-code')
        with patch.object(A.db,'pool',pool),patch.object(A.db,'touch_user',AsyncMock()), \
             patch.object(A,'record',AsyncMock()) as record:
            await A.InboundMiddleware()(AsyncMock(),event,{})
            self.assertEqual(record.await_args.args,(12,'start','command'))
            self.assertNotIn('secret-tracking-code',str(record.await_args))
            event.text='123456789 account and private message'
            await A.InboundMiddleware()(AsyncMock(),event,{})
            self.assertEqual(record.await_args.args,(12,'message','text_or_other'))
            self.assertNotIn('123456789',str(record.await_args))

    async def test_inbound_failures_do_not_block_handlers(self):
        conn,pool=connection()
        handler=AsyncMock(return_value='ok')
        with patch.object(A.db,'pool',pool),patch.object(A.db,'touch_user',AsyncMock(side_effect=RuntimeError())), \
             self.assertLogs(level='ERROR'):
            self.assertEqual(await A.InboundMiddleware()(handler,callback(),{}),'ok')
        with patch.object(A.db,'pool',pool),patch.object(A,'record',AsyncMock()) as record, \
             patch.object(A.db,'touch_user',AsyncMock()) as touch,patch.object(A.config,'ADMIN_IDS',[1]):
            await A.InboundMiddleware()(handler,callback(uid=1),{})
            await A.InboundMiddleware()(handler,callback(chat='group'),{})
            touch.assert_not_awaited()
            record.assert_not_awaited()

    async def test_outbound_tracks_only_success_and_preserves_video(self):
        conn,pool=connection()
        markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text='Register',url='https://example.com/path?click_id=abc&other=x'),
            InlineKeyboardButton(text='Guide',callback_data='go:guide')]])
        method=SendVideo(chat_id=12,video='original-video',width=1080,height=1920,
                         caption='tutorial',reply_markup=markup)
        request=AsyncMock(return_value=types.SimpleNamespace(message_id=99))
        with patch.object(A.db,'pool',pool),patch.object(A,'record',AsyncMock()) as record, \
             patch.object(A.config,'ANALYTICS_LINK_BASE','https://tracker.example'):
            result=await A.OutboundMiddleware()(request,None,method)
            sent=request.await_args.args[1]
            self.assertEqual((sent.video,sent.width,sent.height),('original-video',1080,1920))
            self.assertEqual(method.reply_markup.inline_keyboard[0][0].url,'https://example.com/path?click_id=abc&other=x')
            self.assertTrue(sent.reply_markup.inline_keyboard[0][0].url.startswith('https://tracker.example/r/'))
            self.assertEqual(conn.executemany.await_args.args[1][0][-1],method.reply_markup.inline_keyboard[0][0].url)
            record.assert_awaited_once_with(12,'screen_view','other',screen='other',message_id=99,dedupe_key='screen:12:99')
            self.assertEqual(result.message_id,99)
            conn.execute.reset_mock()
            record.reset_mock()
            request.side_effect=RuntimeError('Telegram unavailable')
            with self.assertRaises(RuntimeError):
                await A.OutboundMiddleware()(request,None,method)
            conn.execute.assert_not_awaited()
            self.assertEqual(record.await_args.args[1],'delivery_error')

    async def test_tracking_failure_keeps_original_links(self):
        conn,pool=connection()
        conn.executemany.side_effect=RuntimeError()
        method=SendMessage(chat_id=12,text='Welcome',reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text='Support',url='https://t.me/support')]]))
        request=AsyncMock(return_value=types.SimpleNamespace(message_id=None))
        with patch.object(A.db,'pool',pool),self.assertLogs(level='ERROR'):
            await A.OutboundMiddleware()(request,None,method)
        self.assertIs(request.await_args.args[1],method)

    async def test_redirect_preserves_url_and_filters_previews(self):
        conn,pool=connection()
        conn.fetchrow.return_value={'tg_id':12,'action':'url:test','label':'Register','screen':'register',
            'destination':'https://example.com/?click_id=correct&x=1','message_id':123}
        request=types.SimpleNamespace(method='GET',headers={'user-agent':'Mozilla/5.0'})
        with patch.object(A.db,'pool',pool),patch.object(A,'record',AsyncMock()) as record:
            result=await A.open_link('a'*32,request)
            self.assertEqual(result.status_code,302)
            self.assertEqual(result.headers['location'],conn.fetchrow.return_value['destination'])
            self.assertEqual(result.headers['referrer-policy'],'no-referrer')
            record.assert_awaited_once()
            record.reset_mock()
            for method,agent in [('HEAD','Mozilla'),('GET','TelegramBot')]:
                request.method=method; request.headers={'user-agent':agent}
                await A.open_link('a'*32,request)
            record.assert_not_awaited()
            self.assertEqual((await A.open_link('bad',request)).status_code,404)

    async def test_admin_handlers_guard_every_report_and_export(self):
        messages=[]; callbacks=[]
        dp=types.SimpleNamespace(message=types.SimpleNamespace(register=lambda h,*a:messages.append(h)),
            callback_query=types.SimpleNamespace(register=lambda h,*a:callbacks.append(h)))
        R.install(dp)
        with patch.object(R.config,'ADMIN_IDS',[1]),patch.object(R,'fetch',AsyncMock(return_value=[])) as fetch:
            for uid,chat in [(12,'private'),(1,'group')]:
                cb=callback(uid,chat);cb.data='an:csv:users:30'
                await callbacks[0](cb)
                fetch.assert_not_awaited()
                cb.message.answer_document.assert_not_awaited()
            cb=callback(1);cb.data='an:csv:users:30'
            await callbacks[0](cb)
            fetch.assert_awaited_once()
            self.assertEqual(fetch.await_args.args,('users',30))
            self.assertEqual(fetch.await_args.kwargs['limit'],R.LIMIT+1)
            self.assertIsInstance(fetch.await_args.kwargs['snapshot'],datetime)
            cb.message.answer_document.assert_awaited_once()
            cb.data='an:overview:999:0'
            await callbacks[0](cb)
            self.assertEqual(fetch.await_count,1)

    async def test_private_admin_commands_and_csv_continuation(self):
        messages=[]; callbacks=[]
        dp=types.SimpleNamespace(message=types.SimpleNamespace(register=lambda h,*a:messages.append(h)),
            callback_query=types.SimpleNamespace(register=lambda h,*a:callbacks.append(h)))
        R.install(dp)
        m=types.SimpleNamespace(from_user=types.SimpleNamespace(id=12),text='/analytics',
            chat=types.SimpleNamespace(type='private'),answer=AsyncMock())
        with patch.object(R.config,'ADMIN_IDS',[1]),patch.object(R,'dashboard',AsyncMock(return_value=('report',None))) as report:
            await messages[0](m)
            report.assert_not_awaited()
            m.from_user.id=1; m.chat.type='group'
            await messages[0](m)
            report.assert_not_awaited()
            m.chat.type='private'; m.text='/analytics 7'
            await messages[0](m)
            report.assert_awaited_once_with('overview',7)
        cb=callback(1);cb.data='an:csv:users:30'
        with patch.object(R.config,'ADMIN_IDS',[1]),patch.object(R,'LIMIT',2), \
             patch.object(R,'fetch',AsyncMock(side_effect=[[{'id':1},{'id':2},{'id':3}],[{'id':3}]])) as fetch:
            await callbacks[0](cb)
            first=cb.message.answer_document.await_args
            cb.data=first.kwargs['reply_markup'].inline_keyboard[0][0].callback_data
            await callbacks[0](cb)
            self.assertEqual(fetch.await_args_list[1].kwargs['offset'],2)
            self.assertEqual(fetch.await_args_list[0].kwargs['snapshot'],fetch.await_args_list[1].kwargs['snapshot'])
            self.assertIsNone(cb.message.answer_document.await_args.kwargs['reply_markup'])

    def test_csv_formula_safety_and_ist(self):
        content=R.csv_bytes([{'name':'=HYPERLINK("bad")','source':' @SUM(1)',
            'at':datetime(2026,10,18,20,tzinfo=timezone.utc),'value':12}]).decode('utf-8-sig')
        self.assertIn("'=HYPERLINK",content)
        self.assertIn("' @SUM",content)
        self.assertIn('2026-10-19T01:30:00+05:30',content)
        self.assertEqual(R.percent(0,0),'N/A')


if __name__=='__main__':
    unittest.main()
