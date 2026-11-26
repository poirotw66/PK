import assert from 'node:assert/strict';
import { mkdtemp, readdir, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { Miniflare } from 'miniflare';
import { fixture } from './fixture.mjs';

test('reviewed results require confirmation, reject stale previews, retain correction audit and protect downstream play', async t => {
    const origin = 'https://pk-result-review.example.test';
    const config = JSON.parse(await readFile('dist/server/wrangler.json', 'utf8')), server = 'dist/server';
    const modules = (await readdir(server, { recursive: true })).filter(file => file.endsWith('.js') && file !== config.main).map(file => ({ type: 'ESModule', path: join(server, file) }));
    const persistence = await mkdtemp(join(tmpdir(), 'pk-result-review-test-'));
    const runtime = new Miniflare({ host: '127.0.0.1', modulesRoot: server, modules: [{ type: 'ESModule', path: join(server, config.main) }, ...modules],
        compatibilityDate: config.compatibility_date, compatibilityFlags: config.compatibility_flags, d1Databases: { DB: 'pk-result-review-test' }, d1Persist: persistence,
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
    const declared=fixture();declared.matches.forEach(match=>match.allowed_outcomes=['played','walkover','retired','double_forfeit']);
    assert.equal((await api('import', declared)).status, 200);
    assert.equal((await api('status', undefined, 'invited')).status, 200);
    let control = (await api('control')).body;
    assert.equal((await api('publish', { revision: control.revision, sequence: control.sequence })).status, 200);
    const rules = { best_of: 3, target: 11, margin: 2 };
    const played = { rules, winner: 'a', games: [{ side_a: 11, side_b: 6 }, { side_a: 11, side_b: 8 }] };
    const initial = (await api('state')).body;
    async function request(match_id, result) { const c = (await api('control')).body; return { revision: c.revision, sequence: c.sequence, match_id, result, reason: 'Synthetic signed result sheet' }; }
    // An unpublished identity change with identical display names must not accept a result for the old public matchup.
    const unpublished = structuredClone(initial.state);
    unpublished.players.push({ id: 'p3', name: unpublished.matches[0].side_a, rest: 0 });
    unpublished.matches[0].players = ['p3', 'p2'];
    await db.prepare('UPDATE tournament SET revision=?,payload=? WHERE id=1').bind('unpublished-same-name-identity', JSON.stringify(unpublished)).run();
    const identityPlan = await api('result-preview', await request('m1', played));
    assert.equal(identityPlan.status, 200, JSON.stringify(identityPlan.body));
    assert.equal((await api('result-confirm', { id: identityPlan.body.id })).status, 409);
    await db.prepare('UPDATE tournament SET revision=?,payload=? WHERE id=1').bind(initial.revision, JSON.stringify(initial.state)).run();
    const firstRequest = await request('m1', played);
    assert.equal((await api('result-preview', firstRequest, 'owner', true)).status, 403);
    const stale = await api('result-preview', firstRequest);
    assert.equal(stale.status, 200, JSON.stringify(stale.body));
    assert.equal(stale.body.before, null);
    assert.equal((await api('state')).body.revision, initial.revision);
    assert.equal((await api('result-confirm', { id: stale.body.id }, 'invited')).status, 409);
    const other = await api('result-preview', await request('m2', { rules, winner: 'b', outcome: 'walkover', games: [] }));
    assert.equal(other.status, 200, JSON.stringify(other.body));
    assert.equal((await api('result-confirm', { id: other.body.id })).status, 200);
    assert.equal((await api('result-confirm', { id: stale.body.id })).status, 409);
    const first = await api('result-preview', await request('m1', played));
    assert.equal((await api('result-confirm', { id: first.body.id })).status, 200);
    assert.equal((await api('result-confirm', { id: first.body.id })).status, 409);
    const beforeCorrection = (await api('state')).body;
    assert.equal((await api('result-preview', await request('m1', { rules, winner: 'b', outcome: 'walkover', games: [] }), 'invited')).status, 403);
    const correction = await api('result-preview', await request('m1', { rules, winner: null, outcome: 'double_forfeit', games: [] }));
    assert.equal(correction.status, 200, JSON.stringify(correction.body));
    assert.equal(correction.body.correction, true);
    assert.deepEqual(correction.body.before.games, played.games);
    assert.equal((await api('state')).body.revision, beforeCorrection.revision);
    assert.equal((await api('result-confirm', { id: correction.body.id })).status, 200);
    const after = (await api('state')).body;
    assert.equal(after.state.matches.find(match => match.id === 'm1').result.winner, null);
    assert.equal(after.state.matches.find(match => match.id === 'm2').result.outcome, 'walkover');
    const audit = await db.prepare('SELECT reason,changes,parent FROM revisions WHERE id=?').bind(after.revision).first();
    assert.match(audit.reason, /Synthetic signed result sheet/);
    assert.equal(audit.parent, beforeCorrection.revision);
    assert.match(audit.changes, /double_forfeit/);
    // A future-result dependency starts after preview: the stale confirmation cannot overwrite it.
    const race = await api('result-preview', await request('m1', { rules, winner: 'b', outcome: 'walkover', games: [] }));
    const playing = structuredClone(after.state);
    Object.assign(playing.matches[1], { status: 'in_progress', dependencies: [{ id: 'm1', lag: 60 }] }); delete playing.matches[1].result;
    await db.prepare('UPDATE tournament SET revision=?,payload=? WHERE id=1').bind('downstream-started', JSON.stringify(playing)).run();
    assert.equal((await api('result-confirm', { id: race.body.id })).status, 409);
    const blocked = await api('result-preview', await request('m1', played));
    assert.equal(blocked.status, 409);
    assert.match(blocked.body.error, /已開打|完賽|鎖定/);
});
