import assert from 'node:assert/strict';
import { mkdtemp, readdir, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { Miniflare } from 'miniflare';
import { fixture } from './fixture.mjs';

test('owner backup restore previews safely, preserves identity, rejects stale plans and republishes monotonically', async t => {
    const origin = 'https://pk-backup.example.test';
    const config = JSON.parse(await readFile('dist/server/wrangler.json', 'utf8'));
    const server = 'dist/server';
    const modules = (await readdir(server, { recursive: true })).filter(file => file.endsWith('.js') && file !== config.main).map(file => ({ type: 'ESModule', path: join(server, file) }));
    const persistence = await mkdtemp(join(tmpdir(), 'pk-backup-test-'));
    const runtime = new Miniflare({ host: '127.0.0.1', modulesRoot: server, modules: [{ type: 'ESModule', path: join(server, config.main) }, ...modules],
        compatibilityDate: config.compatibility_date, compatibilityFlags: config.compatibility_flags,
        d1Databases: { DB: 'pk-backup-test' }, d1Persist: persistence,
        bindings: { APP_ORIGIN: origin, PRIMARY_ADMIN_EMAIL: 'owner@example.test', ADDITIONAL_ADMIN_EMAIL: 'invited@example.test', PUBLISH_SOURCE_ID: 'synthetic-backup-source' } });
    t.after(async () => { await runtime.dispose(); await rm(persistence, { recursive: true, force: true }); });
    const db = await runtime.getD1Database('DB');
    for (const file of (await readdir('drizzle')).filter(file => file.endsWith('.sql')).sort()) {
        const sql = await readFile(join('drizzle', file), 'utf8');
        await db.batch(sql.split('--> statement-breakpoint').map(statement => db.prepare(statement.trim())));
    }
    const identities = { owner: { id: 'owner', email: 'owner@example.test' }, invited: { id: 'invited', email: 'invited@example.test' } };
    const sessions = new Map();
    async function api(path, body, user = 'owner', { noCsrf = false } = {}) {
        const identity = identities[user], session = sessions.get(user) ?? {};
        const headers = { 'oai-authenticated-user-id': identity.id, 'oai-authenticated-user-email': identity.email, Cookie: session.cookie ?? '' };
        if (body !== undefined) Object.assign(headers, { Origin: origin, 'Content-Type': 'application/json', 'X-CSRF-Token': noCsrf ? '' : session.csrf ?? '' });
        const response = await runtime.dispatchFetch(origin + '/api/' + path, { method: body === undefined ? 'GET' : 'POST', headers, body: JSON.stringify(body) });
        const value = await response.json();
        if (response.headers.get('set-cookie')) session.cookie = response.headers.get('set-cookie').split(';')[0];
        if (path === 'status' && value.csrf) session.csrf = value.csrf;
        sessions.set(user, session);
        return { status: response.status, body: value };
    }
    assert.equal((await api('status')).status, 200);
    assert.equal((await api('import', fixture())).status, 200);
    assert.equal((await api('status', undefined, 'invited')).status, 200);
    const initial = (await api('control')).body;
    assert.equal((await api('publish', { revision: initial.revision, sequence: initial.sequence })).status, 200);
    const backupResponse = await api('backup');
    assert.equal(backupResponse.status, 200, JSON.stringify(backupResponse.body));
    const backup = backupResponse.body;
    assert.equal(backup.format, 'pk-admin-backup-v1');
    assert.ok(!('sessions' in backup)); assert.ok(!('administrator' in backup));
    assert.equal((await api('backup', undefined, 'invited')).status, 403);
    const request = { envelope: backup, reason: 'Synthetic recovery rehearsal' };
    assert.equal((await api('backup-preview', request, 'owner', { noCsrf: true })).status, 403);
    assert.equal((await api('backup-preview', request, 'invited')).status, 403);
    const stalePlan = await api('backup-preview', request);
    assert.equal(stalePlan.status, 200, JSON.stringify(stalePlan.body));
    const before = (await api('state')).body;
    const control = (await api('control')).body;
    const match = control.queue.find(match => match.eligible);
    assert.equal((await api('live', { revision: control.revision, sequence: control.sequence, action: 'record-result', match_id: match.id, games: [{ side_a: 11, side_b: 4 }, { side_a: 11, side_b: 7 }] })).status, 200);
    const afterResult = (await api('state')).body;
    assert.notEqual(afterResult.revision, before.revision);
    assert.equal((await api('backup-confirm', { id: stalePlan.body.id })).status, 409);
    assert.equal((await api('state')).body.revision, afterResult.revision);
    const restorePlan = await api('backup-preview', request);
    assert.equal(restorePlan.status, 200, JSON.stringify(restorePlan.body));
    assert.equal(restorePlan.body.changes.find(change => change.id === match.id).before.status, 'completed');
    assert.equal(restorePlan.body.changes.find(change => change.id === match.id).after.status, 'planned');
    assert.equal((await api('backup-confirm', { id: restorePlan.body.id }, 'invited')).status, 403);
    const authBefore = (await db.prepare('SELECT owner,username FROM administrator ORDER BY id').all()).results;
    const sessionCount = (await db.prepare('SELECT count(*) AS count FROM sessions').first()).count;
    const sequenceBefore = (await api('control')).body.sequence;
    const restored = await api('backup-confirm', { id: restorePlan.body.id });
    assert.equal(restored.status, 200, JSON.stringify(restored.body));
    const state = (await api('state')).body;
    assert.deepEqual(state.state, backup.state);
    assert.notEqual(state.revision, backup.cloud_revision);
    assert.ok((await api('control')).body.sequence > sequenceBefore);
    assert.deepEqual((await db.prepare('SELECT owner,username FROM administrator ORDER BY id').all()).results, authBefore);
    assert.equal((await db.prepare('SELECT count(*) AS count FROM sessions').first()).count, sessionCount);
    const audit = await db.prepare('SELECT actor,reason,parent FROM revisions WHERE id=?').bind(state.revision).first();
    assert.match(audit.reason, /Backup restore/);
    assert.equal(audit.parent, afterResult.revision);
    assert.equal((await api('backup-confirm', { id: restorePlan.body.id })).status, 409);
    const outgoing = await db.prepare("SELECT payload FROM public_outbox WHERE action='restore' ORDER BY sequence DESC LIMIT 1").first();
    assert.ok(outgoing);
    assert.equal(JSON.parse(outgoing.payload).schedule_revision, state.revision);
    // Rehearse recovery into an empty application database without deleting access records.
    await db.batch(['tournament', 'revisions', 'public_state', 'public_outbox', 'match_scores', 'previews'].map(table => db.prepare('DELETE FROM ' + table)));
    const emptyPlan = await api('backup-preview', request);
    assert.equal(emptyPlan.status, 200, JSON.stringify(emptyPlan.body));
    assert.equal(emptyPlan.body.initialization, true);
    assert.equal((await api('backup-confirm', { id: emptyPlan.body.id })).status, 200);
    const recovered = (await api('state')).body;
    assert.deepEqual(recovered.state, backup.state);
    assert.ok((await api('control')).body.sequence > backup.sequence);
    assert.deepEqual((await db.prepare('SELECT owner,username FROM administrator ORDER BY id').all()).results, authBefore);
    assert.equal((await db.prepare('SELECT count(*) AS count FROM sessions').first()).count, sessionCount);
    const active = structuredClone(recovered.state); active.matches[0].status = 'in_progress';
    await db.prepare('UPDATE tournament SET payload=?,revision=? WHERE id=1').bind(JSON.stringify(active), 'active-race').run();
    assert.equal((await api('backup-preview', request)).status, 409);
});
