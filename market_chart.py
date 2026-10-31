"""Render validated OHLC data to a Telegram-ready candlestick PNG."""
from datetime import datetime, timezone
import io
import threading
import strategy_indicators

# Matplotlib isn't thread-safe. Use the object API, with no pyplot/global GUI.
_lock = threading.Lock()


def render(snapshot, *, preview=False, indicators=()):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.patches import Rectangle
    from matplotlib.ticker import FuncFormatter, MaxNLocator

    with _lock:
        bars = snapshot.candles[-60:]
        series = strategy_indicators.calculate(snapshot.candles, indicators)
        oscillators = [item for item in series if item['kind'] == 'RSI']
        fig = Figure(figsize=(12, 7), dpi=120, facecolor='#091320')
        FigureCanvasAgg(fig)
        ax = fig.add_axes((.08, .42, .84, .38) if oscillators else (.08, .18, .84, .62), facecolor='#101e30')
        for index, bar in enumerate(bars):
            color = '#20d6a0' if bar.close >= bar.open else '#ff647a'
            ax.vlines(index, bar.low, bar.high, color=color, linewidth=1.2)
            ax.add_patch(Rectangle((index-.32, min(bar.open, bar.close)), .64,
                                  abs(bar.close-bar.open), facecolor=color, edgecolor=color))
            if bar.open == bar.close:
                ax.hlines(bar.open, index-.32, index+.32, color=color, linewidth=1.8)
        ax.set_xlim(-1, len(bars))
        low, high = min(b.low for b in bars), max(b.high for b in bars)
        colors = ('#ffd166', '#b29aff', '#61d6fa', '#ff9db8')
        for index, item in enumerate(series):
            if item['kind'] != 'EMA':
                continue
            values = item['values'][-len(bars):]
            visible = [value for value in values if value is not None]
            low, high = min(low, *visible), max(high, *visible)
            ax.plot(range(len(bars)), values, color=colors[index % len(colors)], linewidth=1.6,
                    label=f"EMA {item['period']} (close) {values[-1]:.8g}")
        if any(item['kind'] == 'EMA' for item in series):
            ax.legend(loc='upper left', facecolor='#101e30', labelcolor='white', fontsize=9)
        pad = max((high-low)*.08, high*1e-6)
        ax.set_ylim(low-pad, high+pad)
        ax.yaxis.tick_right()
        ax.yaxis.set_major_locator(MaxNLocator(7))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f'{value:.8g}'))
        ticks = list(range(0, len(bars), 10))
        ax.set_xticks(ticks, [datetime.fromtimestamp(bars[i].time, timezone.utc).strftime('%H:%M') for i in ticks])
        if oscillators:
            ax.tick_params(labelbottom=False)
            rax = fig.add_axes((.08, .18, .84, .17), facecolor='#101e30')
            for index, item in enumerate(oscillators):
                values = item['values'][-len(bars):]
                rax.plot(range(len(bars)), values, color=colors[(index + 1) % len(colors)], linewidth=1.5,
                         label=f"RSI {item['period']} (Wilder, close) {values[-1]:.2f}")
            rax.set_xlim(-1, len(bars))
            rax.set_ylim(-3, 103)
            rax.set_yticks([0, 50, 100])
            rax.axhline(50, color='#b8c8dc', linestyle='--', alpha=.5, linewidth=.8)
            rax.set_xticks(ticks, [datetime.fromtimestamp(bars[i].time, timezone.utc).strftime('%H:%M') for i in ticks])
            rax.yaxis.tick_right()
            rax.tick_params(colors='#b8c8dc', labelsize=9, length=0, pad=7)
            for spine in rax.spines.values():
                spine.set_color('#223449')
            rax.legend(loc='upper left', facecolor='#101e30', labelcolor='white', fontsize=8)
            rax.grid(alpha=.12, color='#abc0da')
        ax.tick_params(colors='#b8c8dc', labelsize=10, length=0, pad=10)
        for spine in ax.spines.values():
            spine.set_color('#223449')
        ax.grid(alpha=.12, color='#abc0da')
        ax.set_axisbelow(True)
        ax.axhline(bars[-1].close, color='#7ea9d3', alpha=.55, linestyle='--', linewidth=.8)
        symbol = snapshot.asset.symbol  # Validated identifier, not provider HTML.
        closed = datetime.fromtimestamp(snapshot.closed_at, timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        fig.text(.08, .92, 'POCKET IQ', color='#53baff', fontsize=14, weight='bold')
        # Language-neutral chart annotations; all prose lives in the translated
        # Telegram caption, which supports every script without missing fonts.
        fig.text(.08, .86, f'{symbol}  |  M{snapshot.period//60}', color='white', fontsize=20, weight='bold')
        fig.text(.08, .09, closed, color='#b8c8dc', fontsize=11)
        fig.text(.08, .05, snapshot.source, color='#7d97b3', fontsize=10)
        if preview:
            fig.text(.5, .47, 'SYNTHETIC PREVIEW', ha='center', color='white', alpha=.25, fontsize=32, rotation=18)
        buffer = io.BytesIO()
        fig.savefig(buffer, format='png', facecolor=fig.get_facecolor())
        fig.clear()
        return buffer.getvalue()
