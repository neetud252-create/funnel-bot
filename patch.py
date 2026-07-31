import io

c = io.open("config.py", encoding="utf-8").read()
c = c.replace('"See real results", "cb:go:results"', '"See real results", "cb:results"')
c = c.replace('"See real results", "cb:gallery:0"', '"See real results", "cb:results"')

if '"results": {' not in c:
    block = (
        '    "results": {\n'
        '        "photo": "final",\n'
        '        "text": "\\U0001F465 <b>Real feedback from active Go+ traders.</b>"\n'
        '                "\\n\\n\\U0001F446 The screenshots above are a small sample."\n'
        '                "\\n\\n\\U0001F49D More feedback on our channel:\\n" + CHANNEL_URL,\n'
        '        "kb": [[("Get access to Go+", "cb:go:final", "success", E_POINT)],\n'
        '               [("Open Telegram channel", "url:" + CHANNEL_URL, "primary")]],\n'
        '    },\n'
    )
    c = c.replace('    "final": {', block + '    "final": {', 1)

io.open("config.py", "w", encoding="utf-8", newline="\n").write(c)

b = io.open("bot.py", encoding="utf-8").read()

if "send_media_group" not in b:
    handler = (
        '@dp.callback_query(F.data == "results")\n'
        'async def results(cb: CallbackQuery, bot: Bot):\n'
        '    await cb.answer()\n'
        '    tg_id = cb.from_user.id\n'
        '    await wipe(bot, tg_id)\n'
        '    media = [InputMediaPhoto(media=photo_for(k)) for k in config.REVIEWS]\n'
        '    msgs = await bot.send_media_group(tg_id, media)\n'
        '    for k, msg in zip(config.REVIEWS, msgs):\n'
        '        remember(k, msg)\n'
        '    await db.set_album(tg_id, ",".join(str(x.message_id) for x in msgs))\n'
        '    s = config.SCREENS["results"]\n'
        '    m = await bot.send_message(tg_id, s["text"], parse_mode="HTML",\n'
        '                               reply_markup=build_kb(s["kb"]))\n'
        '    await db.set_ui_msg(tg_id, m.message_id)\n\n'
    )
    b = b.replace('@dp.callback_query(F.data.startswith("go:"))', handler + '@dp.callback_query(F.data.startswith("go:"))', 1)

if "InputMediaPhoto" not in b:
    b = b.replace("InlineKeyboardButton, FSInputFile", "InlineKeyboardButton, InputMediaPhoto, FSInputFile")

io.open("bot.py", "w", encoding="utf-8", newline="\n").write(b)
print("patched")