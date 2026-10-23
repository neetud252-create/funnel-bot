"""Render validated OHLC data to a Telegram-ready candlestick PNG."""
from datetime import datetime, timezone
import io
import threading

# Matplotlib isn't thread-safe. Use the object API, with no pyplot/global GUI.
_lock = threading.Lock()


def render(snapshot, *, preview=False):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.patches import Rectangle
    from matplotlib.ticker import FuncFormatter, MaxNLocator

    with _lock:
        bars = snapshot.candles[-60:]
        fig = Figure(figsize=(12, 7), dpi=120, facecolor='#091320')
        FigureCanvasAgg(fig)
        ax = fig.add_axes((.08, .18, .84, .62), facecolor='#101e30')
        for index, bar in enumerate(bars):
            color = '#20d6a0' if bar.close >= bar.open else '#ff647a'
            ax.vlines(index, bar.low, bar.high, color=color, linewidth=1.2)
            ax.add_patch(Rectangle((index-.32, min(bar.open, bar.close)), .64,
                                  abs(bar.close-bar.open), facecolor=color, edgecolor=color))
            if bar.open == bar.close:
                ax.hlines(bar.open, index-.32, index+.32, color=color, linewidth=1.8)
        ax.set_xlim(-1, len(bars))
        low, high = min(b.low for b in bars), max(b.high for b in bars)
        pad = max((high-low)*.08, high*1e-6)
        ax.set_ylim(low-pad, high+pad)
        ax.yaxis.tick_right()
        ax.yaxis.set_major_locator(MaxNLocator(7))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f'{value:.8g}'))
        ticks = list(range(0, len(bars), 10))
        ax.set_xticks(ticks, [datetime.fromtimestamp(bars[i].time, timezone.utc).strftime('%H:%M') for i in ticks])
        ax.tick_params(colors='#b8c8dc', labelsize=10, length=0, pad=10)
        for spine in ax.spines.values():
            spine.set_color('#223449')
        ax.grid(alpha=.12, color='#abc0da')
        ax.set_axisbelow(True)
        ax.axhline(bars[-1].close, color='#7ea9d3', alpha=.55, linestyle='--', linewidth=.8)
        symbol = snapshot.asset.symbol  # Validated identifier, not provider HTML.
        closed = datetime.fromtimestamp(snapshot.closed_at, timezone.utc).strftime('%d %b %Y %H:%M UTC')
        fig.text(.08, .92, 'POCKET IQ', color='#53baff', fontsize=14, weight='bold')
        fig.text(.08, .86, f'{symbol}  |  {snapshot.period//60}-minute candles', color='white', fontsize=20, weight='bold')
        fig.text(.08, .09, f'Last candle closed: {closed}', color='#b8c8dc', fontsize=11)
        fig.text(.08, .05, f'Source: {snapshot.source}  •  Completed candles only', color='#7d97b3', fontsize=10)
        if preview:
            fig.text(.5, .47, 'SYNTHETIC PREVIEW', ha='center', color='white', alpha=.25, fontsize=32, rotation=18)
        buffer = io.BytesIO()
        fig.savefig(buffer, format='png', facecolor=fig.get_facecolor())
        fig.clear()
        return buffer.getvalue()
