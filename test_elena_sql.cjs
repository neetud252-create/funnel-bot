// Run with NODE_PATH pointing to @electric-sql/pglite and PYTHON to Python 3.
// Exercises production SQL with synthetic users in disposable PostgreSQL.
const assert = require('node:assert/strict');
const {spawnSync} = require('node:child_process');
const {PGlite} = require('@electric-sql/pglite');
const source = spawnSync(process.env.PYTHON || 'python', ['-c', `
import ast, json
from pathlib import Path
def tree(name):
    return ast.parse(Path(name + '.py').read_text(encoding='utf-8'))
def schema(name):
    return next(ast.literal_eval(n.value) for n in tree(name).body
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'SCHEMA' for t in n.targets))
queries = {}
for fn in tree('elena_store').body:
    if isinstance(fn, ast.AsyncFunctionDef) and fn.name in ('claim', 'due', 'finish', 'await_chat', 'observe', 'incoming',
            'activate', 'claim_activation', 'activation_opened', 'pending_activations',
            'save_question', 'take_question', 'consent', 'local_only', 'pause', 'claim_ai', 'resume', 'offer_consent'):
        queries[fn.name] = next(n.value for n in ast.walk(fn)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.value.startswith(('INSERT ',) if fn.name == 'observe' else ('SELECT ', 'UPDATE ', 'WITH ')))
version = next(ast.literal_eval(n.value) for n in tree('elena_store').body
    if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'ACTIVATION_VERSION' for t in n.targets))
print(json.dumps({'schema': ''.join(schema(n) for n in
    ('db', 'broadcast', 'verification_reminders', 'elena_store')), 'queries': queries, 'version':version}))
`], {encoding:'utf8'});
if (source.status !== 0) throw Error(source.stderr || 'Python SQL extraction failed');
const {schema, queries, version} = JSON.parse(source.stdout);

