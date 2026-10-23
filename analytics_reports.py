"""Admin-only analytics reports and bounded CSV exports, all dates in India time."""
import csv
from datetime import datetime, timedelta, timezone
import io
import logging

from aiogram import F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup
import config
import db

IST = timezone(timedelta(hours=5, minutes=30))
DAYS = (1, 7, 30, 90)
LIMIT = 50000
COMMON = """
WITH bounds AS (
 SELECT (date_trunc('day',now() AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata')
          - ($2::int-1)*interval '1 day' AS since, now() AS until
), people AS (SELECT * FROM users WHERE NOT(tg_id=ANY($1::bigint[]))
 AND created_at<=(SELECT until FROM bounds)),
ev AS (SELECT e.* FROM analytics_events e,bounds b
 WHERE e.created_at>=b.since AND e.created_at<=b.until AND NOT(e.tg_id=ANY($1::bigint[])))
"""
OVERVIEW = COMMON + """
SELECT b.since,b.until,
 (SELECT setting_value::timestamptz FROM bot_settings WHERE setting_key='detailed_analytics_started_at') AS tracking_started,
 (SELECT count(*) FROM people) AS total_users,
 (SELECT count(*) FROM people WHERE created_at>=b.since AND created_at<=b.until) AS new_users,
 (SELECT count(DISTINCT tg_id) FROM ev WHERE kind IN ('start','message','button_click')) AS active_users,
 (SELECT count(*) FROM (SELECT tg_id FROM ev WHERE kind IN ('start','message','button_click') GROUP BY tg_id
    HAVING count(DISTINCT (created_at AT TIME ZONE 'Asia/Kolkata')::date)>1) r) AS returning_users,
 (SELECT count(*) FROM ev WHERE kind='button_click') AS button_clicks,
 (SELECT count(*) FROM ev WHERE kind='link_open') AS link_opens,
 (SELECT count(*) FROM ev WHERE kind IN ('handler_error','delivery_error')) AS errors,
 (SELECT count(*) FROM people WHERE verified) AS verified,
 (SELECT count(*) FROM people WHERE is_premium) AS premium,
 (SELECT count(*) FROM people WHERE broadcast_blocked) AS blocked,
 (SELECT count(*) FROM people WHERE broadcast_opt_out) AS unsubscribed,
 (SELECT count(*) FROM people WHERE uid IS NOT NULL AND uid<>'') AS submitted_uid,
 (SELECT count(DISTINCT tg_id) FROM ev WHERE kind='deposit_verified') AS verified_in_period
FROM bounds b
"""
BUTTONS = COMMON + """
, exposures AS (
 SELECT m.tg_id,m.message_id,m.screen,j->>'action' AS action,j->>'label' AS label,j->>'type' AS type
 FROM analytics_messages m,bounds b,LATERAL jsonb_array_elements(m.buttons) j
 WHERE m.created_at>=b.since AND m.created_at<=b.until AND NOT(m.tg_id=ANY($1::bigint[]))
), shown AS (
 SELECT screen,action,max(label) AS label,max(type) AS type,count(DISTINCT tg_id) AS shown_users
 FROM exposures GROUP BY screen,action
), hits AS (
 SELECT screen,action,max(label) AS label,count(*) AS clicks,count(DISTINCT tg_id) AS clickers,
  count(*) FILTER(WHERE kind='link_open') AS link_opens
 FROM ev WHERE kind IN ('button_click','link_open') GROUP BY screen,action
), engaged AS (
 SELECT x.screen,x.action,count(DISTINCT x.tg_id) AS engaged_users FROM exposures x
 WHERE EXISTS(SELECT 1 FROM ev e WHERE e.tg_id=x.tg_id AND e.message_id=x.message_id
   AND e.action=x.action AND e.kind IN ('button_click','link_open')) GROUP BY x.screen,x.action
)
SELECT coalesce(s.screen,h.screen) AS screen,coalesce(s.action,h.action) AS action,
 coalesce(s.label,h.label) AS label,coalesce(s.type,CASE WHEN h.link_opens>0 THEN 'link' ELSE 'callback' END) AS type,
 coalesce(s.shown_users,0) AS shown_users,coalesce(h.clicks,0) AS clicks,coalesce(h.clickers,0) AS clickers,
 coalesce(g.engaged_users,0) AS engaged_users,
 round(100.0*g.engaged_users/nullif(s.shown_users,0),1) AS engaged_percent
FROM shown s FULL JOIN hits h USING(screen,action)
LEFT JOIN engaged g ON g.screen=coalesce(s.screen,h.screen) AND g.action=coalesce(s.action,h.action)
ORDER BY clicks DESC,shown_users DESC,screen,action
"""
FUNNEL = COMMON + """
, cohort AS (
 SELECT p.* FROM people p,bounds b WHERE p.created_at>=greatest(b.since,
 (SELECT setting_value::timestamptz FROM bot_settings WHERE setting_key='detailed_analytics_started_at'))
 AND p.created_at<=b.until
), steps AS (
 SELECT p.tg_id,p.created_at AS joined,r.at AS registration,u.at AS uid,c.at AS confirmed,v.at AS verified,
 coalesce(a.at,p.created_at) AS last_active FROM cohort p
 LEFT JOIN LATERAL (SELECT min(created_at) AS at FROM ev WHERE tg_id=p.tg_id
   AND kind='screen_view' AND screen='register' AND created_at>=p.created_at) r ON true
 LEFT JOIN LATERAL (SELECT min(created_at) AS at FROM ev WHERE tg_id=p.tg_id
   AND kind='uid_submitted' AND created_at>=r.at) u ON true
 LEFT JOIN LATERAL (SELECT min(created_at) AS at FROM ev WHERE tg_id=p.tg_id
   AND kind='referral_confirmed' AND created_at>=u.at) c ON true
 LEFT JOIN LATERAL (SELECT min(created_at) AS at FROM ev WHERE tg_id=p.tg_id
   AND kind='deposit_verified' AND created_at>=c.at) v ON true
 LEFT JOIN LATERAL (SELECT max(created_at) AS at FROM ev WHERE tg_id=p.tg_id
   AND kind IN ('start','message','button_click')) a ON true
), stages AS (
 SELECT s.tg_id,s.last_active,t.* FROM steps s CROSS JOIN LATERAL (VALUES
 (1,'Joined bot',joined,registration),(2,'Registration screen',registration,uid),
 (3,'UID submitted',uid,confirmed),(4,'Referral confirmed',confirmed,verified),
 (5,'Deposit verified',verified,NULL::timestamptz)) t(position,stage,reached_at,next_at)
)
SELECT position,stage,count(reached_at) AS users,
 count(*) FILTER(WHERE reached_at IS NOT NULL AND next_at IS NULL AND position<5) AS not_progressed,
 count(*) FILTER(WHERE reached_at IS NOT NULL AND next_at IS NULL AND position<5
   AND last_active<now()-interval '24 hours') AS inactive_24h,
 count(*) FILTER(WHERE reached_at IS NOT NULL AND next_at IS NULL AND position<5
   AND last_active>=now()-interval '24 hours') AS pending,
 round(avg(extract(epoch FROM next_at-reached_at)/60)::numeric,1) AS average_minutes_to_next
FROM stages GROUP BY position,stage ORDER BY position
"""
DROPOFFS = COMMON + """
, last_active AS (
 SELECT tg_id,max(created_at) AS at FROM ev WHERE kind IN ('start','message','button_click') GROUP BY tg_id
), last_screen AS (
 SELECT DISTINCT ON(e.tg_id) e.tg_id,e.screen FROM analytics_events e,bounds b
 WHERE e.kind='screen_view' AND e.created_at<=b.until AND NOT(e.tg_id=ANY($1::bigint[]))
 ORDER BY e.tg_id,e.created_at DESC,e.id DESC
)
SELECT coalesce(s.screen,'No tracked screen') AS screen,count(*) AS inactive_users,
 count(*) FILTER(WHERE NOT p.verified) AS not_verified,
 count(*) FILTER(WHERE p.verified) AS verified
FROM last_active a JOIN people p USING(tg_id) LEFT JOIN last_screen s USING(tg_id)
WHERE a.at<now()-interval '24 hours' GROUP BY s.screen ORDER BY inactive_users DESC,screen
"""
DAILY = COMMON + """
, dates AS (SELECT generate_series((b.since AT TIME ZONE 'Asia/Kolkata')::date,
 (b.until AT TIME ZONE 'Asia/Kolkata')::date,interval '1 day')::date AS day FROM bounds b),
users_by_day AS (SELECT (created_at AT TIME ZONE 'Asia/Kolkata')::date AS day,count(*) AS joined
 FROM people,bounds b WHERE created_at>=b.since AND created_at<=b.until GROUP BY 1),
events_by_day AS (SELECT (created_at AT TIME ZONE 'Asia/Kolkata')::date AS day,
 count(DISTINCT tg_id) FILTER(WHERE kind IN ('start','message','button_click')) AS active,
 count(*) FILTER(WHERE kind='button_click') AS clicks,count(*) FILTER(WHERE kind='link_open') AS link_opens,
 count(DISTINCT tg_id) FILTER(WHERE kind='deposit_verified') AS verified FROM ev GROUP BY 1)
SELECT d.day,coalesce(u.joined,0) AS joined,coalesce(e.active,0) AS active,
 coalesce(e.clicks,0) AS clicks,coalesce(e.link_opens,0) AS link_opens,coalesce(e.verified,0) AS verified
FROM dates d LEFT JOIN users_by_day u USING(day) LEFT JOIN events_by_day e USING(day) ORDER BY d.day DESC
"""
SOURCES = COMMON + """
SELECT coalesce(nullif(c.utm_source,''),'Direct / unknown') AS source,
 coalesce(nullif(c.utm_campaign,''),'Unspecified') AS campaign,
 count(*) AS joined,count(*) FILTER(WHERE p.uid IS NOT NULL AND p.uid<>'') AS uid_submitted,
 count(*) FILTER(WHERE p.verified) AS verified,
 round(100.0*count(*) FILTER(WHERE p.verified)/nullif(count(*),0),1) AS verified_percent
FROM people p CROSS JOIN bounds b LEFT JOIN clicks c ON c.cid=p.ref_code
WHERE p.created_at>=b.since AND p.created_at<=b.until GROUP BY 1,2 ORDER BY joined DESC,source,campaign
"""
RETENTION = COMMON + """
, cohorts AS (SELECT tg_id,(created_at AT TIME ZONE 'Asia/Kolkata')::date AS day
 FROM people,bounds b WHERE created_at>=greatest(b.since,
 (SELECT setting_value::timestamptz FROM bot_settings WHERE setting_key='detailed_analytics_started_at'))
 AND created_at<=b.until)
SELECT offset_days,count(*) FILTER(WHERE c.day+offset_days < (now() AT TIME ZONE 'Asia/Kolkata')::date) AS eligible,
 count(*) FILTER(WHERE c.day+offset_days < (now() AT TIME ZONE 'Asia/Kolkata')::date AND EXISTS(
 SELECT 1 FROM analytics_events e WHERE e.tg_id=c.tg_id AND e.kind IN ('start','message','button_click')
 AND (e.created_at AT TIME ZONE 'Asia/Kolkata')::date=c.day+offset_days)) AS returned
FROM (VALUES(1),(7)) t(offset_days) LEFT JOIN cohorts c ON true GROUP BY offset_days ORDER BY offset_days
"""
USERS = COMMON + """
, totals AS (SELECT tg_id,count(*) FILTER(WHERE kind='button_click') AS clicks,
 count(*) FILTER(WHERE kind='link_open') AS link_opens FROM ev GROUP BY tg_id),
latest AS (SELECT tg_id,max(created_at) FILTER(WHERE kind IN ('start','message','button_click')) AS last_active
 FROM analytics_events WHERE NOT(tg_id=ANY($1::bigint[]))
 AND created_at<=(SELECT until FROM bounds) GROUP BY tg_id),
screens AS (SELECT DISTINCT ON(tg_id) tg_id,screen FROM analytics_events
 WHERE kind='screen_view' AND NOT(tg_id=ANY($1::bigint[]))
 AND created_at<=(SELECT until FROM bounds) ORDER BY tg_id,created_at DESC,id DESC)
SELECT p.tg_id,p.username,p.created_at AS joined_at,p.language,p.verified,p.is_premium,
 (p.uid IS NOT NULL AND p.uid<>'') AS uid_submitted,p.deposit,p.gift_sent_at,p.gift_acknowledged_at,
 p.broadcast_blocked,p.broadcast_opt_out,l.last_active,s.screen AS last_screen,
 coalesce(t.clicks,0) AS clicks_in_period,coalesce(t.link_opens,0) AS link_opens_in_period,
 coalesce(nullif(c.utm_source,''),'Direct / unknown') AS source,c.utm_campaign AS campaign,
 CASE WHEN l.last_active IS NULL THEN 'No detailed activity yet'
 WHEN l.last_active<now()-interval '24 hours' THEN 'Inactive 24h+' ELSE 'Active within 24h' END AS activity
FROM people p LEFT JOIN totals t USING(tg_id) LEFT JOIN latest l USING(tg_id) LEFT JOIN screens s USING(tg_id)
LEFT JOIN clicks c ON c.cid=p.ref_code
"""
EVENTS = COMMON + """
SELECT e.created_at,e.tg_id,p.username,e.kind,e.screen,e.action,e.label,e.message_id
FROM ev e LEFT JOIN people p USING(tg_id) ORDER BY e.created_at DESC,e.id DESC
"""
QUERIES = {'overview': OVERVIEW, 'buttons': BUTTONS, 'funnel': FUNNEL, 'dropoffs': DROPOFFS,
           'daily': DAILY, 'sources': SOURCES, 'retention': RETENTION,
           'users': USERS + ' ORDER BY p.created_at DESC,p.tg_id DESC', 'events': EVENTS}
