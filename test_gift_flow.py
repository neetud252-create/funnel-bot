"""Regression checks for the registration gift and later deposit gate."""

import asyncio
import os
import sys
import types
from decimal import Decimal

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

import test_signal_flow as H


async def main():
    import config
    fake_db = H._install_stub_modules()
    bot_mod = H._load_bot()
    import panelbot

    tg_id = 77001
    fake_db._users[tg_id] = fake_db._fresh_row()
    await fake_db.set_setting(bot_mod.GIFT_FILE_ID_KEY, "go-plus-file-id")
    await fake_db.set_setting(bot_mod.GIFT_FILE_NAME_KEY, "Apex MM.xlsx")

    async def registered_without_deposit(uid):
        return {"campaign_id": config.CAMPAIGN_ID,
                "sum_deposits": Decimal(0)}

    panelbot.lookup_trader = registered_without_deposit
    bot = H.FakeBot()
    status = await bot_mod._run_verification(bot, tg_id, "123456789")
    assert status == config.VERIFY_NEED_DEPOSIT
    docs = [c for c in bot.calls if c["kind"] == "document"]
    assert len(docs) == 1, docs
    assert docs[0]["asset"] == "go-plus-file-id"
    buttons = docs[0]["markup"].inline_keyboard
    assert buttons[0][0].callback_data == "gift:downloaded"
    assert fake_db._users[tg_id]["gift_sent_at"] == "now"

    # A repeated UID cannot send another copy of the workbook.
    bot2 = H.FakeBot()
    await bot_mod._run_verification(bot2, tg_id, "123456789")
    assert not [c for c in bot2.calls if c["kind"] == "document"]

    cb = H.FakeCB(tg_id, "gift:downloaded", 12)
    bot3 = H.FakeBot()
    await bot_mod.gift_downloaded(cb, bot3)
    assert fake_db._users[tg_id]["gift_acknowledged_at"] == "now"
    start_messages = [c for c in bot3.calls if c["kind"] == "text"]
    assert start_messages[-1]["markup"].inline_keyboard[0][0].callback_data == "gift:start"

    state = H.FakeState()
    cb2 = H.FakeCB(tg_id, "gift:start", 13)
    bot4 = H.FakeBot()
    await bot_mod.gift_start(cb2, bot4, state)
    assert state.state == bot_mod.Reg.waiting_uid.state
    assert "send your account ID" in bot4.calls[-1]["body"]

    # The admin upload stores the file id issued to THIS bot.
    old_admins = config.ADMIN_IDS
    config.ADMIN_IDS = [tg_id]
    try:
        upload = H.FakeMessage(tg_id, "/setgift")
        upload.document = types.SimpleNamespace(
            file_id="replacement-file-id", file_name="Apex Replacement.xlsx")
        await bot_mod.cmd_setgift(upload)
        assert await fake_db.get_setting(bot_mod.GIFT_FILE_ID_KEY) == "replacement-file-id"
        assert upload.replies and "GIFT UPLOADED" in upload.replies[-1]
    finally:
        config.ADMIN_IDS = old_admins

    print("PASS - registered UID receives one gift, then reaches deposit verification.")


if __name__ == "__main__":
    asyncio.run(main())
