"""Durable Elena state. No backfill: only new interactions create follow-ups."""
import json
import db

# Release-wide reset: previous contacts/activations have version 0 or 1. Never use
# deployment time here: ordinary restarts must retain new activations.
ACTIVATION_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS elena_connection (
 id INTEGER PRIMARY KEY CHECK(id=1),
 connection_id TEXT NOT NULL,
 owner_id BIGINT NOT NULL,
 enabled BOOLEAN NOT NULL DEFAULT FALSE,
 can_reply BOOLEAN NOT NULL DEFAULT FALSE,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS elena_sessions (
 tg_id BIGINT PRIMARY KEY REFERENCES users(tg_id),
 reason TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending',
 revision BIGINT NOT NULL DEFAULT 1,
 due_at TIMESTAMPTZ,
 sent_count INTEGER NOT NULL DEFAULT 0,
 attempts INTEGER NOT NULL DEFAULT 0,
 stopped BOOLEAN NOT NULL DEFAULT FALSE,
 handoff BOOLEAN NOT NULL DEFAULT FALSE,
 consent BOOLEAN NOT NULL DEFAULT FALSE,
 last_business_at TIMESTAMPTZ,
 business_connection_id TEXT,
 last_message_id BIGINT,
 voice BOOLEAN NOT NULL DEFAULT FALSE,
 history JSONB NOT NULL DEFAULT '[]'::jsonb,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS elena_due ON elena_sessions(due_at) WHERE status='pending';
ALTER TABLE elena_sessions ADD COLUMN IF NOT EXISTS ai_day DATE;
ALTER TABLE elena_sessions ADD COLUMN IF NOT EXISTS ai_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE elena_sessions ADD COLUMN IF NOT EXISTS voice_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE elena_sessions ADD COLUMN IF NOT EXISTS last_ai_at TIMESTAMPTZ;
ALTER TABLE elena_sessions ADD COLUMN IF NOT EXISTS reply_text TEXT;
ALTER TABLE elena_sessions ADD COLUMN IF NOT EXISTS reply_nonce TEXT;
ALTER TABLE elena_sessions ADD COLUMN IF NOT EXISTS voice_sent BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE elena_sessions ADD COLUMN IF NOT EXISTS local_only BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE elena_sessions ADD COLUMN IF NOT EXISTS memory_since TIMESTAMPTZ;
ALTER TABLE elena_sessions ADD COLUMN IF NOT EXISTS activated_at TIMESTAMPTZ;
ALTER TABLE elena_sessions ADD COLUMN IF NOT EXISTS activation_opened_at TIMESTAMPTZ;
ALTER TABLE elena_sessions ADD COLUMN IF NOT EXISTS activation_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE elena_sessions ADD COLUMN IF NOT EXISTS activation_retry_at TIMESTAMPTZ;
ALTER TABLE elena_sessions ADD COLUMN IF NOT EXISTS activation_version INTEGER NOT NULL DEFAULT 0;
"""


async def execute(sql, *args):
    async with db.pool.acquire() as c:
        return await c.execute(sql, *args)


async def one(sql, *args):
    async with db.pool.acquire() as c:
        return await c.fetchrow(sql, *args)


async def session(tg_id):
    return await one('SELECT * FROM elena_sessions WHERE tg_id=$1', tg_id)


async def connection():
    return await one('SELECT * FROM elena_connection WHERE id=1')


async def save_connection(item):
    # Once pinned, a recycled username cannot attach another Telegram account.
    return await one("""INSERT INTO elena_connection(id,connection_id,owner_id,enabled,can_reply)
        VALUES(1,$1,$2,$3,$4) ON CONFLICT(id) DO UPDATE SET
        connection_id=$1,enabled=$3,can_reply=$4,updated_at=now()
        WHERE elena_connection.owner_id=$2 RETURNING id""",
        item.id, item.user.id, item.is_enabled,
        bool(item.rights and item.rights.can_reply))


async def activity(tg_id):
    # Invalidate a job while the normal handler is still processing this input.
    await execute("""UPDATE elena_sessions SET revision=revision+1,
        due_at=CASE WHEN status='pending' THEN now()+interval '1 hour' ELSE due_at END
        WHERE tg_id=$1""", tg_id)


async def observe(tg_id, reason, delay):
    if reason is None:
        await execute("""UPDATE elena_sessions SET reason='ready',status='resolved',
            due_at=NULL,revision=revision+1 WHERE tg_id=$1""", tg_id)
        return
    await execute("""INSERT INTO elena_sessions(tg_id,reason,due_at,sent_count)
        SELECT $1,$2,now()+$3::double precision*interval '1 second',0
        FROM users WHERE tg_id=$1
        ON CONFLICT(tg_id) DO UPDATE SET reason=$2,revision=elena_sessions.revision+1,
        status=CASE WHEN elena_sessions.stopped OR elena_sessions.handoff THEN 'paused'
          WHEN elena_sessions.reason<>$2 OR elena_sessions.status IN ('pending','awaiting_chat') THEN 'pending'
          ELSE elena_sessions.status END,
        due_at=CASE WHEN elena_sessions.reason<>$2 OR elena_sessions.status IN ('pending','awaiting_chat')
          THEN now()+$3::double precision*interval '1 second' ELSE elena_sessions.due_at END,
        updated_at=now()""", tg_id, reason, delay)


async def incoming(tg_id, connection_id, message_id):
    return await one("""UPDATE elena_sessions SET
        last_business_at=now(),business_connection_id=$2,last_message_id=$3,
        sent_count=0,status='paused',due_at=NULL,revision=revision+1,updated_at=now()
        WHERE tg_id=$1 AND (business_connection_id IS DISTINCT FROM $2
          OR last_message_id IS NULL OR last_message_id<$3) RETURNING *""",
        tg_id, connection_id, message_id)


async def activate(tg_id, connection_id, message_id, reason, delay):
    # Only a just-recorded, authenticated incoming message can activate its own
    # sender. This never updates users.verified, deposits, trials or quotas.
    return await one("""UPDATE elena_sessions SET activated_at=now(),activation_version=$6,
        activation_opened_at=NULL,activation_attempts=0,activation_retry_at=now(),reason=$4,
        status=CASE WHEN stopped OR handoff THEN 'paused'
                    WHEN $4='ready' THEN 'resolved' ELSE 'pending' END,
        due_at=CASE WHEN stopped OR handoff OR $4='ready' THEN NULL
                    ELSE now()+$5::double precision*interval '1 second' END,
        revision=revision+1,updated_at=now()
        WHERE tg_id=$1 AND business_connection_id=$2 AND last_message_id=$3
        AND last_business_at IS NOT NULL AND (activated_at IS NULL OR activation_version<>$6)
        RETURNING *""", tg_id, connection_id, message_id, reason, delay, ACTIVATION_VERSION)


async def claim_activation(tg_id):
    return await one("""UPDATE elena_sessions SET
        activation_attempts=activation_attempts+1,
        activation_retry_at=now()+interval '3 minutes'
        WHERE tg_id=$1 AND activated_at IS NOT NULL AND activation_opened_at IS NULL
        AND activation_version=$2 AND activation_attempts<3 AND activation_retry_at<=now()
        RETURNING tg_id""", tg_id, ACTIVATION_VERSION)


async def activation_opened(tg_id):
    await execute("""UPDATE elena_sessions SET activation_opened_at=now(),
        activation_retry_at=NULL WHERE tg_id=$1 AND activated_at IS NOT NULL
        AND activation_opened_at IS NULL AND activation_version=$2""", tg_id, ACTIVATION_VERSION)


async def pending_activations(c):
    return await c.fetch("""SELECT tg_id FROM elena_sessions
        WHERE activated_at IS NOT NULL AND activation_opened_at IS NULL
        AND activation_version=$1 AND activation_attempts<3 AND activation_retry_at<=now()
        ORDER BY activation_retry_at LIMIT 20""", ACTIVATION_VERSION)


async def pause(tg_id, *, stopped=False, handoff=False):
    await execute("""UPDATE elena_sessions SET status='paused',due_at=NULL,
        stopped=stopped OR $2,handoff=handoff OR $3,revision=revision+1,
        history=CASE WHEN $2 OR $3 THEN '[]'::jsonb ELSE history END,
        consent=CASE WHEN $2 THEN FALSE ELSE consent END,
        reply_text=NULL,reply_nonce=NULL,updated_at=now()
        WHERE tg_id=$1""", tg_id, stopped, handoff)


async def consent(tg_id):
    await execute("""UPDATE elena_sessions SET consent=TRUE,stopped=FALSE,
        handoff=FALSE,local_only=FALSE,status='paused',history='[]'::jsonb,revision=revision+1,
        updated_at=now() WHERE tg_id=$1""", tg_id)


async def local_only(tg_id):
    await execute("""UPDATE elena_sessions SET local_only=TRUE,consent=FALSE,
        history='[]'::jsonb,reply_text=NULL,reply_nonce=NULL,revision=revision+1 WHERE tg_id=$1""", tg_id)


async def claim_ai(tg_id):
    return await one("""UPDATE elena_sessions SET
        ai_count=CASE WHEN ai_day=CURRENT_DATE THEN ai_count+1 ELSE 1 END,
        voice_count=CASE WHEN ai_day=CURRENT_DATE THEN voice_count ELSE 0 END,
        history=CASE WHEN memory_since IS NULL OR memory_since<now()-interval '1 day' THEN '[]'::jsonb ELSE history END,
        memory_since=CASE WHEN memory_since IS NULL OR memory_since<now()-interval '1 day' THEN now() ELSE memory_since END,
        ai_day=CURRENT_DATE,last_ai_at=now()
        WHERE tg_id=$1 AND consent AND NOT stopped AND NOT handoff
        AND (ai_day IS DISTINCT FROM CURRENT_DATE OR ai_count<40)
        AND (last_ai_at IS NULL OR last_ai_at<now()-interval '5 seconds') RETURNING *""", tg_id)


async def remember(tg_id, revision, history, reply, nonce):
    return await one("""UPDATE elena_sessions SET history=$3::jsonb,reply_text=$4,
        reply_nonce=$5,voice_sent=FALSE,updated_at=now()
        WHERE tg_id=$1 AND revision=$2 AND consent AND NOT stopped AND NOT handoff
        RETURNING tg_id""", tg_id, revision, json.dumps(history[-8:], ensure_ascii=False), reply, nonce)


async def claim_voice(tg_id, nonce):
    # One attempt per displayed reply; repeated taps cannot trigger duplicate charges.
    return await one("""UPDATE elena_sessions SET voice_sent=TRUE,voice=TRUE,
        voice_count=CASE WHEN ai_day=CURRENT_DATE THEN voice_count+1 ELSE 1 END,
        ai_count=CASE WHEN ai_day=CURRENT_DATE THEN ai_count ELSE 0 END,ai_day=CURRENT_DATE
        WHERE tg_id=$1 AND reply_nonce=$2 AND reply_text IS NOT NULL AND consent
        AND NOT stopped AND NOT handoff AND NOT voice_sent
        AND (ai_day IS DISTINCT FROM CURRENT_DATE OR voice_count<5) RETURNING *""", tg_id, nonce)


async def await_chat(c, job):
    # No send was attempted. Keep counters unchanged and honor newer input.
    await c.execute("""UPDATE elena_sessions SET status='awaiting_chat',due_at=NULL,
        updated_at=now() WHERE tg_id=$1 AND revision=$2 AND status='pending'""",
        job['tg_id'], job['revision'])


async def claim(c, job):
    return await c.fetchrow("""UPDATE elena_sessions e SET status='sending',attempts=e.attempts+1
        FROM users u WHERE e.tg_id=$1 AND e.revision=$2 AND e.status='pending'
        AND e.due_at<=now() AND e.tg_id=u.tg_id AND NOT u.verified
        AND NOT u.broadcast_opt_out AND NOT u.broadcast_blocked
        AND NOT e.stopped AND NOT e.handoff AND e.sent_count+
          CASE WHEN EXISTS(SELECT 1 FROM verification_reminder_deliveries d
               WHERE d.tg_id=e.tg_id AND d.status IN ('sent','sending','uncertain')
               AND d.attempted_at>COALESCE(e.last_business_at,'-infinity'::timestamptz))
          THEN 1 ELSE 0 END<2
        AND (SELECT enabled FROM verification_reminder_settings WHERE id=1)
        RETURNING e.*""", job['tg_id'], job['revision'])


async def finish(c, job, status, delay=None):
    await c.execute("""UPDATE elena_sessions SET status=$3,
        sent_count=sent_count+CASE WHEN $3 IN ('sent','uncertain') THEN 1 ELSE 0 END,
        due_at=CASE WHEN $4::double precision IS NULL THEN NULL
                    ELSE now()+$4::double precision*interval '1 second' END,
        updated_at=now() WHERE tg_id=$1 AND revision=$2 AND status='sending'""",
        job['tg_id'], job['revision'], status, delay)


async def due(c, admin_ids):
    return await c.fetch("""SELECT e.* FROM elena_sessions e JOIN users u USING(tg_id)
        WHERE e.status='pending' AND e.due_at<=now() AND e.sent_count+
          CASE WHEN EXISTS(SELECT 1 FROM verification_reminder_deliveries d
               WHERE d.tg_id=e.tg_id AND d.status IN ('sent','sending','uncertain')
               AND d.attempted_at>COALESCE(e.last_business_at,'-infinity'::timestamptz))
          THEN 1 ELSE 0 END<2
        AND NOT e.stopped AND NOT e.handoff AND NOT u.verified
        AND NOT u.broadcast_opt_out AND NOT u.broadcast_blocked
        AND NOT (e.tg_id=ANY($1::bigint[]))
        AND (SELECT enabled FROM verification_reminder_settings WHERE id=1)
        ORDER BY e.due_at LIMIT 20""", list(admin_ids))
