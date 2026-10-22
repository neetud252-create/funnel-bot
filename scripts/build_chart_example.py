"""Generate an explicitly illustrative chart for the screenshot help screen."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

root = Path(__file__).resolve().parents[1]
image = Image.new('RGB', (1200, 750), '#0c1522')
draw = ImageDraw.Draw(image)
font_path = 'C:/Windows/Fonts/arial.ttf'
font = ImageFont.truetype(font_path, 28)
small = ImageFont.truetype(font_path, 22)
large = ImageFont.truetype(font_path, 42)
draw.text((45, 30), 'GO PLUS / SCREENSHOT GUIDE', font=large, fill='#f4f8ff')
draw.text((45, 95), 'ILLUSTRATION ONLY - NOT LIVE MARKET DATA', font=small, fill='#e9b75f')
draw.rounded_rectangle((45, 155, 1155, 640), radius=18, fill='#111f30', outline='#30435a', width=2)
draw.text((70, 175), 'EUR/USD OTC', font=font, fill='#d7e6ff')
draw.text((930, 175), 'Interval: M1', font=font, fill='#d7e6ff')
for y in range(250, 600, 70):
    draw.line((70, y, 1130, y), fill='#253345', width=1)
values = [470,450,468,425,440,418,435,397,388,415,390,366,348,371,350,365,335,319,342,310,325,300,285,302,280,300,325,310,294,312]
previous = 490
for i, value in enumerate(values):
    x = 85 + i * 35
    color = '#37cba0' if value < previous else '#ef7e7e'
    draw.line((x, min(value,previous)-17,x,max(value,previous)+20),fill=color,width=3)
    draw.rectangle((x-9,min(value,previous),x+9,max(value,previous)+3),fill=color)
    previous = value
draw.text((70, 590), 'Keep recent AND earlier candles visible', font=small, fill='#a8b9cc')
draw.text((45, 675), 'Show the asset + interval. Crop out balance, account ID and personal details.', font=small, fill='#d7e6ff')
image.save(root / 'assets' / 'chart_example.jpg', quality=93)
