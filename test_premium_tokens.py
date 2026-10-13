"""Legacy game-token admin commands remain separate from deposit-based Premium.

Premium verification and quota regression lives in test_premium_deposit.py.
"""
import unittest
from unittest.mock import patch
import config
import test_signal_flow as H


class TokenAdminTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = H._install_stub_modules()
        self.mod = H._load_bot()
        self.db._users[11] = dict(self.db._fresh_row(), verified=True, uid='123456789')

    async def test_tokens_never_grant_premium_and_nonadmins_cannot_issue_them(self):
        with patch.object(config, 'ADMIN_IDS', {99}):
            await self.mod.cmd_tokens(H.FakeMessage(11, '/tokens 11 1000'))
            self.assertEqual(self.db._users[11]['game_tokens'], 0)
            await self.mod.cmd_tokens(H.FakeMessage(99, '/tokens 11 1000'))
            self.assertEqual(self.db._users[11]['game_tokens'], 1000)
            self.assertFalse(self.db._users[11]['is_premium'])
            await self.mod.cmd_tokenset(H.FakeMessage(99, '/tokenset 11 7'))
            self.assertEqual(self.db._users[11]['game_tokens'], 7)
            await self.mod.cmd_tokens(H.FakeMessage(99, '/tokens 11 -10'))
            self.assertEqual(self.db._users[11]['game_tokens'], 0)
            self.assertFalse(self.db._users[11]['is_premium'])


if __name__ == '__main__':
    unittest.main()
