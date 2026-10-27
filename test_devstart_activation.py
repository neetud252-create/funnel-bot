"""Admin reset -> new Elena DM -> automatic bot interface, entirely offline."""
import asyncio
import copy
import os
import types
from unittest.mock import AsyncMock, patch
import test_signal_flow as H


async def main():
    db = H._install_stub_modules()
    B = H._load_bot()
    E, A = B.elena, B.activation
    os.environ.update(ELENA_ENABLED='1', ELENA_ACTIVATION_ENABLED='1', ELENA_ACTIVATION_ONLY='1')
    admin, other, new_admin = 84001, 84002, 84003
    B.config.ADMIN_IDS = [admin, new_admin]
    H._dirty_user(db, admin)
    H._dirty_user(db, other)
    for uid in (admin, other):
        db._users[uid] = db._fresh_row() | db._users[uid]
    sessions = {uid: dict(tg_id=uid, activated_at='before', activation_opened_at='before',
                         activation_version=E.store.ACTIVATION_VERSION, last_message_id=10,
                         last_business_at='before', business_connection_id='approved',
                         stopped=False, handoff=False) for uid in (admin, other)}
    untouched_user, untouched_session = copy.deepcopy(db._users[other]), dict(sessions[other])
    bot, state = H.FakeBot(), H.FakeState(state='Reg:waiting_uid')
    bot.id = 8790198817

    async def session(uid):
        return dict(sessions[uid]) if uid in sessions else None

    async def reset(uid):
        if uid in sessions:
            sessions[uid].update(activated_at=None, activation_opened_at=None, activation_version=0,
                                 last_business_at=None)

    async def incoming(uid, connection_id, message_id):
        if message_id <= sessions[uid]['last_message_id']:
            return None
        sessions[uid].update(last_message_id=message_id, last_business_at='new')
        return dict(sessions[uid])

    async def activate(uid, *args):
        sessions[uid].update(activated_at='new', activation_version=E.store.ACTIVATION_VERSION)
        return dict(sessions[uid])

    async def opened(uid):
        if A.current(sessions[uid]):
            sessions[uid]['activation_opened_at'] = 'new'

    connection = dict(connection_id='approved', owner_id=99001, enabled=True, can_reply=True)
    original_reset_user = db.reset_user

    async def reset_user(uid):
        if uid == admin:
            assert state.state is None, 'Old analysis session must close before free quota resets'
        return await original_reset_user(uid)

    with patch.object(E.store, 'session', side_effect=session), \
         patch.object(db, 'reset_user', side_effect=reset_user), \
         patch.object(E.store, 'reset_activation', side_effect=reset) as reset_call, \
         patch.object(E.store, 'connection', AsyncMock(return_value=connection)), \
         patch.object(E.store, 'incoming', side_effect=incoming), \
         patch.object(E.store, 'activate', side_effect=activate) as activate_call, \
         patch.object(E.store, 'claim_activation', AsyncMock(return_value={'tg_id':admin})), \
         patch.object(E.store, 'activation_opened', side_effect=opened), \
         patch.object(E, 'send_business', AsyncMock()) as send:
        # Non-admin attempts do not even touch Elena state.
        await B.cmd_devstart(H.FakeMessage(other, '/devstart'), bot, state)
        reset_call.assert_not_awaited()
        assert not bot.calls and state.state == 'Reg:waiting_uid'

        # An older Elena reply finishes before reset. No stale interface wins.
        lock = E.user_lock(admin)
        await lock.acquire()
        task = asyncio.create_task(B.cmd_devstart(H.FakeMessage(admin, '/devstart'), bot, state))
        await asyncio.sleep(0)
        assert not task.done()
        reset_call.assert_not_awaited()
        lock.release()
        await asyncio.wait_for(task, 2)
        reset_call.assert_awaited_once_with(admin)
        assert await A.required(admin)
        assert db._users[admin]['verified'] is False and db._users[admin]['uid'] is None
        assert state.state is None
        assert bot.calls[-1]['body'] == E.copy.ACTIVATION
        buttons = [b for row in bot.calls[-1]['markup'].inline_keyboard for b in row]
        assert len(buttons) == 1 and 'elitecsupport' in buttons[0].url
        assert not await B._open_from_elena(bot, admin), 'Old pending menu cannot reopen access'

        # A normal /start stays on the gate; it is not proof of Elena contact.
        await B.start(H.FakeMessage(admin, '/start'), bot, state)
        assert bot.calls[-1]['body'] == E.copy.ACTIVATION
        m = types.SimpleNamespace(chat=types.SimpleNamespace(id=admin, type='private'),
            from_user=types.SimpleNamespace(id=admin), message_id=10, text=A.DRAFT,
            business_connection_id='approved', sender_business_bot=None, via_bot=None, is_from_offline=False)
        await E.business_message(m, bot)
        activate_call.assert_not_awaited()
        send.assert_not_awaited()
        assert await A.required(admin)

        m.message_id = 11
        await E.business_message(m, bot)
        activate_call.assert_awaited_once()
        assert send.await_args.args[2] == E.copy.ACTIVATED
        assert send.await_args.kwargs == {'activation_confirmation': True}
        assert not await A.required(admin)
        assert bot.calls[-1]['asset'] == 'assets/home_banner_loop.mp4'
        assert db._users[admin]['verified'] is False, 'Contact activation does not verify a trader account'
        count = len(bot.calls)
        await E.business_message(m, bot)
        assert len(bot.calls) == count and send.await_count == 1
        assert db._users[other] == untouched_user and sessions[other] == untouched_session

        # First-ever admin use also opens activation without needing an old row.
        await B.cmd_devstart(H.FakeMessage(new_admin, '/devstart'), bot, H.FakeState())
        assert bot.calls[-1]['body'] == E.copy.ACTIVATION

    print('PASS: admin-only reset, reply serialization, activation gate, replay rejection, new DM confirmation and automatic menu, other accounts unchanged.')


if __name__ == '__main__':
    asyncio.run(main())