(async () => {
    const db = new PGlite();
    const activate = (...args) => db.query(queries.activate, [...args,version]);
    const claimActivation = id => db.query(queries.claim_activation, [id,version]);
    const activationOpened = id => db.query(queries.activation_opened, [id,version]);
    const pendingActivations = () => db.query(queries.pending_activations, [version]);
    const dueJobs = (admins=[]) => db.query(queries.due, [admins,version]);
    try {
        // Seed already activated accounts in the PRE-UPDATE schema, then upgrade.
        await db.exec(schema.replace(/ALTER TABLE elena_sessions ADD COLUMN IF NOT EXISTS activation_version[^;]+;/, ''));
        await db.exec(`INSERT INTO users(tg_id,verified,verified_at,signals_used_today) VALUES(901,true,now(),9),(902,true,now(),7);
            INSERT INTO elena_sessions(tg_id,reason,status,activated_at,activation_opened_at,
                activation_attempts,activation_retry_at,last_business_at,business_connection_id,last_message_id)
            VALUES(901,'ready','resolved',now(),now(),3,now(),now(),'approved',10),
                  (902,'ready','resolved',now(),NULL,0,now(),now(),'approved',10);`);
        const oldUsers = (await db.query('SELECT * FROM users ORDER BY tg_id')).rows;
        await db.exec(schema);
        await db.exec(schema);
        const oldSessions = (await db.query('SELECT * FROM elena_sessions ORDER BY tg_id')).rows;
        assert.ok(oldSessions.every(row=>row.activation_version===0), 'Every old account requires this release activation');
        assert.deepEqual((await db.query('SELECT * FROM users ORDER BY tg_id')).rows, oldUsers);
        assert.equal((await pendingActivations()).rows.length, 0, 'Old pending menus cannot bypass reset');
        assert.equal((await claimActivation(902)).rows.length, 0);
        await activationOpened(902);
        assert.equal((await db.query('SELECT activation_opened_at FROM elena_sessions WHERE tg_id=902')).rows[0].activation_opened_at, null);
        await db.exec(`
            INSERT INTO users(tg_id,attempts) VALUES (101,77),(102,88),(103,99);
            INSERT INTO elena_sessions(tg_id,reason,due_at)
                SELECT tg_id,'uid',now()-interval '1 minute' FROM users WHERE tg_id IN (101,102,103);
        `);
        const due = (await dueJobs([103])).rows;
        assert.deepEqual(due.map(r=>Number(r.tg_id)).sort(), [101,102]);
        const claim = async (id, revision=1) =>
            (await db.query(queries.claim, [id,revision])).rows;
        const first = (await claim(101))[0];
        assert.equal(first.status, 'sending');
        assert.equal(first.attempts, 1);
        assert.equal((await db.query('SELECT attempts FROM users WHERE tg_id=101')).rows[0].attempts, 77);
        assert.equal((await claim(101)).length, 0, 'Only one worker can claim a job');
        await db.query(queries.finish, [101,1,'sent',null]);
        const sent = (await db.query('SELECT * FROM elena_sessions WHERE tg_id=101')).rows[0];
        assert.equal(sent.sent_count, 1);
        assert.equal(sent.due_at, null);
        assert.equal((await claim(101)).length, 0, 'Sent jobs must not be repeated');

        await db.exec('UPDATE elena_sessions SET revision=2 WHERE tg_id=102');
        assert.equal((await claim(102,1)).length, 0, 'New input invalidates a queued job');
        await db.exec('UPDATE users SET verified=true WHERE tg_id=102');
        assert.equal((await claim(102,2)).length, 0, 'Verified customers receive no setup reminder');
        await db.exec('UPDATE users SET verified=false,broadcast_opt_out=true WHERE tg_id=102');
        assert.equal((await claim(102,2)).length, 0, 'Opted-out customers receive no reminder');
        await db.exec('UPDATE users SET broadcast_opt_out=false WHERE tg_id=102');
        await db.exec('UPDATE elena_sessions SET stopped=true WHERE tg_id=102');
        assert.equal((await claim(102,2)).length, 0, 'Stop cancels delivery');
        await db.exec(`UPDATE elena_sessions SET stopped=false,sent_count=1 WHERE tg_id=102;
            INSERT INTO verification_reminder_deliveries(tg_id,status) VALUES(102,'sent');`);
        assert.equal((await claim(102,2)).length, 0, 'Legacy and Elena reminders share the two-message cap');
        assert.equal((await dueJobs([103])).rows.length, 0);

        const row = async () => (await db.query('SELECT * FROM elena_sessions WHERE tg_id=103')).rows[0];
        await db.query(queries.await_chat, [103,0]);
        assert.equal((await row()).status, 'pending', 'Stale checks cannot defer newer input');
        await db.query(queries.await_chat, [103,1]);
        let deferred = await row();
        assert.equal(deferred.status, 'awaiting_chat');
        assert.equal(deferred.due_at, null);
        assert.equal(deferred.attempts, 0, 'No Telegram attempt was made');
        assert.equal(deferred.sent_count, 0, 'No delivery consumed');
        assert.equal((await dueJobs()).rows.length, 0, 'Deferred jobs must leave the delivery queue');
        assert.equal((await claim(103)).length, 0, 'Deferred jobs cannot be claimed');
        await db.query(queries.observe, [103,'uid',60]);
        deferred = await row();
        assert.equal(deferred.status, 'pending', 'New bot activity may schedule a fresh check');
        assert.ok(deferred.due_at instanceof Date);
        assert.equal(deferred.attempts, 0);
        const revision = Number(deferred.revision);
        await db.query(queries.await_chat, [103,revision]);
        await db.query(queries.incoming, [103,'business-test',1]);
        await db.query(queries.await_chat, [103,revision]);
        deferred = await row();
        assert.equal(deferred.status, 'paused', 'A new Elena conversation must not be overwritten');
        assert.equal(deferred.business_connection_id, 'business-test');
        assert.ok(deferred.last_business_at instanceof Date);
        await db.exec("UPDATE elena_sessions SET status='awaiting_chat',stopped=true WHERE tg_id=103");
        await db.query(queries.observe, [103,'uid',60]);
        assert.equal((await row()).status, 'paused', 'Activity must not override Stop');
        await db.exec("UPDATE elena_sessions SET status='awaiting_chat',stopped=false,handoff=true WHERE tg_id=103");
        await db.query(queries.observe, [103,'uid',60]);
        assert.equal((await row()).status, 'paused', 'Activity must not override human handoff');
        // Activation requires an authenticated incoming message, never a link click.
        assert.equal((await activate(101,'approved',11,'inactive',1800)).rows.length, 0);
        await db.query(queries.incoming, [101,'approved',11]);
        assert.equal((await activate(101,'foreign',11,'inactive',1800)).rows.length, 0);
        assert.equal((await activate(101,'approved',12,'inactive',1800)).rows.length, 0);
        const original = (await db.query('SELECT * FROM users WHERE tg_id=101')).rows[0];
        const activated = (await activate(101,'approved',11,'inactive',1800)).rows[0];
        assert.ok(activated.activated_at instanceof Date);
        assert.equal(activated.status, 'pending');
        assert.ok(activated.due_at - activated.activated_at >= 1799000);
        assert.deepEqual((await db.query('SELECT * FROM users WHERE tg_id=101')).rows[0], original);
        assert.equal((await activate(101,'approved',11,'inactive',1800)).rows.length, 0);
        assert.equal((await db.query(queries.incoming, [101,'approved',11])).rows.length, 0);
        assert.equal((await pendingActivations()).rows.length, 1);
        assert.equal((await claimActivation(101)).rows.length, 1);
        assert.equal((await claimActivation(101)).rows.length, 0, 'Single delivery lease');
        await db.exec(schema); // Redeploy retains the pending activation and lease.
        assert.equal((await claimActivation(101)).rows.length, 0);
        await db.exec("UPDATE elena_sessions SET activation_retry_at=now()-interval '1 minute' WHERE tg_id=101");
        assert.equal((await claimActivation(101)).rows.length, 1, 'Failed delivery can retry');
        await activationOpened(101);
        assert.equal((await pendingActivations()).rows.length, 0);
        await db.exec("UPDATE elena_sessions SET activation_retry_at=now()-interval '1 minute' WHERE tg_id=101");
        assert.equal((await claimActivation(101)).rows.length, 0, 'Opened menus never repeat');
        await db.query(queries.incoming, [103,'approved',12]);
        const handedOff = (await activate(103,'approved',12,'inactive',1800)).rows[0];
        assert.equal(handedOff.status, 'paused');
        assert.equal(handedOff.due_at, null, 'Activation does not override human takeover');
        await db.exec('UPDATE elena_sessions SET activation_attempts=3 WHERE tg_id=103');
        assert.equal((await claimActivation(103)).rows.length, 0, 'Retries are bounded');

        // Old activation cannot suppress the new confirmation/menu delivery.
        assert.equal((await db.query(queries.incoming, [901,'approved',10])).rows.length, 0, 'Replayed old message is ignored');
        await db.query(queries.incoming, [901,'approved',11]);
        const reactivated = (await activate(901,'approved',11,'ready',1800)).rows[0];
        assert.equal(reactivated.activation_version, version);
        assert.equal(reactivated.activation_opened_at, null);
        assert.equal(reactivated.activation_attempts, 0);
        assert.equal((await claimActivation(901)).rows.length, 1);
        await activationOpened(901);
        await db.exec(schema);
        const retained = (await db.query('SELECT * FROM elena_sessions WHERE tg_id=901')).rows[0];
        assert.equal(retained.activation_version, version, 'Ordinary redeploy does not reset new activation');
        assert.ok(retained.activation_opened_at instanceof Date);
        assert.deepEqual((await db.query('SELECT * FROM users WHERE tg_id IN (901,902) ORDER BY tg_id')).rows, oldUsers);
        // Admin accounts may test the customer path after this release activation.
        await db.exec(`INSERT INTO users(tg_id) VALUES(201),(202),(203);
            INSERT INTO elena_sessions(tg_id,reason,due_at,activated_at,activation_version)
            VALUES(201,'uid',now()-interval '1 minute',now(),${version}),
                  (202,'uid',now()-interval '1 minute',now(),0),
                  (203,'uid',now()-interval '1 minute',NULL,${version});`);
        const adminJobs = async () => (await dueJobs([201,202,203])).rows
            .filter(row=>Number(row.tg_id)>=201 && Number(row.tg_id)<=203);
        assert.deepEqual((await adminJobs()).map(row=>Number(row.tg_id)), [201]);
        for (const flag of ['stopped','handoff']) {
            await db.exec(`UPDATE elena_sessions SET ${flag}=true WHERE tg_id=201`);
            assert.equal((await adminJobs()).length, 0, `Admin still respects ${flag}`);
            await db.exec(`UPDATE elena_sessions SET ${flag}=false WHERE tg_id=201`);
        }
        for (const flag of ['verified','broadcast_opt_out','broadcast_blocked']) {
            await db.exec(`UPDATE users SET ${flag}=true WHERE tg_id=201`);
            assert.equal((await adminJobs()).length, 0, `Admin still respects ${flag}`);
            await db.exec(`UPDATE users SET ${flag}=false WHERE tg_id=201`);
        }
        await db.exec('UPDATE verification_reminder_settings SET enabled=false WHERE id=1');
        assert.equal((await adminJobs()).length, 0);
        await db.exec('UPDATE verification_reminder_settings SET enabled=true WHERE id=1');
        await db.exec('UPDATE elena_sessions SET sent_count=2 WHERE tg_id=201');
        assert.equal((await adminJobs()).length, 0, 'Admin still respects the unanswered-message cap');
        await db.exec('UPDATE elena_sessions SET sent_count=0 WHERE tg_id=201');
        assert.equal((await claim(201)).length, 1, 'Activated admin can claim a due follow-up');
        assert.equal((await claim(201)).length, 0, 'Admin delivery is also duplicate-safe');
        await db.exec(`INSERT INTO users(tg_id) VALUES(301);
            INSERT INTO elena_sessions(tg_id,reason) VALUES(301,'registration');`);
        await db.query(queries.save_question, [301,1,'My registration form fails.']);
        assert.equal((await db.query(queries.take_question, [301])).rows.length, 0, 'No question is sent before consent');
        assert.equal((await db.query(queries.offer_consent,[301,1,false])).rows.length,1);
        await db.exec(schema); // Restart/migration must retain the one-time offer.
        assert.equal((await db.query(queries.offer_consent,[301,1,false])).rows.length,0);
        assert.equal((await db.query(queries.offer_consent,[301,1,true])).rows.length,1,'Explicit /ai may reopen the choice');
        await db.query(queries.consent, [301]);
        assert.equal((await db.query(queries.take_question, [301])).rows[0].pending_question, 'My registration form fails.');
        assert.equal((await db.query(queries.take_question, [301])).rows.length, 0, 'Consent double-taps cannot repeat the question');
        assert.equal((await db.query(queries.claim_ai, [301])).rows[0].ai_count, 1);
        assert.equal((await db.query(queries.claim_ai, [301])).rows[0].ai_count, 2, 'Quick conversational follow-up is not dropped');
        await db.exec('UPDATE elena_sessions SET ai_count=40 WHERE tg_id=301');
        assert.equal((await db.query(queries.claim_ai, [301])).rows.length, 0, 'Daily cost limit remains enforced');
        for (const choice of ['local_only', 'stop', 'human']) {
            await db.exec(`UPDATE elena_sessions SET pending_question='redacted question',
                pending_question_at=now(),stopped=false,handoff=false WHERE tg_id=301`);
            if (choice==='local_only') await db.query(queries.local_only, [301]);
            else await db.query(queries.pause, [301,choice==='stop',choice==='human']);
            assert.equal((await db.query('SELECT pending_question FROM elena_sessions WHERE tg_id=301')).rows[0].pending_question, null);
        }
        await db.exec(`UPDATE elena_sessions SET pending_question='expired',pending_question_at=now()-interval '2 days',
            consent=true,stopped=false,handoff=false WHERE tg_id=301`);
        assert.equal((await db.query(queries.take_question, [301])).rows.length, 0, 'Expired questions never leave local storage');
        // A greeting must not clear Stop; explicit resume preserves AI and notification choices.
        await db.query(queries.pause, [301,true,true]);
        assert.equal((await db.query(queries.consent,[301])).rows.length,0,'Concurrent Stop/handoff wins over consent');
        assert.equal((await db.query(queries.offer_consent,[301,1,true])).rows.length,0,'Explicit prompt cannot bypass Stop');
        await db.exec('UPDATE users SET broadcast_opt_out=true WHERE tg_id=301');
        let paused = (await db.query('SELECT * FROM elena_sessions WHERE tg_id=301')).rows[0];
        assert.equal((await db.query(queries.resume,[301,Number(paused.revision)-1])).rows.length,0,
            'A stale resume cannot override newer customer/operator input');
        const resumed = (await db.query(queries.resume,[301,paused.revision])).rows[0];
        assert.equal(resumed.handoff,false);
        assert.equal(resumed.stopped,false);
        assert.equal(resumed.consent,false,'Resume does not grant AI consent');
        assert.equal(resumed.status,'paused');
        assert.equal(resumed.due_at,null,'Resume does not schedule outreach');
        assert.equal((await db.query('SELECT broadcast_opt_out FROM users WHERE tg_id=301')).rows[0].broadcast_opt_out,true);
        assert.equal((await db.query(queries.resume,[301,resumed.revision])).rows.length,0,'Resume is idempotent');
        console.log('PASS: explicit resume, stale race rejection, preserved AI consent and reminder preferences.');
        console.log('PASS: pending question consent, single consumption, fast follow-ups, daily cap and privacy controls.');
        console.log('PASS: activated admin follow-ups, old/unactivated admin exclusion and unchanged delivery safeguards.');
        console.log('PASS: all-account activation reset migration, old-job exclusion, new-message reactivation, entitlement preservation, duplicate suppression, durable delivery and restart stability.');
    } finally {
        await db.close();
    }
})().catch(error => {console.error(error);process.exitCode=1;});
