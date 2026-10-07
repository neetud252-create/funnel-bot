"""Private admin metrics; daily unique users and event counts in India time."""
import logging
from aiogram import BaseMiddleware
from aiogram.filters import Command
import db
import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS activity_stats (
 tg_id BIGINT NOT NULL, kind TEXT NOT NULL, campaign TEXT NOT NULL DEFAULT '',
 day DATE NOT NULL, events INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(tg_id,kind,campaign,day)
);
CREATE INDEX IF NOT EXISTS activity_stats_day ON activity_stats(day,kind);
INSERT INTO bot_settings(setting_key,setting_value)
 VALUES('activity_tracking_started_at',now()::text) ON CONFLICT DO NOTHING;
"""


async def record(uid, kind, campaign=''):
    if uid in config.ADMIN_IDS or getattr(db, 'pool', None) is None:
        return
    try:
        async with db.pool.acquire() as conn:
            await conn.execute("INSERT INTO activity_stats(tg_id,kind,campaign,day) "
                "VALUES($1,$2,$3,(now() AT TIME ZONE 'Asia/Kolkata')::date) "
                "ON CONFLICT(tg_id,kind,campaign,day) DO UPDATE "
                "SET events=activity_stats.events+1", uid, kind, campaign)
    except Exception:
        # Metrics must never interrupt registration, gifts or signals.
        logging.exception('Activity tracking failed kind=%s', kind)


class ActivityMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, 'from_user', None)
        message = getattr(event, 'message', None)
        chat = getattr(event, 'chat', None) or getattr(message, 'chat', None)
        if user and not user.is_bot and chat and chat.type == 'private':
            await record(user.id, 'active')
        return await handler(event, data)


def rate(numerator, denominator):
    return f'{100 * numerator / denominator:.1f}%' if denominator else 'N/A'


def format_report(users, metrics, campaigns, started):
    get = lambda kind: metrics.get(kind, {}).get('people', 0)
    total = lambda kind: metrics.get(kind, {}).get('events', 0)
    text = (
        'Go+ ADMIN STATISTICS\nTimezone: India (Asia/Kolkata)\n\n'
        f'Total stored users: {users["total"]}\n'
        f'New today / 7 days / 30 days: {users["new_today"]} / {users["new_7"]} / {users["new_30"]}\n'
        f'Active today / 7 days / 30 days: {users["active_today"]} / {users["active_7"]} / {users["active_30"]}\n\n'
        'Current account totals\n'
        f'UID submitted: {users["uid"]}\nActivated (verified): {users["verified"]}\n'
        f'Gift recipients: {users["gifts"]}\nGift-confirmation taps (users): {users["ack"]}\n'
        f'Blocked / unsubscribed: {users["blocked"]} / {users["optout"]}\n\n'
        'Tracked funnel — last 30 calendar days\n'
        f'Registration screen opened: {get("registration_opened")} users\n'
        f'UID submitted: {get("uid_submitted")} users\n'
        f'Referral account confirmed: {get("referral_confirmed")} users\n'
        f'Deposit verified: {get("deposit_verified")} users\n'
        f'Gift deliveries: {total("gift_delivered")} sends to {get("gift_delivered")} users\n'
        f'Gift confirmations: {get("gift_ack")} users\n\n'
        'First-tracked referral cohort — last 30 days\n'
        f'First tracked referral confirmations: {users["cohort"]}\n'
        f'Of these, deposit verified afterward: {users["converted"]}\n'
        f'Registration-to-deposit conversion: {rate(users["converted"], users["cohort"])}\n\n'
        'Latest broadcasts (clicks tracked since analytics launch)\n'
    )
    if not campaigns:
        text += 'No queued broadcasts yet.\n'
    for c in campaigns:
        text += (f'{c["id"]}: delivered {c["sent"]}, blocked {c["blocked"]}, '
                 f'failed {c["failed"]}, uncertain {c["uncertain"]}; '
                 f'button clickers {c["clickers"]}\n')
    return text + (f'\nActivity tracking began: {started}\n'
        'Admins excluded. Active means a message or button interaction, not a broadcast delivery. '
        'Gift confirmation records a tap, not a verified download. '
        'Older activity and clicks are unavailable. Cohort conversion can grow as users return.')


async def report():
    async with db.pool.acquire() as conn:
        async with conn.transaction(isolation='repeatable_read', readonly=True):
            users = dict(await conn.fetchrow("""
              WITH clock AS (SELECT (now() AT TIME ZONE 'Asia/Kolkata')::date AS today),
              cohort AS (
                SELECT tg_id, min(day) AS first_day FROM activity_stats
                WHERE kind='referral_confirmed' AND NOT (tg_id=ANY($1::bigint[])) GROUP BY tg_id
              )
              SELECT count(*) AS total,
               count(*) FILTER(WHERE (created_at AT TIME ZONE 'Asia/Kolkata')::date=c.today) AS new_today,
               count(*) FILTER(WHERE (created_at AT TIME ZONE 'Asia/Kolkata')::date>=c.today-6) AS new_7,
               count(*) FILTER(WHERE (created_at AT TIME ZONE 'Asia/Kolkata')::date>=c.today-29) AS new_30,
               count(*) FILTER(WHERE uid IS NOT NULL AND uid<>'') AS uid,
               count(*) FILTER(WHERE verified) AS verified,
               count(*) FILTER(WHERE gift_sent_at IS NOT NULL) AS gifts,
               count(*) FILTER(WHERE gift_acknowledged_at IS NOT NULL) AS ack,
               count(*) FILTER(WHERE broadcast_blocked) AS blocked,
               count(*) FILTER(WHERE broadcast_opt_out) AS optout,
               (SELECT count(DISTINCT tg_id) FROM activity_stats WHERE kind='active' AND day=c.today
                 AND NOT(tg_id=ANY($1::bigint[]))) AS active_today,
               (SELECT count(DISTINCT tg_id) FROM activity_stats WHERE kind='active' AND day>=c.today-6
                 AND NOT(tg_id=ANY($1::bigint[]))) AS active_7,
               (SELECT count(DISTINCT tg_id) FROM activity_stats WHERE kind='active' AND day>=c.today-29
                 AND NOT(tg_id=ANY($1::bigint[]))) AS active_30,
               (SELECT count(*) FROM cohort WHERE first_day>=c.today-29) AS cohort,
               (SELECT count(*) FROM cohort r WHERE first_day>=c.today-29 AND EXISTS(
                 SELECT 1 FROM activity_stats d WHERE d.tg_id=r.tg_id AND d.kind='deposit_verified'
                   AND d.day>=r.first_day)) AS converted
              FROM users CROSS JOIN clock c WHERE NOT(tg_id=ANY($1::bigint[])) GROUP BY c.today
            """, list(config.ADMIN_IDS)) or {})
            # Empty installations still return a useful zero-valued dashboard.
            for key in ('total','new_today','new_7','new_30','uid','verified','gifts','ack',
                        'blocked','optout','active_today','active_7','active_30','cohort','converted'):
                users.setdefault(key, 0)
            rows = await conn.fetch("SELECT kind,count(DISTINCT tg_id) AS people,sum(events) AS events "
                "FROM activity_stats WHERE day >= (now() AT TIME ZONE 'Asia/Kolkata')::date-29 "
                "AND NOT(tg_id=ANY($1::bigint[])) GROUP BY kind", list(config.ADMIN_IDS))
            campaigns = await conn.fetch("""
              SELECT b.id,
               count(*) FILTER(WHERE d.status='sent') AS sent,
               count(*) FILTER(WHERE d.status='blocked') AS blocked,
               count(*) FILTER(WHERE d.status='failed') AS failed,
               count(*) FILTER(WHERE d.status='uncertain') AS uncertain,
               (SELECT count(DISTINCT a.tg_id) FROM activity_stats a
                 WHERE a.campaign=b.id AND a.kind='broadcast_click'
                 AND NOT(a.tg_id=ANY($1::bigint[]))) AS clickers
              FROM broadcasts b LEFT JOIN broadcast_deliveries d ON d.broadcast_id=b.id
              WHERE b.status IN ('queued','running','complete')
              GROUP BY b.id,b.created_at ORDER BY b.created_at DESC LIMIT 3
            """, list(config.ADMIN_IDS))
            started = await conn.fetchval("SELECT to_char(setting_value::timestamptz "
                "AT TIME ZONE 'Asia/Kolkata','YYYY-MM-DD HH24:MI') || ' IST' "
                "FROM bot_settings WHERE setting_key='activity_tracking_started_at'")
    return format_report(users, {r['kind']: dict(r) for r in rows}, campaigns, started)


def install(dp):
    async def stats(message):
        if message.from_user.id not in config.ADMIN_IDS or message.chat.type != 'private':
            return
        try:
            await message.answer(await report())
        except Exception:
            logging.exception('Admin statistics failed')
            await message.answer('Statistics are temporarily unavailable. Please retry shortly.')
    dp.message.register(stats, Command('stats'))
