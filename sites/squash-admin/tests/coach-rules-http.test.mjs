import assert from 'node:assert/strict';
import { mkdtemp, readdir, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { Miniflare } from 'miniflare';
import { fixture } from './fixture.mjs';

test('coach rules require primary preview confirmation, CAS, audit, and publication before new final result',async t=>{
    const origin = 'https://pk-coach-rules.example.test';
    const config = JSON.parse(await readFile('dist/server/wrangler.json', 'utf8')), server = 'dist/server';
    const modules = (await readdir(server, { recursive: true })).filter(file => file.endsWith('.js') && file !== config.main).map(file => ({ type: 'ESModule', path: join(server, file) }));
    const persistence = await mkdtemp(join(tmpdir(), 'pk-coach-rules-test-'));
    const runtime = new Miniflare({ host: '127.0.0.1', modulesRoot: server, modules: [{ type: 'ESModule', path: join(server, config.main) }, ...modules],
        compatibilityDate: config.compatibility_date, compatibilityFlags: config.compatibility_flags, d1Databases: { DB: 'pk-coach-rules-test' }, d1Persist: persistence,
        bindings: { APP_ORIGIN: origin, PRIMARY_ADMIN_EMAIL: 'owner@example.test', ADDITIONAL_ADMIN_EMAIL: 'invited@example.test', PUBLISH_SOURCE_ID: 'synthetic-result-review-source' } });
    t.after(async () => { await runtime.dispose(); await rm(persistence, { recursive: true, force: true }); });
    const db = await runtime.getD1Database('DB');
    for (const file of (await readdir('drizzle')).filter(file => file.endsWith('.sql')).sort()) {
        const sql = await readFile(join('drizzle', file), 'utf8');
        await db.batch(sql.split('--> statement-breakpoint').map(statement => db.prepare(statement.trim())));
    }
    const sessions = new Map();
    async function api(path, body, user = 'owner', noCsrf = false) {
        const session = sessions.get(user) ?? {};
        const headers = { 'oai-authenticated-user-id': user, 'oai-authenticated-user-email': user === 'owner' ? 'owner@example.test' : 'invited@example.test', Cookie: session.cookie ?? '' };
        if (body !== undefined) Object.assign(headers, { Origin: origin, 'Content-Type': 'application/json', 'X-CSRF-Token': noCsrf ? '' : session.csrf ?? '' });
        const response = await runtime.dispatchFetch(origin + '/api/' + path, { method: body === undefined ? 'GET' : 'POST', headers, body: JSON.stringify(body) });
        const value = await response.json();
        if (response.headers.get('set-cookie')) session.cookie = response.headers.get('set-cookie').split(';')[0];
        if (value.csrf) session.csrf = value.csrf;
        sessions.set(user, session);
        return { status: response.status, body: value };
    }
    await api('status');
    assert.equal((await api('import',fixture())).status,200);
    await api('status',undefined,'invited');
    const request=async ids=>{const control=(await api('control')).body;return {revision:control.revision,sequence:control.sequence,championship_ids:ids};};
    const original=(await api('state')).body;
    assert.equal((await api('coach-rules-preview',await request(['m1']),'invited')).status,403);
    assert.equal((await api('coach-rules-preview',await request(['m1']),'owner',true)).status,403);
    assert.equal((await api('coach-rules-preview',await request(['missing']))).status,409);
    const preview=await api('coach-rules-preview',await request(['m1']));assert.equal(preview.status,200,JSON.stringify(preview.body));
    assert.deepEqual(preview.body.changes,[{id:'m1',code:'1',before:3,after:5}]);
    assert.equal((await api('state')).body.revision,original.revision);
    assert.equal((await api('coach-rules-confirm',{id:preview.body.id},'invited')).status,403);
    assert.equal((await api('local-update-confirm',{id:preview.body.id})).status,409);
    const confirmed=await api('coach-rules-confirm',{id:preview.body.id});assert.equal(confirmed.status,200,JSON.stringify(confirmed.body));assert.equal(confirmed.body.requires_publication,true);
    const applied=(await api('state')).body;
    assert.deepEqual(applied.state.coach_profile,{version:1,championship_ids:['m1']});assert.equal(applied.state.matches[0].scoring_rules.best_of,5);assert.equal(applied.state.matches[1].scoring_rules.best_of,3);
    assert.equal((await api('coach-rules-confirm',{id:preview.body.id})).status,409);
    const audit=await db.prepare('SELECT actor,parent,changes FROM revisions WHERE id=?').bind(applied.revision).first();assert.ok(audit.actor);assert.equal(audit.parent,original.revision);assert.match(audit.changes,/championship_ids/);
    let control=(await api('control')).body;
    assert.equal((await api('publish',{revision:control.revision,sequence:control.sequence})).status,200);
    const stale=await api('coach-rules-preview',await request(['m1']));assert.equal(stale.status,200);
    control=(await api('control')).body;
    const result={rules:{best_of:5,target:11,margin:2},outcome:'walkover',winner:'a',games:[]};
    const saved=await api('quick-result',{revision:control.revision,sequence:control.sequence,match_id:'m1',expected_result:null,result});assert.equal(saved.status,200,JSON.stringify(saved.body));
    assert.equal((await api('coach-rules-confirm',{id:stale.body.id})).status,409);
    const before=(await api('state')).body;
    const expired=await api('coach-rules-preview',await request(['m1']));await db.prepare('UPDATE previews SET created=? WHERE id=?').bind(Date.now()-700000,expired.body.id).run();assert.equal((await api('coach-rules-confirm',{id:expired.body.id})).status,409);
    assert.deepEqual((await api('state')).body.state,before.state);
    const afterResult=await api('coach-rules-preview',await request(['m1']));assert.deepEqual(afterResult.body.changes,[]);assert.equal((await api('coach-rules-confirm',{id:afterResult.body.id})).status,200);
    const final=(await api('state')).body.state;assert.equal(final.matches[0].championship,true);assert.equal(final.matches[0].scoring_rules.best_of,5);assert.deepEqual(final.matches[0].result,result);
});
