import assert from 'node:assert/strict';
import { mkdtemp, readdir, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { Miniflare } from 'miniflare';
import { fixture } from './fixture.mjs';

test('completed games can be recorded without starting live scoring and are available for display', async t => {
    const origin = 'https://pk-results.example.test';
    const config = JSON.parse(await readFile('dist/server/wrangler.json', 'utf8'));
    const server = 'dist/server';
    const modules = (await readdir(server, { recursive: true })).filter(file => file.endsWith('.js') && file !== config.main)
        .map(file => ({ type: 'ESModule', path: join(server, file) }));
    const persistence = await mkdtemp(join(tmpdir(), 'pk-results-test-'));
    const runtime = new Miniflare({ host: '127.0.0.1', modulesRoot: server,
        modules: [{ type: 'ESModule', path: join(server, config.main) }, ...modules],
        compatibilityDate: config.compatibility_date, compatibilityFlags: config.compatibility_flags,
        d1Databases: { DB: 'pk-results-test' }, d1Persist: persistence,
        bindings: { APP_ORIGIN: origin, PRIMARY_ADMIN_EMAIL: 'owner@example.test', PUBLISH_SOURCE_ID: 'synthetic-result-source' } });
    t.after(async () => { await runtime.dispose(); await rm(persistence, { recursive: true, force: true }); });
    const db = await runtime.getD1Database('DB');
    for (const file of (await readdir('drizzle')).filter(file => file.endsWith('.sql')).sort()) {
        const sql = await readFile(join('drizzle', file), 'utf8');
        await db.batch(sql.split('--> statement-breakpoint').map(statement => db.prepare(statement.trim())));
    }
    let cookie = '', csrf = '';
    async function api(path, body) {
        const headers = { 'oai-authenticated-user-id': 'owner', 'oai-authenticated-user-email': 'owner@example.test', Cookie: cookie };
        if (body !== undefined) Object.assign(headers, { Origin: origin, 'Content-Type': 'application/json', 'X-CSRF-Token': csrf });
        const response = await runtime.dispatchFetch(origin + '/api/' + path, { method: body === undefined ? 'GET' : 'POST', headers, body: JSON.stringify(body) });
        if (response.headers.get('set-cookie')) cookie = response.headers.get('set-cookie').split(';')[0];
        return { status: response.status, body: await response.json() };
    }
    csrf = (await api('status')).body.csrf;
    assert.equal((await api('import', fixture())).status, 200);
    const initial = (await api('control')).body;
    assert.equal((await api('publish', { revision: initial.revision, sequence: initial.sequence })).status, 200);
    const control = (await api('control')).body;
    assert.ok(control.dispatch.courts.every(court => court.matches.length === 0));
    const match = control.queue.find(match => match.eligible);
    assert.ok(match);
    const queued = await api('dispatch', { revision: control.revision, sequence: control.sequence, match_id: match.id, court_id: 'a' });
    assert.equal(queued.status, 200, JSON.stringify(queued.body));
    assert.equal(queued.body.dispatch.courts[0].matches[0].id, match.id);
    const stale = await api('dispatch', { revision: control.revision, sequence: control.sequence, match_id: match.id, court_id: null });
    assert.equal(stale.status, 409);
    Object.assign(control, queued.body);
    const games = [{ side_a: 11, side_b: 6 }, { side_a: 11, side_b: 8 }];
    const request = { revision: control.revision, sequence: control.sequence, match_id: match.id, expected_result: null, result: {games,winner:'a',rules:match.scoring_rules,outcome:'played'} };
    const invalid = await api('quick-result', { ...request, result:{...request.result,games: [{ side_a: 11, side_b: 10 }, games[1]]} });
    assert.equal(invalid.status, 409);
    const recorded = await api('quick-result', request);
    assert.equal(recorded.status, 200, JSON.stringify(recorded.body));
    assert.ok(recorded.body.courts.every(court => !court.current));
    const saved = (await api('state')).body.state.matches.find(row => row.id === match.id);
    assert.equal(saved.status, 'completed');
    assert.deepEqual(saved.result.games, games);
    assert.equal(saved.result.winner, 'a');
    assert.ok(recorded.body.dispatch.courts.every(court => !court.matches.some(row => row.id === match.id)));
    assert.ok(!recorded.body.queue.some(row => row.id === match.id));
    assert.equal((await api('quick-result', request)).status, 409);
});
