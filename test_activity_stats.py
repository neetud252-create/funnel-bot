import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.modules['db'] = types.SimpleNamespace(pool=None)
import activity_stats as A


class StatsTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_admin_access(self):
        handlers = []
        dp = types.SimpleNamespace(message=types.SimpleNamespace(register=lambda h,*f: handlers.append(h)))
        A.install(dp)
        m = types.SimpleNamespace(from_user=types.SimpleNamespace(id=2),
                                  chat=types.SimpleNamespace(type='private'),answer=AsyncMock())
        with patch.object(A.config,'ADMIN_IDS',[1]), patch.object(A,'report',AsyncMock(return_value='stats')) as report:
            await handlers[0](m)
            report.assert_not_awaited()
            m.from_user.id = 1
            m.chat.type = 'group'
            await handlers[0](m)
            report.assert_not_awaited()
            m.chat.type = 'private'
            await handlers[0](m)
            m.answer.assert_awaited_once_with('stats')

    async def test_activity_is_inbound_and_handler_continues(self):
        event = types.SimpleNamespace(from_user=types.SimpleNamespace(id=2,is_bot=False),
            chat=types.SimpleNamespace(type='private'))
        handler = AsyncMock(return_value='result')
        with patch.object(A,'record',AsyncMock()) as record:
            self.assertEqual(await A.ActivityMiddleware()(handler,event,{}),'result')
            record.assert_awaited_once_with(2,'active')
        event.chat.type = 'group'
        with patch.object(A,'record',AsyncMock()) as record:
            await A.ActivityMiddleware()(handler,event,{})
            record.assert_not_awaited()

    async def test_admin_exclusion_and_fail_open(self):
        conn=types.SimpleNamespace(execute=AsyncMock(side_effect=RuntimeError('offline')))
        acquire=MagicMock()
        acquire.__aenter__=AsyncMock(return_value=conn)
        acquire.__aexit__=AsyncMock(return_value=False)
        with patch.object(A.db,'pool',types.SimpleNamespace(acquire=lambda:acquire)), \
             patch.object(A.config,'ADMIN_IDS',[1]):
            await A.record(1,'active')
            conn.execute.assert_not_awaited()
            with self.assertLogs(level='ERROR'):
                await A.record(2,'active')
            self.assertIn("Asia/Kolkata",conn.execute.await_args.args[0])
            self.assertIn('ON CONFLICT',conn.execute.await_args.args[0])

    async def test_dashboard_conversion_and_labels(self):
        keys=('total','new_today','new_7','new_30','uid','verified','gifts','ack',
              'blocked','optout','active_today','active_7','active_30','cohort','converted')
        users=dict.fromkeys(keys,0)
        users.update(cohort=100,converted=20)
        metrics={'gift_delivered':{'people':5,'events':9}}
        result=A.format_report(users,metrics,[],'2026-10-08 02:00 IST')
        self.assertIn('20.0%',result)
        self.assertIn('9 sends to 5 users',result)
        self.assertIn('not a verified download',result)
        self.assertIn('Older activity and clicks are unavailable',result)
        self.assertEqual(A.rate(0,0),'N/A')


if __name__=='__main__':
    unittest.main()
