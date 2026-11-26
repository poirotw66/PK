import assert from 'node:assert/strict';
import { mkdtemp, readdir, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { Miniflare } from 'miniflare';
import { fixture } from './fixture.mjs';
import { canonicalDigest } from '../outputs/local-return-model.mjs';
import { emptyLive } from '../outputs/publication-model.mjs';

test('private cloud handoff reviews, saves and rejects stale or unauthorized replacements', async t => {
    const origin = 'https://pk-admin.example.test';
    const config = JSON.parse(await readFile('dist/server/wrangler.json', 'utf8'));
    const server = 'dist/server';
    const modules = (await readdir(server, { recursive: true })).filter(file => file.endsWith('.js') && file !== config.main)
        .map(file => ({ type: 'ESModule', path: join(server, file) }));
    const persistence = await mkdtemp(join(tmpdir(), 'pk-handoff-test-'));
    const runtime = new Miniflare({
        host: '127.0.0.1', modulesRoot: server,
        modules: [{ type: 'ESModule', path: join(server, config.main) }, ...modules],
        compatibilityDate: config.compatibility_date, compatibilityFlags: config.compatibility_flags,
        d1Databases: { DB: 'pk-handoff-test' }, d1Persist: persistence,
        bindings: { APP_ORIGIN: origin, PRIMARY_ADMIN_EMAIL: 'owner@example.test', ADDITIONAL_ADMIN_EMAIL: 'invited@example.test' },
    });
    t.after(async () => { await runtime.dispose(); await rm(persistence, { recursive: true, force: true }); });
    const db = await runtime.getD1Database('DB');
    for (const file of (await readdir('drizzle')).filter(file => file.endsWith('.sql')).sort()) {
        const sql = await readFile(join('drizzle', file), 'utf8');
        await db.batch(sql.split('--> statement-breakpoint').map(statement => db.prepare(statement.trim())));
    }
    const owner = { id: 'owner', email: 'owner@example.test' }, invited = { id: 'invited', email: 'invited@example.test' };
    async function api(path, user, { body, cookie, csrf, requestOrigin = origin } = {}) {
        const headers = new Headers();
        if (user) { headers.set('oai-authenticated-user-id', user.id); headers.set('oai-authenticated-user-email', user.email); }
        if (cookie) headers.set('Cookie', cookie);
        if (csrf) headers.set('X-CSRF-Token', csrf);
        if (body !== undefined) { headers.set('Origin', requestOrigin); headers.set('Content-Type', 'application/json'); }
        const response = await runtime.dispatchFetch(origin + '/api/' + path, { method: body === undefined ? 'GET' : 'POST', headers, body: JSON.stringify(body) });
        return { status: response.status, body: await response.json(), cookie: response.headers.get('set-cookie')?.split(';')[0], disposition: response.headers.get('content-disposition') };
    }
    assert.equal((await api('local-return', null)).status, 401);
    const login = await api('status', owner);
    const auth = { cookie: login.cookie, csrf: login.body.csrf };
    assert.equal((await api('local-return', owner, auth)).status, 404);
    const imported = await api('import', owner, { ...auth, body: fixture() });
    assert.equal(imported.status, 200);
    const secondary = await api('status', invited);
    const secondaryAuth = { cookie: secondary.cookie, csrf: secondary.body.csrf };
    assert.equal((await api('local-return', invited, secondaryAuth)).status, 403);
    const first = await api('local-return', owner, auth);
    assert.equal(first.status, 200, JSON.stringify(first.body));
    assert.match(first.disposition, /attachment/);
    assert.equal(first.body.cloud_revision, imported.body.revision);
    const { sha256, ...returnPayload } = first.body;
    assert.equal(sha256, await canonicalDigest(returnPayload));
    async function makeUpdate(exported, source) {
        const state = structuredClone(exported.state);
        state.source_revision = source;
        state.matches[1].assignment.start += 15;
        const payload = { format: 'pk-local-update-v1', base_cloud_revision: exported.cloud_revision, base_source_revision: exported.source_revision,
            origin, base_sequence: exported.sequence, state };
        return { ...payload, sha256: await canonicalDigest(payload) };
    }
    const envelope = await makeUpdate(first.body, 'next-local-revision');
    const request = { envelope, reason: 'Synthetic local replan' };
    assert.equal((await api('local-update-preview', invited, { ...secondaryAuth, body: request })).status, 403);
    assert.equal((await api('local-update-preview', owner, { cookie: auth.cookie, body: request })).status, 403);
    assert.equal((await api('local-update-preview', owner, { ...auth, requestOrigin: 'https://foreign.example.test', body: request })).status, 403);
    const preview = await api('local-update-preview', owner, { ...auth, body: request });
    assert.equal(preview.status, 200, JSON.stringify(preview.body));
    assert.equal(preview.body.local_update, true);
    assert.equal(preview.body.changes.length, 1);
    const before = await api('state', owner, auth);
    assert.equal(before.body.state.source_revision, first.body.source_revision);
    assert.equal((await api('local-update-confirm', invited, { ...secondaryAuth, body: { id: preview.body.id } })).status, 403);
    const saved = await api('local-update-confirm', owner, { ...auth, body: { id: preview.body.id } });
    assert.equal(saved.status, 200, JSON.stringify(saved.body));
    assert.equal((await api('local-update-confirm', owner, { ...auth, body: { id: preview.body.id } })).status, 409);
    const after = await api('state', owner, auth);
    assert.equal(after.body.state.source_revision, envelope.state.source_revision);
    assert.equal(after.body.history[0].parent, imported.body.revision);
    assert.match(after.body.history[0].reason, /Local replan/);
    assert.equal((await api('local-update-preview', owner, { ...auth, body: request })).status, 409);
    const latest = await api('local-return', owner, auth);
    const sequenceUpdate = await makeUpdate(latest.body, 'sequence-local-revision');
    const sequencePlan = await api('local-update-preview', owner, { ...auth, body: { envelope: sequenceUpdate, reason: 'Synthetic publish race' } });
    assert.equal(sequencePlan.status, 200, JSON.stringify(sequencePlan.body));
    await db.prepare('INSERT INTO public_state (id,sequence,dispatch_id,schedule_revision,payload,published_at,ack_sequence) VALUES (1,1,?,?,?,?,0)')
        .bind('synthetic-dispatch', saved.body.revision, JSON.stringify({ schedule: latest.body.state, live: emptyLive(latest.body.state) }), '2030-01-01T00:00:00.000Z').run();
    assert.equal((await api('local-update-confirm', owner, { ...auth, body: { id: sequencePlan.body.id } })).status, 409);
    const published = await api('local-return', owner, auth);
    assert.equal(published.body.sequence, 1);
    const next = await makeUpdate(published.body, 'third-local-revision');
    const stale = await api('local-update-preview', owner, { ...auth, body: { envelope: next, reason: 'Synthetic stale race' } });
    assert.equal(stale.status, 200, JSON.stringify(stale.body));
    await db.prepare('UPDATE tournament SET revision=? WHERE id=1').bind('concurrent-cloud-revision').run();
    assert.equal((await api('local-update-confirm', owner, { ...auth, body: { id: stale.body.id } })).status, 409);
    const unchanged = await api('state', owner, auth);
    assert.equal(unchanged.body.state.source_revision, envelope.state.source_revision);
    const activeState = structuredClone(unchanged.body.state);
    activeState.matches[0].status = 'in_progress';
    await db.prepare('UPDATE tournament SET payload=? WHERE id=1').bind(JSON.stringify(activeState)).run();
    assert.equal((await api('local-return', owner, auth)).status, 409);
});
