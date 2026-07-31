import io, re

c = io.open("config.py", encoding="utf-8").read()

for name in ["final", "howto", "support", "register"]:
    c = re.sub(r'    "' + name + r'": \{.*?\n    \},\n', '', c, flags=re.S)

c = c.replace('[[("Get access to Go+", "cb:go:final", "success", E_POINT)],\n               [("Open Telegram channel", "url:" + CHANNEL_URL, "primary")]]',
              '[[("Open Telegram channel", "url:" + CHANNEL_URL, "primary")]]')
c = c.replace('"photo": "final"', '"photo": "welcome"')

io.open("config.py", "w", encoding="utf-8", newline="\n").write(c)
print("config trimmed")