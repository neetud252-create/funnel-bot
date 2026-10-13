"""The workbook is downloaded from the welcome menu, never auto-delivered."""
import asyncio
from decimal import Decimal
import types
from unittest.mock import AsyncMock, patch

import test_signal_flow as H


async def main():
    import config
    import home_menu
    db = H._install_stub_modules()
    mod = H._load_bot()
    tg_id = 77001
    row = db._fresh_row()
    db._users[tg_id] = row
    await db.set_setting(mod.GIFT_FILE_ID_KEY, 'go-plus-file-id')
    await db.set_setting(mod.GIFT_FILE_NAME_KEY, 'Apex MM.xlsx')
    assert home_menu.keyboard()[-2:] == [
        [('📊 Money Management Sheet', 'cb:home:sheet', 'primary')],
        [('🌐 Change Language', 'cb:home:language', 'danger')]]

    # Everyone can download directly, repeatedly, without changing their access,
    # account ID, quota, or an in-progress registration/Premium conversation.
    for verified in (False, True):
        row.update(verified=verified, uid='123456789', signals_used_today=12)
        before = dict(row)
        bot = H.FakeBot()
        state = H.FakeState('existing-flow')
        with patch.object(mod.panelbot, 'lookup_trader', AsyncMock()) as lookup:
            for _ in range(2):
                await mod.home_action(H.FakeCB(tg_id, 'home:sheet', 1), bot, state)
            lookup.assert_not_awaited()
        docs = [c for c in bot.calls if c['kind'] == 'document']
        assert len(docs) == 2
        assert all(c['asset'] == 'go-plus-file-id' for c in docs)
        assert all(c['body'] == home_menu.SHEET_CAPTION for c in docs)
        assert docs[0]['markup'].inline_keyboard[0][0].callback_data == 'home:back'
        assert 'Account confirmed' not in docs[0]['body']
        assert state.state == 'existing-flow'
        assert {k:v for k,v in row.items() if k!='gift_sent_at'} == {
            k:v for k,v in before.items() if k!='gift_sent_at'}

    # The registration verdict now shows the deposit requirement directly.
    for amount, expected in ((Decimal(0), config.VERIFY_NEED_DEPOSIT),
                             (config.MIN_DEPOSIT, config.VERIFY_GRANTED)):
        bot = H.FakeBot()
        with patch.object(mod.panelbot, 'lookup_trader', AsyncMock(return_value={
                'campaign_id':config.CAMPAIGN_ID, 'sum_deposits':amount})):
            assert await mod._run_verification(bot,tg_id,'123456789') == expected
        assert not any(c['kind']=='document' for c in bot.calls)
        if amount == 0:
            assert config.MSG_NEED_DEPOSIT in [c['body'] for c in bot.calls]

    # Returning to the menu or resubmitting a verified ID no longer sends it.
    bot = H.FakeBot()
    await mod.show(bot,tg_id,'menu')
    await mod._capture_uid(H.FakeMessage(tg_id,'123456789'),bot,H.FakeState())
    assert not any(c['kind']=='document' for c in bot.calls)
    row['verified'] = False
    with patch.object(config,'TEST_MODE',True):
        await mod._capture_uid(H.FakeMessage(tg_id,'123456789'),bot,H.FakeState())
    assert not any(c['kind']=='document' for c in bot.calls)

    # Missing or expired Telegram document IDs yield a useful reply, not a crash.
    await db.set_setting(mod.GIFT_FILE_ID_KEY,'')
    bot = H.FakeBot()
    await mod.home_action(H.FakeCB(tg_id,'home:sheet',1),bot,H.FakeState())
    assert bot.calls[-1]['body'] == home_menu.SHEET_UNAVAILABLE
    await db.set_setting(mod.GIFT_FILE_ID_KEY,'expired-file-id')
    bot.send_document = AsyncMock(side_effect=RuntimeError('unavailable'))
    await mod.home_action(H.FakeCB(tg_id,'home:sheet',1),bot,H.FakeState())
    assert bot.calls[-1]['body'] == home_menu.SHEET_UNAVAILABLE

    # /setgift still updates the same persisted Telegram file ID.
    with patch.object(config,'ADMIN_IDS',[tg_id]):
        upload=H.FakeMessage(tg_id,'/setgift')
        upload.document=types.SimpleNamespace(file_id='replacement-id',file_name='Apex.xlsx')
        await mod.cmd_setgift(upload)
        assert await db.get_setting(mod.GIFT_FILE_ID_KEY)=='replacement-id'
        assert 'welcome menu' in upload.replies[-1]
    print('PASS: welcome sheet download, repeat downloads, no deposit gate, no auto-delivery, failure handling, admin replacement.')


if __name__=='__main__':
    asyncio.run(main())
