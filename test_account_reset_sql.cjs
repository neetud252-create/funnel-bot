// Actual migration SQL against disposable PostgreSQL, never the live database.
const assert = require('node:assert/strict');
const {spawnSync} = require('node:child_process');
const {PGlite} = require('@electric-sql/pglite');
const source = spawnSync(process.env.PYTHON || 'python', ['-c', `
import ast, json
from pathlib import Path
def constants(name):
    values = {}
    for node in ast.parse(Path(name+'.py').read_text(encoding='utf-8')).body:
        if isinstance(node, ast.Assign):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name): values[target.id] = value
    return values
print(json.dumps({'reset':constants('account_reset'),
    'version':constants('elena_store')['ACTIVATION_VERSION'],
    'schema':''.join(constants(n)['SCHEMA'] for n in
       ('db','broadcast','verification_reminders','elena_store','chart_trial','chart_analysis'))}))
`], {encoding:'utf8'});
if (source.status !== 0) throw Error(source.stderr);
const {reset:r, version, schema} = JSON.parse(source.stdout);

(async () => {
    const db = new PGlite();
    const rows = async sql => (await db.query(sql)).rows;
    async function apply(fail=false) {
        await db.exec('BEGIN');
        try {
            await db.query('SELECT pg_advisory_xact_lock($1::bigint)', [r.LOCK_ID]);
            await db.exec(r.SCHEMA);
            if ((await db.query('SELECT 1 FROM account_reset_runs WHERE reset_id=$1',[r.RESET_ID])).rows.length) {
                await db.exec('COMMIT');
                return false;
            }
            await db.exec(r.LOCK_SQL);
            const count = (await rows(r.COUNTS_SQL))[0];
            await db.query(r.SNAPSHOT_SQL, [r.RESET_ID]);
            await db.exec(r.RESET_SQL);
            if (fail) await db.exec('INSERT INTO users(tg_id) VALUES(101)');
            await db.query(r.MARK_SQL, [r.RESET_ID,count.users_reset,count.verified_before,count.premium_before]);
            await db.exec('COMMIT');
            return true;
        } catch (error) {
            await db.exec('ROLLBACK');
            throw error;
        }
    }
    try {
        await db.exec(schema);
        await db.exec(`
            INSERT INTO users(tg_id,username,uid,verified,verified_at,deposit,is_premium,setup_stage,
                language,broadcast_opt_out,broadcast_blocked,created_at)
            VALUES(101,'one','111111',true,now(),150,true,'active','hi',true,false,now()-interval '30 days'),
                  (102,'two','222222',false,NULL,12,false,'deposit','ar',false,true,now()-interval '10 days'),
                  (103,'admin','333333',true,now(),80,false,'active','en',false,false,now());
            UPDATE users SET attempts=3,signals_used_today=9,last_reset_date=CURRENT_DATE,
                game_tokens=55,gift_sent_at=now(),gift_acknowledged_at=now(),account_kind='existing',
                eligibility_confirmed_at=now(),access_check_status='granted',access_check_at=now(),
                access_check_deposit=150,last_checked=now(),ref_code='campaign',ref_code_at=now(),
                nudge_msg_id=99,ui_msg_id=100,album_ids='[101,102]',welcome_message_id=10,
                welcome_pinned_at=now(),welcome_content_hash='welcome';
            INSERT INTO elena_connection(id,connection_id,owner_id,enabled,can_reply)
                VALUES(1,'approved',900,true,true);
            INSERT INTO elena_sessions(tg_id,reason,status,revision,due_at,consent,voice,history,
                reply_text,reply_nonce,memory_since,last_business_at,business_connection_id,last_message_id,
                activated_at,activation_opened_at,activation_version,stopped,handoff)
                VALUES(101,'ready','resolved',5,NULL,true,true,'["old conversation"]',
                    'old reply','old_nonce',now(),now(),'approved',30,now(),now(),1,false,false),
                    (102,'deposit','pending',9,now(),false,false,'[]',
                    NULL,NULL,NULL,now(),'approved',40,now(),NULL,1,true,true);
            INSERT INTO chart_trial_slots(token,tg_id,message_id,status,expires_at,quota_date)
                VALUES('daily',101,50,'used',now(),CURRENT_DATE),
                    ('trial',102,51,'used',now(),NULL),('pending',103,52,'pending',now()+interval '1 hour',NULL);
            INSERT INTO chart_analysis_usage(tg_id,usage_day,requests,last_request)
                VALUES(0,CURRENT_DATE,100,now()),(101,CURRENT_DATE,30,now()),(102,CURRENT_DATE,5,now());
            INSERT INTO verification_reminder_deliveries(tg_id,status) VALUES(101,'sent'),(102,'pending');
            INSERT INTO broadcasts(id,admin_id,status) VALUES('post',103,'queued');
            INSERT INTO broadcast_deliveries(broadcast_id,tg_id,status)
                VALUES('post',101,'pending'),('post',102,'sending'),('post',103,'sent');
            INSERT INTO traders(trader_id,deposit) VALUES('111111',150);
            INSERT INTO media_cache(asset_key,file_id) VALUES('activate_account','cached_file');
            INSERT INTO bot_settings(setting_key,setting_value) VALUES('gift_file','keep');
        `);
        const before = await rows('SELECT * FROM users ORDER BY tg_id');
        const beforeJson = (await rows('SELECT to_jsonb(u) AS payload FROM users u ORDER BY tg_id')).map(x=>x.payload);
        const connection = await rows('SELECT * FROM elena_connection');
        const globalBudget = await rows('SELECT * FROM chart_analysis_usage WHERE tg_id=0');

        await assert.rejects(apply(true), /duplicate key/);
        assert.deepEqual(await rows('SELECT * FROM users ORDER BY tg_id'), before,
            'A failure rolls back the complete reset');
        assert.equal((await rows('SELECT * FROM chart_trial_slots')).length, 3);
        assert.equal((await rows("SELECT to_regclass('account_reset_runs') AS name"))[0].name, null,
            'Failed migration leaves neither a completion marker nor partial backups');

        assert.equal(await apply(), true);
        const resetUsers = await rows('SELECT * FROM users ORDER BY tg_id');
        for (const [index,user] of resetUsers.entries()) {
            assert.equal(user.verified, false);
            assert.equal(user.is_premium, false);
            assert.equal(user.setup_stage, 'new');
            for (const name of ['uid','verified_at','last_checked','last_reset_date','account_kind',
                'eligibility_confirmed_at','access_check_status','access_check_at','access_check_deposit',
                'gift_sent_at','gift_acknowledged_at','ref_code','ref_code_at','nudge_msg_id'])
                assert.equal(user[name], null, name);
            for (const name of ['deposit','attempts','signals_used_today','game_tokens'])
                assert.equal(Number(user[name]), 0, name);
            for (const name of ['tg_id','username','created_at','language','broadcast_opt_out','broadcast_blocked',
                'ui_msg_id','album_ids','welcome_message_id','welcome_pinned_at','welcome_content_hash'])
                assert.deepEqual(user[name], before[index][name], name);
        }
        const sessions = await rows('SELECT * FROM elena_sessions ORDER BY tg_id');
        assert.equal(sessions.length, 3, 'Even previously verified/admin users without Elena state are gated');
        for (const session of sessions) {
            assert.equal(session.status, 'paused');
            assert.equal(session.reason, 'inactive');
            assert.equal(session.activated_at, null);
            assert.equal(session.activation_opened_at, null);
            assert.equal(session.activation_version, 0);
            assert.equal(session.due_at, null);
            assert.equal(session.last_business_at, null);
            assert.equal(session.consent, false);
            assert.equal(session.reply_text, null);
            assert.deepEqual(session.history, []);
        }
        assert.equal(sessions[0].last_message_id, 30, 'Old incoming-message replay remains blocked');
        assert.equal(sessions[1].stopped, true);
        assert.equal(sessions[1].handoff, true);
        assert.deepEqual(await rows('SELECT * FROM elena_connection'), connection);
        assert.deepEqual(await rows('SELECT * FROM chart_analysis_usage'), globalBudget);
        assert.equal((await rows('SELECT * FROM chart_trial_slots')).length, 0);
        assert.equal((await rows('SELECT * FROM verification_reminder_deliveries')).length, 0);
        assert.deepEqual((await rows('SELECT status FROM broadcast_deliveries ORDER BY tg_id')).map(x=>x.status),
            ['cancelled','cancelled','sent']);
        assert.equal(Number((await rows('SELECT deposit FROM traders'))[0].deposit), 150,
            'Reset never changes broker/postback financial records');
        assert.equal((await rows('SELECT * FROM media_cache')).length, 1);
        assert.equal((await rows('SELECT * FROM bot_settings')).length, 1);
        const run = (await rows('SELECT * FROM account_reset_runs'))[0];
        assert.equal(run.users_reset, 3);
        assert.equal(run.verified_before, 2);
        assert.equal(run.premium_before, 1);
        const snapshots = await rows('SELECT * FROM account_reset_snapshots');
        assert.deepEqual(snapshots.filter(s=>s.source_table==='users').map(s=>s.payload).sort((a,b)=>a.tg_id-b.tg_id),
            beforeJson);
        assert.ok(!JSON.stringify(snapshots).includes('old conversation'));
        assert.ok(!JSON.stringify(snapshots).includes('old reply'));

        // A later boot must not erase fresh activation, UID verification or usage.
        await db.exec(`UPDATE users SET uid='444444',verified=true,verified_at=now(),signals_used_today=1 WHERE tg_id=101;
            UPDATE elena_sessions SET activated_at=now(),activation_version=${version} WHERE tg_id=101;
            INSERT INTO chart_trial_slots(token,tg_id,message_id,status,expires_at)
                VALUES('new',102,53,'used',now());`);
        const fresh = await rows('SELECT * FROM users ORDER BY tg_id');
        await db.exec(schema);
        assert.equal(await apply(), false);
        assert.deepEqual(await rows('SELECT * FROM users ORDER BY tg_id'), fresh);
        assert.equal((await rows('SELECT * FROM chart_trial_slots')).length, 1);
        assert.equal((await rows('SELECT * FROM account_reset_snapshots')).length, snapshots.length);
        console.log('PASS: all-account reset, rollback on failure, recovery records, fresh verification/trials, queue cancellation, opt-outs, connection preservation and restart idempotency.');
    } finally { await db.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