VIEWS = {'overview': 'Overview', 'buttons': 'Top buttons', 'funnel': 'Conversion funnel',
         'dropoffs': 'Inactive / drop-offs', 'daily': 'Daily activity', 'sources': 'Sources',
         'retention': 'Retention', 'users': 'Users', 'export': 'CSV downloads'}


def stamp(value):
    return value.astimezone(IST).strftime('%d %b %Y %H:%M IST') if isinstance(value, datetime) else str(value or 'Not tracked')


def percent(n, d):
    return f'{100*n/d:.1f}%' if d else 'N/A'


def clean(value, size=70):
    return str(value or '—').replace('\n', ' ').replace('\r', ' ')[:size]


def keyboard(view, days, *, offset=0, has_next=False):
    rows = [[InlineKeyboardButton(text=('✓ ' if n == days else '') + ('Today' if n == 1 else f'{n} days'),
                                  callback_data=f'an:{view}:{n}:0') for n in DAYS]]
    items = list(VIEWS.items())
    for i in range(0, len(items), 3):
        rows.append([InlineKeyboardButton(text=label, callback_data=f'an:{key}:{days}:0')
                     for key, label in items[i:i+3]])
    nav = []
    if offset:
        nav.append(InlineKeyboardButton(text='Previous', callback_data=f'an:{view}:{days}:{max(0,offset-10)}'))
    if has_next:
        nav.append(InlineKeyboardButton(text='Next', callback_data=f'an:{view}:{days}:{offset+10}'))
    if nav:
        rows.append(nav)
    return rows


