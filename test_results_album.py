"""Legacy review entry points now show disclosed community reviews."""
import asyncio
import journey
import test_signal_flow as H

async def main():
    db = H._install_stub_modules()
    mod = H._load_bot()
    bot = H.FakeBot()
    db._users[12] = dict(db._fresh_row(), ui_msg_id=90, album_ids='88,89')
    await mod.results(H.FakeCB(12, 'results', 90), bot)
    sends = [c for c in bot.calls if c['kind'] != 'delete']
    assert len(sends) == 1 and sends[0]['kind'] == 'text'
    assert sends[0]['body'] == journey.TEXTS['reviews']
    assert 'not independently audited' in sends[0]['body']
    buttons = [b for row in sends[0]['markup'].inline_keyboard for b in row]
    assert any(b.url == journey.config.REVIEWS_URL for b in buttons)
    assert any(b.callback_data == 'flow:results' for b in buttons)
    assert any(b.callback_data == 'flow:support' for b in buttons)
    assert {88, 89, 90}.issubset({c['id'] for c in bot.calls if c['kind'] == 'delete'})
    assert db._users[12]['album_ids'] is None
    await mod.show(bot, 12, 'results')
    assert bot.calls[-1]['body'] == journey.TEXTS['reviews']
    assert not any(c['kind'] == 'album' for c in bot.calls)
    print('PASS: legacy review routes, clear disclosure, channel/results/support buttons, old album cleanup.')

if __name__ == '__main__':
    asyncio.run(main())
