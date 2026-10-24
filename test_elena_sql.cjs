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
    if isinstance(fn, ast.AsyncFunctionDef) and fn.name in ('claim', 'due', 'finish'):
        queries[fn.name] = next(n.value for n in ast.walk(fn)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.value.startswith(('SELECT ', 'UPDATE ')))
print(json.dumps({'schema': ''.join(schema(n) for n in
    ('db', 'broadcast', 'verification_reminders', 'elena_store')), 'queries': queries}))
`], {encoding:'utf8'});
if (source.status !== 0) throw Error(source.stderr || 'Python SQL extraction failed');
const {schema, queries} = JSON.parse(source.stdout);

(async () => {
    const db = new PGlite();
    try {
        await db.exec(schema);
        await db.exec(schema);
        await db.exec(`
            INSERT INTO users(tg_id,attempts) VALUES (101,77),(102,88),(103,99);
            INSERT INTO elena_sessions(tg_id,reason,due_at)
                SELECT tg_id,'uid',now()-interval '1 minute' FROM users;
        `);
        const due = (await db.query(queries.due, [[103]])).rows;
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
        assert.equal((await db.query(queries.due, [[103]])).rows.length, 0);
        console.log('PASS: Elena PostgreSQL migrations, due jobs, claim counters, duplicate suppression, input revisions, verification, opt-out, Stop and shared reminder limit.');
    } finally {
        await db.close();
    }
})().catch(error => {console.error(error);process.exitCode=1;});