async def fetch(view, days, *, limit=11, offset=0, user_id=None, snapshot=None):
    if view not in QUERIES or days not in DAYS:
        raise ValueError('Invalid analytics report')
    sql = QUERIES[view]
    args = [list(config.ADMIN_IDS), days]
    if user_id is not None:
        if view == 'users':
            sql = USERS + ' WHERE p.tg_id=$3'
        elif view == 'events':
            sql = COMMON + 'SELECT created_at,tg_id,kind,screen,action,label,message_id FROM ev WHERE tg_id=$3 ORDER BY created_at DESC,id DESC'
        else:
            raise ValueError('Invalid user report')
        args.append(user_id)
    if snapshot is not None:
        # Keep time bounds stable across CSV parts, even across midnight.
        # Only fixed application SQL is rewritten; values remain bound parameters.
        sql = sql.replace('now()', f'${len(args)+1}::timestamptz')
        args.append(snapshot)
    sql += f' LIMIT ${len(args)+1} OFFSET ${len(args)+2}'
    args += [limit, offset]
    async with db.pool.acquire() as conn:
        async with conn.transaction(isolation='repeatable_read', readonly=True):
            await conn.execute("SET LOCAL statement_timeout='15s'")
            return [dict(r) for r in await conn.fetch(sql, *args)]


