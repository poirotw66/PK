import assert from 'node:assert/strict';
import test from 'node:test';
import { buildBackup, reviewBackup } from '../outputs/backup-model.mjs';
import { canonicalDigest } from '../outputs/local-return-model.mjs';
import { fixture } from './fixture.mjs';

const origin = 'https://pk-backup.example.test';
const make = (state = fixture(), extra = {}) => buildBackup({ origin, exported_at: '2030-01-01T04:00:00.000Z', cloud_revision: 'cloud-old', sequence: 4, state, ...extra });
async function checksum(backup) { const { sha256, ...payload } = backup; return { ...payload, sha256: await canonicalDigest(payload) }; }

test('backup preserves confirmed results, source metadata and bounded audit summary', async () => {
    const state = fixture();
    state.matches[0].status = 'completed';
    state.matches[0].result = { rules: { best_of: 3, target: 11, margin: 2 }, winner: 'a', games: [{ side_a: 11, side_b: 4 }, { side_a: 11, side_b: 6 }] };
    const history = [{ id: 'cloud-old', parent: null, actor: 'operator', reason: 'Imported', created: 1 }];
    const backup = await make(state, { history });
    const restored = await reviewBackup(backup, origin, fixture());
    assert.deepEqual(restored.state.matches[0].result, state.matches[0].result);
    assert.equal(restored.completed, 1);
    assert.deepEqual(restored.backup.history, history);
    assert.equal(restored.changes.length, 1);
    assert.equal(restored.changes[0].before.result, undefined);
    assert.deepEqual(restored.changes[0].after.result, state.matches[0].result);
    assert.ok(Object.values(restored.live.courts).every(court => !court.match_id));
});

test('backup fails closed for corruption, another site, foreign participants and unexpected sensitive fields', async () => {
    const backup = await make();
    const damaged = structuredClone(backup); damaged.state.name = 'Changed';
    await assert.rejects(reviewBackup(damaged, origin, fixture()), /校驗/);
    await assert.rejects(reviewBackup(backup, 'https://other.example.test', fixture()), /另一個網站/);
    const foreign = fixture(); foreign.players[0].name = 'Another player';
    await assert.rejects(reviewBackup(backup, origin, foreign), /識別不同/);
    await assert.rejects(reviewBackup({ ...backup, sessions: [{ token: 'private' }] }, origin, fixture()));
});

test('restoration blocks active scoring, unfinished confirmations and invalid historical courts', async () => {
    const active = fixture(); active.matches[0].status = 'in_progress';
    await assert.rejects(reviewBackup(await make(active), origin, fixture()), /進行中/);
    await assert.rejects(reviewBackup(await make(), origin, active), /進行中/);
    const missing = fixture(); missing.matches[0].status = 'completed';
    await assert.rejects(reviewBackup(await make(missing), origin, fixture()), /尚未確認/);
    const invalid = await make(); invalid.live.actual_courts = { m1: 'unknown' }; invalid.live.starts = { m1: '2030-01-01T00:00:00.000Z' };
    await assert.rejects(reviewBackup(await checksum(invalid), origin, fixture()), /實際場地/);
});

test('restoration supports empty D1 and identifies settings-only changes', async () => {
    const backup = await make();
    const empty = await reviewBackup(backup, origin, null);
    assert.equal(empty.changes.length, backup.state.matches.length);
    assert.ok(empty.changes.every(change => change.before === null));
    const current = fixture(); current.name = 'New event display name';
    const settings = await reviewBackup(backup, origin, current);
    assert.equal(settings.metadataChanged, true);
    assert.equal(settings.changes.length, 0);
});
