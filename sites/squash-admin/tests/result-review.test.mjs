import assert from 'node:assert/strict';
import test from 'node:test';
import { fixture } from './fixture.mjs';
import { validateResult } from '../outputs/scoring.mjs';
import { reviewResultChange } from '../outputs/result-review-model.mjs';
const rules = { best_of: 3, target: 11, margin: 2 };
const played = { rules, winner: 'a', games: [{ side_a: 11, side_b: 6 }, { side_a: 11, side_b: 8 }] };
test('explicit exceptional results retain actual scores and never invent scores or winners', () => {
    assert.equal(validateResult({ rules, outcome: 'walkover', winner: 'b', games: [] }).winner, 'b');
    assert.equal(validateResult({ rules, outcome: 'double_forfeit', winner: null, games: [] }).winner, null);
    assert.equal(validateResult({ rules, outcome: 'retired', winner: 'a', games: [{ side_a: 3, side_b: 7 }] }).games[0].side_b, 7);
    assert.throws(() => validateResult({ ...played, outcome: 'walkover' }), /虛構/);
    assert.throws(() => validateResult({ rules, outcome: 'double_forfeit', winner: 'a', games: [] }), /勝方/);
    assert.throws(() => validateResult({ ...played, winner: null }), /勝方/);
    assert.throws(() => validateResult({ ...played, outcome: 'retired' }), /已分出勝負/);
    assert.throws(() => validateResult({ rules, outcome: 'retired', winner: 'a', games: [{ side_a: 12, side_b: 1 }] }), /局分/);
    assert.throws(() => validateResult({ rules, outcome: 'retired', winner: 'b', games: [{ side_a: 3, side_b: 7 }, { side_a: 1, side_b: 2 }] }), /最後一局/);
});
test('normal results remain backward compatible and corrections require a changed result', () => {
    assert.deepEqual(validateResult(played), played);
    const state = fixture();state.matches[0].allowed_outcomes=['played','double_forfeit'];const result = reviewResultChange(state, 'm1', played);
    assert.equal(state.matches[0].status, 'planned');
    assert.equal(result.state.matches[0].status, 'completed');
    assert.equal(result.correction, false);
    assert.throws(() => reviewResultChange(result.state, 'm1', { ...played, outcome: 'played' }), /沒有變更/);
    const changed = reviewResultChange(result.state, 'm1', { rules, outcome: 'double_forfeit', winner: null, games: [] });
    assert.equal(changed.correction, true);
    assert.equal(changed.after.winner, null);
});
function advancementState() {
    const state = fixture();state.matches[0].allowed_outcomes=["played","walkover","retired","double_forfeit"];
    state.entries = [{ id: 'e1', player_id: 'p1' }, { id: 'e2', player_id: 'p2' }];
    Object.assign(state.matches[0], { status: 'completed', result: played, slots: { a: { kind: 'entry', entry_id: 'e1' }, b: { kind: 'entry', entry_id: 'e2' } }, entry_ids: { a: 'e1', b: 'e2' } });
    Object.assign(state.matches[1], { dependencies: [{ id: 'm1', lag: 60 }], slots: { a: { kind: 'winner', match_id: 'm1' }, b: { kind: 'entry', entry_id: 'e2' } }, entry_ids: { a: 'e1', b: 'e2' } });
    return state;
}
test('correction invalidates unplayed derived entrants for a fresh advancement confirmation', () => {
    const state = advancementState();
    const result = reviewResultChange(state, 'm1', { rules, winner: 'b', games: [{ side_a: 5, side_b: 11 }, { side_a: 3, side_b: 11 }] });
    const downstream = result.state.matches[1];
    assert.deepEqual(result.invalidated, ['m2']);
    assert.equal(downstream.entry_ids.a, null);
    assert.deepEqual(downstream.players, ['p2']);
    assert.equal(downstream.slots.a.match_id, 'm1');
    assert.equal(state.matches[1].entry_ids.a, 'e1');
});
test('started completed locked and ambiguous downstreams block source corrections', () => {
    for (const patch of [{ status: 'in_progress' }, { status: 'completed', result: played }, { lock_time: true }, { lock_court: true }]) {
        const state = advancementState(); Object.assign(state.matches[1], patch);
        assert.throws(() => reviewResultChange(state, 'm1', { rules, outcome: 'walkover', winner: 'b', games: [] }), /不能自動更正/);
    }
    const old = fixture(); Object.assign(old.matches[0], { status: 'completed', result: played, allowed_outcomes: ['played','walkover'] }); old.matches[1].dependencies = [{ id: 'm1', lag: 60 }];
    assert.throws(() => reviewResultChange(old, 'm1', { rules, outcome: 'walkover', winner: 'b', games: [] }), /缺少明確晉級來源/);
});

test('legacy schedules cannot record special outcomes until explicit local policy is reviewed',()=>{assert.throws(()=>reviewResultChange(fixture(),'m1',{rules,outcome:'walkover',winner:'a',games:[]}),/規則/);});

// Qualification is frozen once an audited encouragement draw uses the source.
test('a drawn encouragement qualification cannot silently change through result correction', () => {
    const state=fixture();Object.assign(state.matches[0],{status:'completed',result:played});
    state.encouragement_draws=[{source_match_ids:['m1']}];
    assert.throws(()=>reviewResultChange(state,'m1',{rules,winner:'b',games:[{side_a:2,side_b:11},{side_a:3,side_b:11}]}),/鼓勵賽資格/);
    assert.deepEqual(state.matches[0].result,played);
});
