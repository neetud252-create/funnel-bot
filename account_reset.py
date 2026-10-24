"""Owner-requested, one-time reset of all customer setup and access state."""
import logging

RESET_ID = '2026-10-25-all-accounts-v1'
LOCK_ID = 8790198821

SCHEMA = """
CREATE TABLE IF NOT EXISTS account_reset_runs (
 reset_id TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 users_reset INTEGER NOT NULL, verified_before INTEGER NOT NULL,
 premium_before INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS account_reset_snapshots (
 reset_id TEXT NOT NULL, source_table TEXT NOT NULL, row_key TEXT NOT NULL,
 payload JSONB NOT NULL, PRIMARY KEY(reset_id,source_table,row_key)
);
"""

LOCK_SQL = """LOCK TABLE users, elena_sessions, chart_trial_slots,
    chart_analysis_usage, verification_reminder_deliveries, broadcast_deliveries
    IN SHARE ROW EXCLUSIVE MODE"""
COUNTS_SQL = """SELECT count(*)::int AS users_reset,
    count(*) FILTER (WHERE verified)::int AS verified_before,
    count(*) FILTER (WHERE is_premium)::int AS premium_before FROM users"""

# Recovery records stay in the same private database. Do not retain discarded
# conversations/voice reply text in the recovery snapshot or emit user data to logs.
SNAPSHOT_SQL = """INSERT INTO account_reset_snapshots(reset_id,source_table,row_key,payload)
 SELECT $1,'users',tg_id::text,to_jsonb(u) FROM users u
 UNION ALL SELECT $1,'elena_sessions',tg_id::text,
   to_jsonb(e)-ARRAY['history','reply_text','reply_nonce','memory_since'] FROM elena_sessions e
 UNION ALL SELECT $1,'chart_trial_slots',token,to_jsonb(s) FROM chart_trial_slots s
   WHERE tg_id IN (SELECT tg_id FROM users)
 UNION ALL SELECT $1,'chart_analysis_usage',tg_id::text,to_jsonb(a) FROM chart_analysis_usage a
   WHERE tg_id<>0 AND tg_id IN (SELECT tg_id FROM users)
 UNION ALL SELECT $1,'verification_reminder_deliveries',tg_id::text,to_jsonb(d)
   FROM verification_reminder_deliveries d
 UNION ALL SELECT $1,'broadcast_deliveries',broadcast_id||':'||tg_id::text,to_jsonb(b)
   FROM broadcast_deliveries b WHERE status IN ('pending','sending')
     AND tg_id IN (SELECT tg_id FROM users)"""

RESET_SQL = """
UPDATE users SET uid=NULL,verified=FALSE,deposit=0,attempts=0,last_checked=NULL,
 verified_at=NULL,is_premium=FALSE,signals_used_today=0,last_reset_date=NULL,
 game_tokens=0,gift_sent_at=NULL,gift_acknowledged_at=NULL,
 setup_stage='new',account_kind=NULL,eligibility_confirmed_at=NULL,
 access_check_status=NULL,access_check_at=NULL,access_check_deposit=NULL,
 ref_code=NULL,ref_code_at=NULL,nudge_msg_id=NULL;

-- A paused record for EVERY account prevents a historical-user reminder blast.
-- Only fresh user interaction/activation may resume the normal setup flow.
-- Retain stop/handoff choices and the message high-water mark for deduplication.
INSERT INTO elena_sessions(tg_id,reason,status)
 SELECT tg_id,'inactive','paused' FROM users
 ON CONFLICT(tg_id) DO UPDATE SET reason='inactive',status='paused',
 revision=elena_sessions.revision+1,due_at=NULL,sent_count=0,attempts=0,
 consent=FALSE,voice=FALSE,history='[]'::jsonb,local_only=FALSE,
 ai_day=NULL,ai_count=0,voice_count=0,last_ai_at=NULL,
 reply_text=NULL,reply_nonce=NULL,voice_sent=FALSE,memory_since=NULL,
 last_business_at=NULL,activated_at=NULL,activation_opened_at=NULL,
 activation_attempts=0,activation_retry_at=NULL,activation_version=0,updated_at=now();

DELETE FROM chart_trial_slots WHERE tg_id IN (SELECT tg_id FROM users);
DELETE FROM chart_analysis_usage WHERE tg_id<>0 AND tg_id IN (SELECT tg_id FROM users);
DELETE FROM verification_reminder_deliveries WHERE tg_id IN (SELECT tg_id FROM users);
UPDATE broadcast_deliveries SET status='cancelled'
 WHERE status IN ('pending','sending') AND tg_id IN (SELECT tg_id FROM users);
"""

MARK_SQL = """INSERT INTO account_reset_runs(reset_id,users_reset,verified_before,premium_before)
    VALUES($1,$2,$3,$4)"""


async def apply(pool):
    """Snapshot, reset and completion marker commit together, before polling."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock($1::bigint)', LOCK_ID)
            await conn.execute(SCHEMA)
            if await conn.fetchval('SELECT 1 FROM account_reset_runs WHERE reset_id=$1', RESET_ID):
                logging.info('Account reset %s already applied; preserving new account progress', RESET_ID)
                return False
            await conn.execute(LOCK_SQL)
            counts = await conn.fetchrow(COUNTS_SQL)
            await conn.execute(SNAPSHOT_SQL, RESET_ID)
            await conn.execute(RESET_SQL)
            await conn.execute(MARK_SQL, RESET_ID, counts['users_reset'],
                               counts['verified_before'], counts['premium_before'])
    logging.info('Account reset %s applied; users=%s; previously verified=%s; previously Premium=%s',
                 RESET_ID, counts['users_reset'], counts['verified_before'], counts['premium_before'])
    return True