def format_rows(view, rows, days, offset=0):
    title = f'Pocket IQ ANALYTICS · {VIEWS[view]}\nLast {days} calendar day(s), including today · IST\n'
    if view == 'overview' and rows:
        r = rows[0]
        return title + (f'\nAll stored users: {r["total_users"]}\nNew in period: {r["new_users"]}\n'
            f'Active users: {r["active_users"]}\nActive on 2+ days: {r["returning_users"]}\n'
            f'Native button taps: {r["button_clicks"]}\nTracked link opens: {r["link_opens"]}\n'
            f'Verifications in period: {r["verified_in_period"]}\nInteraction/delivery errors: {r["errors"]}\n\n'
            f'Current totals: UID submitted {r["submitted_uid"]} · Verified {r["verified"]} · Premium {r["premium"]}\n'
            f'Blocked {r["blocked"]} · Unsubscribed {r["unsubscribed"]}\n\n'
            f'Detailed tracking began: {stamp(r["tracking_started"])}\n'
            'Older joins and account status are available; older click journeys cannot be reconstructed.\n'
            'Admins excluded. Screen views mean delivered screens, not read receipts.\n'
            'Choose a report below. /userstats <Telegram ID> opens a user timeline.')
    if not rows:
        return title + '\nNo matching data yet. New activity appears after users interact with the bot.'
    out = [title]
    for i, r in enumerate(rows, offset+1):
        if view == 'buttons':
            out.append(f'{i}. {clean(r["label"])} [{clean(r["screen"])}]\n'
                       f'   {r["clicks"]} {"link opens" if r["type"]=="link" else "taps"} · {r["clickers"]} users\n'
                       f'   Shown to {r["shown_users"]} users · shown-user engagement {percent(r["engaged_users"],r["shown_users"])}')
        elif view == 'funnel':
            out.append(f'{r["position"]}. {r["stage"]}: {r["users"]} users' + (
                f'\n   Not progressed {r["not_progressed"]}: inactive 24h+ {r["inactive_24h"]}, pending {r["pending"]}'
                f'\n   Average to next step: {r["average_minutes_to_next"] if r["average_minutes_to_next"] is not None else "N/A"} min'
                if r['position']<5 else ''))
        elif view == 'dropoffs':
            out.append(f'{clean(r["screen"])}: {r["inactive_users"]} inactive\n'
                       f'   Not verified {r["not_verified"]} · Verified {r["verified"]}')
        elif view == 'daily':
            out.append(f'{r["day"]}: new {r["joined"]} · active {r["active"]}\n'
                       f'   Taps {r["clicks"]} · link opens {r["link_opens"]} · verified {r["verified"]}')
        elif view == 'sources':
            out.append(f'{clean(r["source"])} / {clean(r["campaign"])}\n'
                       f'   Joined {r["joined"]} · UID {r["uid_submitted"]} · verified {r["verified"]} ({r["verified_percent"]}%)')
        elif view == 'retention':
            out.append(f'Day {r["offset_days"]}: {r["returned"]}/{r["eligible"]} eligible users returned ({percent(r["returned"],r["eligible"])})')
        elif view == 'users':
            out.append(f'{r["tg_id"]} · {clean(r["username"])}\n'
                       f'   {r["activity"]} · last screen {clean(r["last_screen"])}\n'
                       f'   Joined {stamp(r["joined_at"])} · verified {"yes" if r["verified"] else "no"}')
    notes = {
        'buttons': 'Shown-user engagement counts people who received and used that same button in this period. Old-message taps still count in totals. Link opens are estimates: previews/forwarded links may remain; reloads are deduplicated for 30 minutes.',
        'funnel': 'Ordered cohort: users who first joined in this period after detailed tracking began. A later stage counts only after the preceding stage. Pending means active within 24h; inactivity does not prove permanent abandonment.',
        'dropoffs': 'Users active in this period with no bot interaction for 24h+. Last screen is a delivery, not a read. This is an inactivity signal, not proof of why they left.',
        'sources': 'First-touch source from the saved landing-page tracking code. Verification is current account status for users who joined in this period.',
        'retention': 'New users since detailed tracking began; a return is an inbound bot interaction on exactly day 1 or day 7 after joining (IST). Only completed return-days enter the denominator.',
        'users': 'All stored users, newest first. Click totals in CSV use the selected period. Tap a user below for their timeline.',
    }
    if view in notes:
        out.append(notes[view])
    return '\n\n'.join(out)


