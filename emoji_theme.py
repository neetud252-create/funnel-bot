"""Custom emoji supplied by the owner; applied after text localization."""
import re

ICONS = {
    'back': '5305522282695768654',
    'language': '5447410659077661506',
    'tips': '5422439311196834318',
    'youtube': '5897969921182142023',
    'support': '6116134696242909508',
    'review': '5438496463044752972',
    'signal': '5310278924616356636',
    'guide': '5222444124698853913',
    'access': '5427168083074628963',
    'assistant': '5773793517482546530',
    'hello': '4942823933909926751',
    'bolt': '5456140674028019486',
    'chart': '5244837092042750681',
    'pin': '5397782960512444700',
    'camera': '5260623291583831521',
    'rocket': '5188481279963715781',
    'home': '5312486108309757006',
    'chat': '5443038326535759644',
    'community': '5197304993920616826',
    'tools': '5449428597922079323',
    'unlocked': '5429405838345265327',
    'account': '5249053508681883137',
    'premium': '5433758796289685818',
    'trophy': '5188344996356448758',
    'key': '5307843983102204243',
    'locked': '5296369303661067030',
    'link': '5271604874419647061',
    'location': '5292291996717690768',
    'new': '6300677037086476915',
    'payment': '5472250091332993630',
    'success': '5895763905719832199',
    'error': '6219971367484266517',
    'question': '5436113877181941026',
    'info': '5334544901428229844',
    'warning': '5420323339723881652',
    'blocked': '5427240268589968037',
    'waiting': '5206356981094310220',
    'refresh': '5346269127059196142',
    'pause': '5116447063432758252',
    'settings': '5341715473882955310',
    'analysis': '6012529369119987828',
    'market': '5206598117738177151',
    'commodities': '5436341901290658109',
    'momentum': '5269254848703902904',
    'image': '5257974976094412956',
    'pending': '5445164438426507058',
    'currency': '5377336227533969892',
    'timer': '5195352914104694560',
    'navigation_back': '5976429872644363952',
    'play': '6015096075935813920',
    'notifications': '5458603043203327669',
    'muted': '5244807637157029775',
    'shuffle': '5129905231785624480',
    'fire': '5424972470023104089',
    'green': '5188234920639632382',
    'red': '5411225014148014586',
    'up': '5449683594425410231',
    'down': '6087173448298138241',
    'attention': '5440660757194744323',
    'gift': '5203996991054432397',
    'money': '5224257782013769471',
    'test': '5411512278740640309',
    'flag_gb': '5458416160586342331',
    'flag_ru': '5449408995691341691',
    'flag_ua': '5447309366568953338',
    'flag_in': '5447419223242449630',
    'flag_bd': '5222131025877936317',
    'flag_pk': '5305740445559570836',
    'flag_vn': '5474542319673812606',
    'flag_id': '5291997937486800451',
    'flag_tr': '5226948110873278599',
    'flag_es': '5884515815868339417',
    'flag_sa': '5188644677699510586',
    'flag_pt': '5382075788369605892',
}
GLYPHS = {
    '👋': 'hello', '🤖': 'assistant',
    '📈': 'chart', '📊': 'chart', '⚡️': 'bolt', '⚡': 'bolt',
    '💎': 'access', '📖': 'guide', '🎯': 'signal', '⭐': 'review',
    '☎️': 'support', '☎': 'support', '▶️': 'play', '▶': 'play',
    '💡': 'tips', '🌐': 'language', '🌍': 'language',
    '👇': 'back', '⬅️': 'navigation_back', '⬅': 'navigation_back',
    '📌': 'pin', '📸': 'camera', '🚀': 'rocket', '🏠': 'home',
    '💬': 'chat', '📢': 'community', '🧰': 'tools', '🔓': 'unlocked',
    '👤': 'account', '👑': 'premium', '🏆': 'trophy', '🔑': 'key',
    '🔒': 'locked', '🔗': 'link', '📍': 'location', '🆕': 'new', '💳': 'payment',
    '✅': 'success', '❌': 'error', '❓': 'question', 'ℹ️': 'info', 'ℹ': 'info',
    '❗️': 'warning', '❗': 'warning', '⚠️': 'warning', '⚠': 'warning',
    '⛔': 'blocked', '⏳': 'waiting', '🔄': 'refresh',
    '⏸️': 'pause', '⏸': 'pause', '⚙️': 'settings', '⚙': 'settings',
    '🔲': 'analysis', '💗': 'market', '🪙': 'momentum',
    '🖼️': 'image', '🖼': 'image', '▫️': 'pending', '▫': 'pending',
    '💱': 'currency', '⏱️': 'timer', '⏱': 'timer',
    '🔙': 'navigation_back', '◀️': 'navigation_back', '◀': 'navigation_back',
    '🔔': 'notifications', '🔕': 'muted', '🔀': 'shuffle', '🔥': 'fire', '🛢': 'commodities',
    '🟢': 'green', '🔴': 'red', '⬆️': 'up', '⬆': 'up', '⬇️': 'down', '⬇': 'down',
    '‼️': 'attention', '‼': 'attention', '🎁': 'gift', '💰': 'money', '🧪': 'test',
    '🇬🇧': 'flag_gb', '🇷🇺': 'flag_ru', '🇺🇦': 'flag_ua', '🇮🇳': 'flag_in',
    '🇧🇩': 'flag_bd', '🇵🇰': 'flag_pk', '🇻🇳': 'flag_vn', '🇮🇩': 'flag_id',
    '🇹🇷': 'flag_tr', '🇪🇸': 'flag_es', '🇸🇦': 'flag_sa', '🇵🇹': 'flag_pt',
}
LABELS = {
    'Get Bot Access': 'access', 'Get access to Go +': 'access',
    'Guide': 'guide', 'Quick Setup Guide': 'guide', 'How to Register': 'guide',
    'Test Signals': 'signal', 'Get a signal': 'signal', 'New Signal': 'signal',
    'Review': 'review', 'Support': 'support', 'YouTube': 'youtube',
    'YouTube channel': 'youtube', 'Forex Tips': 'tips',
    'Change Language': 'language', 'Back': 'navigation_back', 'Back to registration': 'navigation_back',
}
_glyph = re.compile('|'.join(re.escape(x) for x in sorted(GLYPHS, key=len, reverse=True)))
_protected = re.compile(
    r'(<tg-emoji\b[^>]*>.*?</tg-emoji>|<code\b[^>]*>.*?</code>|'
    r'<pre\b[^>]*>.*?</pre>|<[^>]+>|https?://[^\s<>]+)', re.S)


