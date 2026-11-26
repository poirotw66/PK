import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { fixture } from './fixture.mjs';
import { buildCloudReturn, canonicalDigest, canonicalJson } from '../outputs/local-return-model.mjs';
import { reviewLocalUpdate } from '../outputs/local-update-model.mjs';
import { emptyLive, transitionLive } from '../outputs/publication-model.mjs';

const origin = 'https://pk-admin.example.test';
const now = '2030-01-01T00:00:00.000Z';
const result = { rules: { best_of: 3, target: 11, margin: 2 }, winner: 'a', games: [{ side_a: 11, side_b: 0 }, { side_a: 12, side_b: 10 }] };
const setup = state => ({ state, cloud_revision: 'cloud-revision', origin, exported_at: now, sequence: 2, live: emptyLive(state), start_history: [] });
async function update(state, change = {}) {
    const next = structuredClone(state);
    next.source_revision = 'new-local-revision';
    next.matches[1].assignment.start += 15;
    const payload = { format: 'pk-local-update-v1', base_cloud_revision: 'cloud-revision', base_source_revision: state.source_revision, origin, base_sequence: 2, state: next, ...change };
    return { ...payload, sha256: await canonicalDigest(payload) };
}

test('cloud handoff checksum agrees with Python canonical JSON with Chinese data', async () => {
    const state = fixture();
    state.name = '合成賽事 🏸';
    const exported = await buildCloudReturn(setup(state));
    const { sha256, ...payload } = exported;
    const process = spawnSync('python3', ['-c', 'import sys,json,hashlib; value=json.load(sys.stdin); print(hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest())'], { input: JSON.stringify(payload), encoding: 'utf8' });
    assert.equal(process.status, 0, process.stderr);
    assert.equal(sha256, process.stdout.trim());
    assert.equal(sha256, createHash('sha256').update(canonicalJson(payload)).digest('hex'));
    assert.equal(exported.source_revision, state.source_revision);
    assert.deepEqual(exported.state, state);
});

test('active scores and incomplete finished results cannot be silently omitted', async () => {
    const state = fixture();
    state.matches[0].status = 'in_progress';
    await assert.rejects(buildCloudReturn(setup(state)), /進行中/);
    state.matches[0].status = 'completed';
    await assert.rejects(buildCloudReturn(setup(state)), /缺少確認成績/);
    state.matches[0].result = result;
    const exported = await buildCloudReturn(setup(state));
    assert.deepEqual(exported.state.matches[0].result, result);
});

test('actual alternate court survives finish and canceled start removes its record', async () => {
    const state = fixture(), initial = { schedule: structuredClone(state), live: emptyLive(state) };
    const started = transitionLive(state, initial, { action: 'start', match_id: 'm1', court_id: 'b' }, now);
    const finished = transitionLive(started.main, started.publication, { action: 'end', match_id: 'm1', court_id: 'b', result }, now);
    const exported = await buildCloudReturn({ ...setup(finished.main), live: finished.publication.live });
    assert.equal(exported.state.matches[0].assignment.court, 'a');
    assert.equal(exported.live.actual_courts.m1, 'b');
    assert.equal(exported.live.starts.m1, now);
    const canceled = transitionLive(started.main, started.publication, { action: 'cancel', match_id: 'm1', court_id: 'b' }, now);
    assert.equal(canceled.publication.live.actual_courts.m1, undefined);
    assert.equal(canceled.publication.live.starts.m1, undefined);
});

test('legacy actual court needs matching historical evidence', async () => {
    const state = fixture();
    state.matches[0].status = 'completed';
    state.matches[0].result = result;
    const input = setup(state);
    input.live.starts.m1 = now;
    await assert.rejects(buildCloudReturn(input), /缺少實際場地/);
    input.start_history = [{ matches: [{ id: 'm1', status: 'in_progress', started_at: now, court_id: 'b' }] }];
    assert.equal((await buildCloudReturn(input)).live.actual_courts.m1, 'b');
});

test('local replan accepts only assignment changes and preserves confirmed results', async () => {
    const state = fixture();
    state.matches[0].status = 'completed';
    state.matches[0].result = result;
    const envelope = await update(state);
    const review = await reviewLocalUpdate(state, envelope, origin);
    assert.equal(review.changes.length, 1);
    assert.deepEqual(review.state.matches[0], state.matches[0]);
    assert.deepEqual(state.matches[1].assignment.start + 15, review.state.matches[1].assignment.start);
});