def csv_bytes(rows):
    if not rows:
        return b'\xef\xbb\xbfNo data\r\n'
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    for row in rows:
        values = {}
        for key, value in row.items():
            if isinstance(value, datetime):
                value = value.astimezone(IST).isoformat()
            # Neutralize spreadsheet formulas in user-controlled names/sources.
            if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
                value = "'" + value
            values[key] = value
        writer.writerow(values)
    return stream.getvalue().encode('utf-8-sig')


async def dashboard(view, days, offset=0):
    if view == 'export':
        rows = keyboard(view, days)
        rows += [[InlineKeyboardButton(text=f'Download {key}.csv', callback_data=f'an:csv:{key}:{days}')]
                 for key in QUERIES]
        return ('Pocket IQ ANALYTICS · CSV downloads\n'
                f'Period: last {days} calendar day(s), IST. Users.csv includes every stored user; its click counts use this period.\n\n'
                'Exports contain Telegram IDs and usernames and are available only to admins. '
                'Large exports are split into parts of 50,000 rows with a Next CSV part button. '
                'Dates include the +05:30 timezone.', InlineKeyboardMarkup(inline_keyboard=rows))
    data = await fetch(view, days, offset=offset)
    rows = keyboard(view, days, offset=offset, has_next=len(data)>10)
    data = data[:10]
    if view == 'users':
        rows += [[InlineKeyboardButton(text=f'{r["tg_id"]} · {clean(r["username"],25)}',
                                      callback_data=f'an:user:{r["tg_id"]}:{days}:0')] for r in data]
    return format_rows(view,data,days,offset), InlineKeyboardMarkup(inline_keyboard=rows)