def html(text):
    """Preserve markup, identifiers and existing custom emojis; refresh matching icons."""
    def replace(match):
        glyph = match.group()
        return f'<tg-emoji emoji-id="{ICONS[GLYPHS[glyph]]}">{glyph}</tg-emoji>'
    parts = _protected.split(text)
    for index, part in enumerate(parts):
        if index % 2 == 0:
            parts[index] = _glyph.sub(replace, part)
        elif part.startswith('<tg-emoji'):
            inner = re.fullmatch(r'<tg-emoji\b[^>]*>(.*?)</tg-emoji>', part, re.S)
            if inner and inner[1] in GLYPHS:
                glyph = inner[1]
                parts[index] = f'<tg-emoji emoji-id="{ICONS[GLYPHS[glyph]]}">{glyph}</tg-emoji>'
    return ''.join(parts)


def button(button):
    """One native icon per button; keep its label, action, style and language flags."""
    text = button.text
    # Language buttons end with a flag. Give it the native icon slot even when
    # a selected-language check precedes the name; keep selection as a text tick.
    if (button.callback_data or '').startswith('lang:'):
        label, _, flag = text.rpartition(' ')
        name = GLYPHS.get(flag, '')
        if label and name.startswith('flag_'):
            if label.startswith('✅ '):
                label = '✓ ' + label.removeprefix('✅ ')
            return button.model_copy(update={'text': label, 'icon_custom_emoji_id': ICONS[name]})
    leading = _glyph.match(text)
    label = text[leading.end():].strip() if leading else text.removeprefix('« ').strip()
    # An explicit glyph chooses its supplied artwork. In particular, the new
    # speech-bubble Support icon must not fall back to the old phone artwork.
    name = GLYPHS[leading.group()] if leading else LABELS.get(label)
    # Category artwork is tied to the action, so it survives every language
    # without changing the flame used by M1 or other buttons.
    action = (button.callback_data or '').split(':')
    if len(action) == 4 and action[:2] == ['market', 'cat']:
        name = {'indices': 'market', 'commodities': 'commodities'}.get(action[3], name)
    if name == 'play' and 'youtube' in label.casefold():
        name = 'youtube'
    # Gallery arrows need readable labels when their glyph becomes a native icon.
    if not label and name in ('navigation_back', 'play'):
        label = 'Back' if name == 'navigation_back' else 'Next'
    # Other icon-only buttons retain their label, since Telegram requires text.
    if not name or not label:
        return button
    return button.model_copy(update={'text': label, 'icon_custom_emoji_id': ICONS[name]})
