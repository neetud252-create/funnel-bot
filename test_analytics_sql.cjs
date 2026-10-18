// Run with NODE_PATH pointing at an installed @electric-sql/pglite package.
// Uses an in-memory PostgreSQL database with synthetic users; never production.
const assert = require('node:assert/strict');
const {spawnSync} = require('node:child_process');
const {PGlite} = require('@electric-sql/pglite');
const source = spawnSync(process.env.PYTHON || 'python', ['-c',
  "import os,json; os.environ.setdefault('DATABASE_URL','postgresql://unused/unused'); import db,broadcast,analytics,analytics_reports as r; print(json.dumps({'schema':db.SCHEMA+broadcast.SCHEMA+analytics.SCHEMA,'queries':r.QUERIES,'user_query':r.USERS}))"],
  {encoding:'utf8'});
if(source.status) throw Error(source.stderr);
const {schema, queries, user_query} = JSON.parse(source.stdout);

(async()=>{
  const db = new PGlite();
  try {
    await db.exec(schema);
    await db.exec(schema); // Migrations must be safe on every restart.
    for(const [name,sql] of Object.entries(queries)) {
      await db.query(sql+' LIMIT $3 OFFSET $4', [[1],30,11,0]);
    }
    await db.exec(`
      UPDATE bot_settings SET setting_value=(now()-interval '40 days')::text
        WHERE setting_key='detailed_analytics_started_at';
      INSERT INTO clicks(cid,utm_source,utm_campaign) VALUES('campaign-a','facebook','launch');
      INSERT INTO users(tg_id,username,created_at,verified,ref_code)
      SELECT id,'user_'||id,(date_trunc('day',now() AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata')
          - age*interval '1 day'+interval '6 hours',verified,ref
      FROM (VALUES (101,10,true,'campaign-a'),(102,5,false,NULL),(103,2,false,NULL),
        (104,2,false,NULL),(201,80,true,NULL),(1,2,true,NULL)) u(id,age,verified,ref);
    `);
    async function event(uid, kind, action, age, hours=0, screen='',mid=null) {
      return db.query(`INSERT INTO analytics_events(tg_id,kind,action,screen,message_id,created_at)
        SELECT $1,$2,$3,$4,$5,(date_trunc('day',now() AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata')
          - $6::int*interval '1 day'+($7::int+6)*interval '1 hour'`,[uid,kind,action,screen,mid,age,hours]);
    }
    for(const [uid,age] of [[101,10],[102,5],[103,2],[104,2],[201,2],[1,2]]) {
      await event(uid,'start','command',age);
      await event(uid,'screen_view','home',age,0,'home',uid);
    }
    for(const [uid,age] of [[101,10],[102,5],[104,2],[1,2]]) {
      await event(uid,'screen_view','register',age,1,'register',uid+1000);
    }
    await event(101,'uid_submitted','',10,2);
    await event(101,'referral_confirmed','',10,3);
    await event(101,'deposit_verified','',10,4);
    await event(104,'uid_submitted','',2,2);
    // A UID submitted BEFORE the registration step must not count as ordered conversion.
    await event(102,'uid_submitted','',5,0);
    await event(102,'deposit_verified','',5,2);
    await event(201,'deposit_verified','',2,2); // Legacy user excluded from the new-join funnel.
    await event(1,'deposit_verified','',2,2); // Admin excluded from every report.
    await event(101,'message','text_or_other',9); // D1 return.
    await event(101,'message','text_or_other',3); // D7 return.
    await db.query("INSERT INTO analytics_events(tg_id,kind,created_at) VALUES(103,'message',now()-interval '1 minute')");
    const button = JSON.stringify([{action:'go:guide',label:'Guide',type:'callback',callback:'go:guide'}]);
    for(const [uid,mid,age] of [[101,500,10],[101,501,10],[102,502,5],[201,503,45],[1,504,2]]) {
      await db.query(`INSERT INTO analytics_messages(tg_id,message_id,screen,buttons,created_at)
        VALUES($1,$2,'home',$3::jsonb,now()-$4::int*interval '1 day')`,[uid,mid,button,age]);
    }
    for(const [uid,mid] of [[101,500],[101,501],[201,503],[1,504]]) {
      await event(uid,'button_click','go:guide',1,0,'home',mid);
    }
    const rows = async name=>(await db.query(queries[name],[[1],30])).rows;
    const overview=(await rows('overview'))[0];
    assert.equal(Number(overview.total_users),5);
    assert.equal(Number(overview.new_users),4);
    assert.equal(Number(overview.active_users),5);
    assert.equal(Number(overview.button_clicks),3);
    const funnel=await rows('funnel');
    assert.deepEqual(funnel.map(x=>Number(x.users)),[4,3,2,1,1]);
    assert.equal(Number(funnel[0].pending),1);
    assert.equal(Number(funnel[1].inactive_24h),1);
    assert.equal(Number(funnel[2].inactive_24h),1);
    const guide=(await rows('buttons')).find(x=>x.action==='go:guide');
    assert.equal(Number(guide.shown_users),2);
    assert.equal(Number(guide.clicks),3);
    assert.equal(Number(guide.clickers),2);
    assert.equal(Number(guide.engaged_users),1);
    assert.equal(Number(guide.engaged_percent),50);
    const retention=await rows('retention');
    assert.equal(Number(retention[0].eligible),4);
    assert.equal(Number(retention[0].returned),1);
    assert.equal(Number(retention[1].eligible),1);
    assert.equal(Number(retention[1].returned),1);
    const sources=await rows('sources');
    assert.equal(Number(sources.find(x=>x.source==='facebook').joined),1);
    assert.equal((await rows('daily')).length,30);
    const users=await rows('users');
    assert.equal(users.length,5);
    assert(!users.some(x=>Number(x.tg_id)===1));
    assert.equal(Number((await db.query(user_query+' WHERE p.tg_id=$3 LIMIT $4 OFFSET $5',[[1],30,101,1,0])).rows[0].tg_id),101);
    for(const days of [1,7,30,90]) {
      for(const sql of Object.values(queries)) await db.query(sql+' LIMIT $3 OFFSET $4',[[1],days,11,0]);
    }
    const snapshot = new Date();
    for(const sql of Object.values(queries)) {
      await db.query(sql.replaceAll('now()','$3::timestamptz')+' LIMIT $4 OFFSET $5',[[1],30,snapshot,11,0]);
    }
    await db.query("INSERT INTO analytics_events(tg_id,kind,dedupe_key) VALUES(101,'button_click','replay') ON CONFLICT(dedupe_key) DO NOTHING");
    await db.query("INSERT INTO analytics_events(tg_id,kind,dedupe_key) VALUES(101,'button_click','replay') ON CONFLICT(dedupe_key) DO NOTHING");
    assert.equal(Number((await db.query("SELECT count(*) AS n FROM analytics_events WHERE dedupe_key='replay'")).rows[0].n),1);
    console.log('PASS: PostgreSQL schema/restarts, all 9 reports, empty state, ordered cohorts, admin exclusion, unique engagement, retention, date windows, user lookup and replay deduplication.');
  } finally {await db.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