async def user_report(uid, days, offset=0):
    people = await fetch('users',days,user_id=uid)
    if not people:
        return 'No matching non-admin user is stored.', InlineKeyboardMarkup(inline_keyboard=keyboard('users',days))
    p = people[0]
    events = await fetch('events',days,user_id=uid,limit=16,offset=offset)
    text = (f'Pocket IQ USER · {uid} · {clean(p["username"])}\n'
            f'Joined: {stamp(p["joined_at"])}\nLast active: {stamp(p["last_active"])}\n'
            f'Language: {p["language"]} · {p["activity"]}\n'
            f'UID submitted: {p["uid_submitted"]} · Verified: {p["verified"]} · Premium: {p["is_premium"]}\n'
            f'Source: {clean(p["source"])} / {clean(p["campaign"])}\n'
            f'Last screen: {clean(p["last_screen"])}\nTaps: {p["clicks_in_period"]} · link opens: {p["link_opens_in_period"]}\n\n'
            f'Latest events · last {days} calendar days\n')
    for e in events[:15]:
        text += f'\n{stamp(e["created_at"])} · {e["kind"]}\n{clean(e["screen"])} · {clean(e["label"] or e["action"])}\n'
    if not events:
        text += 'No detailed events in this period. Historical activity is unavailable.'
    nav = []
    if offset:
        nav.append(InlineKeyboardButton(text='Newer events', callback_data=f'an:user:{uid}:{days}:{max(0,offset-15)}'))
    if len(events)>15:
        nav.append(InlineKeyboardButton(text='Older events', callback_data=f'an:user:{uid}:{days}:{offset+15}'))
    rows = [nav] if nav else []
    rows += [[InlineKeyboardButton(text='Back to users',callback_data=f'an:users:{days}:0')]]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def allowed(event):
    message = getattr(event,'message',None) or event
    return (event.from_user.id in config.ADMIN_IDS and getattr(message,'chat',None)
            and message.chat.type=='private')


