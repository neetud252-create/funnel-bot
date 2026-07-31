import io

d = io.open("db.py", encoding="utf-8").read()

if "album_ids" not in d:
    d = d.replace(
        '"""\n\nasync def connect',
        'ALTER TABLE users ADD COLUMN IF NOT EXISTS album_ids TEXT;\n"""\n\nasync def connect'
    )

if "set_album" not in d:
    d += (
        "\nasync def set_album(tg_id, ids):\n"
        "    async with pool.acquire() as c:\n"
        "        await c.execute(\"UPDATE users SET album_ids=$1 WHERE tg_id=$2\", ids, tg_id)\n"
    )

io.open("db.py", "w", encoding="utf-8", newline="\n").write(d)
print("patched db")