test('tampered, foreign, stale, locked, changed result and conflicting replans fail', async () => {
    const state = fixture();
    const tampered = await update(state);
    tampered.state.name = 'tampered';
    await assert.rejects(reviewLocalUpdate(state, tampered, origin), /校驗失敗/);
    await assert.rejects(reviewLocalUpdate(state, await update(state), 'https://other.example.test'), /另一個/);
    await assert.rejects(reviewLocalUpdate(state, await update(state, { base_source_revision: 'stale' }), origin), /已過期/);
    const locked = fixture();
    locked.matches[1].lock_time = true;
    await assert.rejects(reviewLocalUpdate(locked, await update(locked), origin), /鎖定/);
    const changed = await update(state);
    changed.state.matches[0].players = [];
    const { sha256, ...payload } = changed;
    changed.sha256 = await canonicalDigest(payload);
    await assert.rejects(reviewLocalUpdate(state, changed, origin), /只能調整/);
    const finished = fixture();
    finished.matches[0].status = 'completed';
    finished.matches[0].result = result;
    const changedResult = await update(finished);
    changedResult.state.matches[0].result = { ...result, winner: 'b', games: [{ side_a: 0, side_b: 11 }, { side_a: 10, side_b: 12 }] };
    const { sha256: resultDigest, ...resultPayload } = changedResult;
    changedResult.sha256 = await canonicalDigest(resultPayload);
    await assert.rejects(reviewLocalUpdate(finished, changedResult, origin), /只能調整/);
    const conflict = await update(state);
    conflict.state.matches[1].assignment.start = state.matches[0].assignment.start;
    const { sha256: oldDigest, ...conflictPayload } = conflict;
    conflict.sha256 = await canonicalDigest(conflictPayload);
    await assert.rejects(reviewLocalUpdate(state, conflict, origin), /未通過/);
});

test('reviewed local updates can declare special outcome policies without changing scoring rules',async()=>{const current=fixture(),envelope=await update(current);envelope.state.matches[0].allowed_outcomes=['played','walkover'];const {sha256,...payload}=envelope;envelope.sha256=await canonicalDigest(payload);const plan=await reviewLocalUpdate(current,envelope,origin);assert.equal(plan.policy_changes.length,1);assert.deepEqual(plan.policy_changes[0].after,['played','walkover']);envelope.state.matches[0].scoring_rules={best_of:5,target:11,margin:2};const {sha256:old,...bad}=envelope;envelope.sha256=await canonicalDigest(bad);await assert.rejects(reviewLocalUpdate(current,envelope,origin),/規則|只能/);});

async function resign(envelope) { const {sha256,...payload}=envelope;envelope.sha256=await canonicalDigest(payload);return envelope; }
function applyCoachProfile(envelope,ids) {
 envelope.state.coach_profile={version:1,championship_ids:ids};
 for(const match of envelope.state.matches){match.championship=ids.includes(match.id);match.allowed_outcomes=['played','walkover'];if(!match.result&&match.status!=='completed'&&match.status!=='in_progress')match.scoring_rules={best_of:match.championship?5:3,target:11,margin:2};}
 return envelope;
}
test('reviewed local coach profile permits only exact future final 5 and other 3 while preserving scored matches',async()=>{
 const current=fixture();Object.assign(current.matches[0],{status:'completed',result});
 const envelope=await resign(applyCoachProfile(await update(current),['m2']));
 const reviewed=await reviewLocalUpdate(current,envelope,origin);
 assert.deepEqual(reviewed.state.coach_profile,{version:1,championship_ids:['m2']});assert.deepEqual(reviewed.state.matches[0].result,result);assert.equal(reviewed.state.matches[0].scoring_rules,undefined);assert.equal(reviewed.state.matches[1].scoring_rules.best_of,5);
 for(const mutate of [
  value=>{value.state.matches[1].scoring_rules.best_of=3;},
  value=>{value.state.matches[1].scoring_rules.target=15;},
  value=>{value.state.matches[0].scoring_rules={best_of:5,target:11,margin:2};},
  value=>{value.state.matches[0].result={...result,winner:'b',games:[{side_a:1,side_b:11},{side_a:2,side_b:11}]};},
  value=>{value.state.matches[1].side_a='Changed identity';},
  value=>{value.state.matches[1].players=['p2'];},
  value=>{value.state.matches[0].championship=true;},
 ]){const changed=structuredClone(envelope);mutate(changed);await assert.rejects(reviewLocalUpdate(current,await resign(changed),origin),/規則|只能調整|標記/);}
});
test('reviewed local coach profile enforces nonfinal three and preserves established final markers',async()=>{
 const current=fixture();current.matches[0].scoring_rules={best_of:5,target:11,margin:2};
 const envelope=await resign(applyCoachProfile(await update(current),['m2']));assert.equal((await reviewLocalUpdate(current,envelope,origin)).state.matches[0].scoring_rules.best_of,3);
 envelope.state.matches[0].scoring_rules.best_of=5;await assert.rejects(reviewLocalUpdate(current,await resign(envelope),origin),/規則/);
 const established=fixture();established.matches[0].championship=true;established.matches[1].championship=false;
 const redefined=await resign(applyCoachProfile(await update(established),['m2']));await assert.rejects(reviewLocalUpdate(established,redefined,origin),/標記|設定/);
 const correct=await resign(applyCoachProfile(await update(established),['m1']));assert.equal((await reviewLocalUpdate(established,correct,origin)).state.matches[0].scoring_rules.best_of,5);
});
