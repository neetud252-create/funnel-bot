// Production reset/quota SQL against disposable PostgreSQL; no live accounts.
const assert = require('node:assert/strict');
const {spawnSync} = require('node:child_process');
const {PGlite} = require('@electric-sql/pglite');
const source = spawnSync(process.env.PYTHON || 'python', ['-c', `
import ast, json
from pathlib import Path
def tree(name):
    return ast.parse(Path(name + '.py').read_text(encoding='utf-8'))
def constant(name, key):
    return next(ast.literal_eval(n.value) for n in tree(name).body
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == key for t in n.targets))
def query(function, marker):
    fn = next(n for n in tree('chart_trial').body if isinstance(n, ast.AsyncFunctionDef) and n.name == function)
    return next(n.value for n in ast.walk(fn) if isinstance(n, ast.Constant)
        and isinstance(n.value, str) and marker in n.value)
print(json.dumps(dict(schema=constant('db','SCHEMA') + constant('chart_trial','SCHEMA'),
    reset=constant('db','_RESET_SQL'), limit=constant('chart_trial','LIMIT'),
    usage=query('_status','AND quota_date IS NULL'),
    pending=query('consume',"WHERE token=$1 AND status='pending'"))))
`], {encoding:'utf8'});
if (source.status !== 0) throw Error(source.stderr);
const sql = JSON.parse(source.stdout);

(async () => {
    const db = new PGlite();
    const rows = async (query, args=[]) => (await db.query(query, args)).rows;
    const remaining = async id => Math.max(0, sql.limit - Number((await rows(sql.usage,[id]))[0].count));
    try {
        await db.exec(sql.schema);
        await db.exec(`
            INSERT INTO users(tg_id,uid,verified,deposit,signals_used_today,last_reset_date,
                is_premium,language,game_tokens,welcome_message_id)
            VALUES(101,'123456',true,50,9,CURRENT_DATE,true,'hi',77,88),
                  (102,'654321',true,60,5,CURRENT_DATE,true,'ar',55,66);
            INSERT INTO chart_trial_slots(token,tg_id,message_id,status,expires_at,quota_date)
            VALUES('one',101,1,'used',now(),NULL),('two',101,-2,'used',now(),NULL),
                  ('pending',101,3,'pending',now()+interval '1 hour',NULL),
                  ('daily',101,4,'used',now(),CURRENT_DATE),
                  ('other',102,1,'used',now(),NULL);
        `);
        const other = await rows('SELECT * FROM users WHERE tg_id=102');
        const otherSlots = await rows('SELECT * FROM chart_trial_slots WHERE tg_id=102');
        const before = await rows('SELECT * FROM users WHERE tg_id=101');
        assert.equal(await remaining(101), 0);

        // A slot-reset failure must roll back the account reset as well.
        await db.exec(`CREATE FUNCTION reject_reset() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'test reset failure'; END $$;
            CREATE TRIGGER fail_reset BEFORE UPDATE ON chart_trial_slots
                FOR EACH ROW EXECUTE FUNCTION reject_reset();`);
        await assert.rejects(rows(sql.reset,[101]), /test reset failure/);
        assert.deepEqual(await rows('SELECT * FROM users WHERE tg_id=101'), before);
        assert.equal(await remaining(101), 0);
        await db.exec('DROP TRIGGER fail_reset ON chart_trial_slots');

        assert.deepEqual(await rows(sql.reset,[101]), [{tg_id:101}]);
        assert.equal(await remaining(101), 2, 'Both lifetime free signals are available again');
        const user = (await rows('SELECT * FROM users WHERE tg_id=101'))[0];
        assert.equal(user.verified, false);
        assert.equal(user.uid, null);
        assert.equal(user.signals_used_today, 0);
        assert.equal(user.last_reset_date, null);
        assert.equal(user.is_premium, false);
        assert.equal(user.language, 'hi');
        assert.equal(user.game_tokens, 77);
        assert.equal(user.welcome_message_id, 88);
        assert.deepEqual(await rows(sql.pending,['pending']), [], 'Old pending work cannot consume a restored slot');
        assert.equal((await rows("SELECT COUNT(*) FROM chart_trial_slots WHERE tg_id=101 AND status='released'"))[0].count, 4);
        assert.equal((await rows(`INSERT INTO chart_trial_slots(token,tg_id,message_id,status,expires_at)
            VALUES('replay',101,1,'pending',now()+interval '1 hour')
            ON CONFLICT(tg_id,message_id) DO NOTHING RETURNING token`)).length, 0,
            'Old Telegram requests remain deduplicated');
        await db.exec(`INSERT INTO chart_trial_slots(token,tg_id,message_id,status,expires_at)
            VALUES('fresh',101,5,'used',now())`);
        assert.equal(await remaining(101), 1);
        await rows(sql.reset,[101]);
        assert.equal(await remaining(101), 2, 'Each explicit devstart restores two, without accumulating credit');
        assert.deepEqual(await rows(sql.reset,[999]), []);
        assert.deepEqual(await rows('SELECT * FROM users WHERE tg_id=102'), other);
        assert.deepEqual(await rows('SELECT * FROM chart_trial_slots WHERE tg_id=102'), otherSlots);
        console.log('PASS: two free signals restored, repeated reset, stale reservations/replays blocked, atomic rollback, other users and preferences preserved.');
    } finally { await db.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