def install(dp):
    async def command(message):
        if not allowed(message):
            return
        parts = (message.text or '').split()
        try:
            if parts[0].split('@')[0]=='/userstats':
                if len(parts)!=2 or not parts[1].isdigit():
                    await message.answer('Usage: /userstats <Telegram ID>')
                    return
                text,kb = await user_report(int(parts[1]),30)
            else:
                days = int(parts[1]) if len(parts)==2 else 30
                if days not in DAYS or len(parts)>2:
                    raise ValueError()
                text,kb = await dashboard('overview',days)
            await message.answer(text,reply_markup=kb,parse_mode=None)
        except ValueError:
            await message.answer('Usage: /analytics [1, 7, 30 or 90]\n/userstats <Telegram ID>')
        except Exception:
            logging.exception('Analytics command failed')
            await message.answer('Analytics are temporarily unavailable. Please retry shortly.')

    async def callback(cb):
        if not allowed(cb):
            await cb.answer('Unavailable')
            return
        await cb.answer()
        try:
            parts = cb.data.split(':')
            if len(parts) in (4,6) and parts[1]=='csv':
                view,days = parts[2],int(parts[3])
                offset = int(parts[4]) if len(parts)==6 else 0
                epoch = int(parts[5]) if len(parts)==6 else int(datetime.now(timezone.utc).timestamp())
                if not 0<=offset<=100_000_000 or not 0<epoch<=datetime.now(timezone.utc).timestamp()+60:
                    raise ValueError()
                snapshot = datetime.fromtimestamp(epoch,timezone.utc)
                data = await fetch(view,days,limit=LIMIT+1,offset=offset,snapshot=snapshot)
                capped = len(data)>LIMIT
                data = data[:LIMIT]
                content = csv_bytes(data)
                if len(content)>40_000_000:
                    await cb.message.answer('This export is too large. Choose a shorter period.')
                    return
                more = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Next CSV part',
                    callback_data=f'an:csv:{view}:{days}:{offset+LIMIT}:{epoch}')]]) if capped else None
                await cb.message.answer_document(BufferedInputFile(content,filename=f'go_plus_{view}_{days}days_part{offset//LIMIT+1}.csv'),
                    caption=f'{view}: {len(data)} rows · part {offset//LIMIT+1} · timestamps IST (+05:30). '
                    f'Activity cut-off: {stamp(snapshot)}. Account status is current.'+
                    (' More rows are available in the next part.' if capped else ''),reply_markup=more,parse_mode=None)
                return
            if len(parts)==5 and parts[1]=='user':
                uid,days,offset = map(int,parts[2:])
                if days not in DAYS or not 0<=offset<=1_000_000 or uid<=0:
                    raise ValueError()
                text,kb = await user_report(uid,days,offset)
            elif len(parts)==4 and parts[1] in VIEWS:
                view,days,offset = parts[1],int(parts[2]),int(parts[3])
                if days not in DAYS or not 0<=offset<=1_000_000:
                    raise ValueError()
                text,kb = await dashboard(view,days,offset)
            else:
                raise ValueError()
            from aiogram.exceptions import TelegramBadRequest
            try:
                await cb.message.edit_text(text,reply_markup=kb,parse_mode=None)
            except TelegramBadRequest as exc:
                if 'message is not modified' not in str(exc).lower():
                    raise
        except ValueError:
            await cb.message.answer('Open /analytics to refresh this report.')
        except Exception:
            logging.exception('Analytics report/export failed')
            await cb.message.answer('Analytics are temporarily unavailable. Please retry shortly.')

    dp.message.register(command,Command('analytics','userstats'))
    dp.callback_query.register(callback,F.data.startswith('an:'))
