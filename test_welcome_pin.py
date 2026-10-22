"""Persistent welcome pins: lifecycle, cleanup, concurrency and failure isolation."""
import asyncio
import gc
from unittest.mock import AsyncMock, patch
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import PinChatMessage
import test_signal_flow as H


async def main():
    db = H._install_stub_modules()
    mod = H._load_bot()
    bot, state = H.FakeBot(), H.FakeState()
    uid = 73001
    await mod.start(H.FakeMessage(uid, '/start'), bot, state)
    anchor = db._users[uid]['welcome_message_id']
    assert anchor == db._users[uid]['ui_msg_id']
    assert db._users[uid]['welcome_pinned_at']
    assert bot.pins == [(uid, anchor, True)]
    assert bot.calls[-1]['kind'] == 'animation'

    # Buttons still work and menu replacement never deletes the first welcome.
    await mod.home_action(H.FakeCB(uid, 'home:access', anchor), bot, state)
    assert db._users[uid]['ui_msg_id'] != anchor
    assert not any(c['kind'] == 'delete' and c['id'] == anchor for c in bot.calls)
    await mod.start(H.FakeMessage(uid, '/start'), bot, state)
    assert len(bot.pins) == 1 and db._users[uid]['welcome_message_id'] == anchor

    # A fresh process and an owner test-reset both retain the existing pin.
    mod = H._load_bot()
    await mod.start(H.FakeMessage(uid, '/start'), bot, state)
    await db.reset_user(uid)
    await mod.start(H.FakeMessage(uid, '/start'), bot, state)
    assert len(bot.pins) == 1
    assert db._users[uid]['welcome_message_id'] == anchor

    # All shared cleanup paths protect the pinned anchor, including stale taps.
    bot.calls.clear()
    db._users[uid].update(ui_msg_id=anchor, album_ids=f'{anchor},7002')
    await mod.wipe(bot, uid)
    await mod._drop_msgs(bot, uid, [anchor, 7003])
    await mod._replace(bot, uid, anchor, 'Check complete')
    deleted = {c['id'] for c in bot.calls if c['kind'] == 'delete'}
    assert anchor not in deleted and {7002, 7003}.issubset(deleted)

    # A manual unpin/hide is respected: saved successful pins are not forced back.
    bot.pins.clear()
    await mod.start(H.FakeMessage(uid, '/start'), bot, state)
    assert bot.pins == []

    # Group /start commands never cause an automatic private-chat pin.
    group_start = H.FakeMessage(73002, '/start')
    group_start.chat.type = 'group'
    await mod.start(group_start, bot, state)
    assert db._users[73002]['welcome_message_id'] is None
    assert bot.pins == []

    # Transient Telegram failure preserves the welcome and retries the SAME ID.
    uid = 73003
    bot = H.FakeBot()
    with patch.object(bot, 'pin_chat_message', AsyncMock(side_effect=RuntimeError('offline'))):
        await mod.start(H.FakeMessage(uid, '/start'), bot, state)
    pending = db._users[uid]['welcome_message_id']
    assert pending and db._users[uid]['welcome_pinned_at'] is None
    assert bot.calls[-1]['markup']
    await mod.start(H.FakeMessage(uid, '/start'), bot, state)
    assert bot.pins == [(uid, pending, True)]
    assert not any(c['kind'] == 'delete' and c['id'] == pending for c in bot.calls)

    # If an unconfirmed pending message disappeared, pin this start's fresh card.
    uid = 73004
    db._users[uid] = dict(db._fresh_row(), welcome_message_id=88)
    bot = H.FakeBot()
    missing = TelegramBadRequest(method=PinChatMessage(chat_id=uid, message_id=88),
                                 message='Bad Request: message to pin not found')
    with patch.object(bot, 'pin_chat_message', AsyncMock(side_effect=[missing, True])) as pin:
        await mod.start(H.FakeMessage(uid, '/start'), bot, state)
    assert pin.await_count == 2
    assert pin.await_args_list[0].kwargs['message_id'] == 88
    assert pin.await_args_list[1].kwargs['message_id'] == db._users[uid]['ui_msg_id']
    assert db._users[uid]['welcome_pinned_at']
    # The named exception fixture otherwise keeps its caught coroutine frames
    # (including the temporary start lock) alive for the rest of this test.
    missing.__traceback__ = None

    # Rapid duplicate starts serialize and only pin one anchor.
    uid, bot = 73005, H.FakeBot()
    await asyncio.gather(*(mod.start(H.FakeMessage(uid, '/start'), bot, H.FakeState()) for _ in range(3)))
    anchor = db._users[uid]['welcome_message_id']
    assert bot.pins == [(uid, anchor, True)]
    assert not any(c['kind'] == 'delete' and c['id'] == anchor for c in bot.calls)
    await asyncio.sleep(0)  # release completed gather callbacks holding task frames
    gc.collect()  # weak references retire locks when completed task cycles are collected
    assert len(mod._welcome_locks) == 0, list(mod._welcome_locks)

    # Pin persistence failures never turn a valid home screen into an error.
    uid, bot = 73006, H.FakeBot()
    with patch.object(db, 'claim_welcome_message', AsyncMock(side_effect=RuntimeError('database unavailable'))):
        await mod.start(H.FakeMessage(uid, '/start'), bot, state)
    assert bot.calls[-1]['kind'] == 'animation' and bot.calls[-1]['markup']
    assert bot.pins == []

    # If anchor lookup fails, cleanup is skipped rather than deleting blindly.
    bot.calls.clear()
    with patch.object(db, 'get_user', AsyncMock(side_effect=RuntimeError('database unavailable'))):
        await mod._replace(bot, uid, anchor, 'Verification complete')
    assert not any(c['kind'] == 'delete' for c in bot.calls)
    assert bot.calls[-1]['body'] == 'Verification complete'
    print('PASS - welcome pin lifecycle, restarts, cleanup, unpin choice, private scope, retries, concurrency, and failure isolation.')


if __name__ == '__main__':
    asyncio.run(main())
