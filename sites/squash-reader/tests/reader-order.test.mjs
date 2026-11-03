import test from 'node:test';
import assert from 'node:assert/strict';
import {orderedMatches, courtMatches, queryState, stateSearch} from '../reader-state.mjs';

test('mixed timezone offsets are ordered by their actual instant',()=>{
  const matches=[{code:'M3',planned_start_at:'2026-10-24T09:00:00+08:00'},{code:'M1',planned_start_at:'2026-10-24T00:00:00Z'},{code:'M2',planned_start_at:'2026-10-24T08:00:00+08:00'}];
  assert.deepEqual(orderedMatches(matches).map(m=>m.code),['M1','M2','M3']);
  assert.equal(matches[0].code,'M3');
});
test('unscheduled matches remain last and use natural match-code order',()=>{
  const matches=[{code:'M10',planned_start_at:null},{code:'M2',planned_start_at:null},{code:'M1',planned_start_at:'2026-10-24T00:00:00Z'}];
  assert.deepEqual(orderedMatches(matches).map(m=>m.code),['M1','M2','M10']);
});


test('same-time matches follow court order before match codes',()=>{
  const matches=[{code:'M1',court_id:'d',planned_start_at:'2026-10-24T08:00:00+08:00'},{code:'M9',court_id:'a',planned_start_at:'2026-10-24T08:00:00+08:00'}];
  assert.deepEqual(orderedMatches(matches,new Map([['a',0],['d',3]])).map(m=>m.code),['M9','M1']);
});


test('court board uses confirmed live status and excludes finished or cancelled matches', () => {
  const match = (code, status, extra = {}) => ({code, status, court_id:'a', planned_start_at:'2020-01-01T08:00:00Z', ...extra});
  const rows = [match('M1','in_progress'), match('M2','planned'), match('M3','completed'), match('M4','cancelled'), match('M5','excluded'), match('M6','ready',{result:{}}), match('M7','in_progress',{court_id:'b'})];
  const board = courtMatches(rows, 'a');
  assert.deepEqual(board.current.map(m => m.code), ['M1']);
  assert.deepEqual(board.upcoming.map(m => m.code), ['M2']);
  assert.deepEqual(courtMatches(rows, 'empty'), {current:[], upcoming:[]});
});

test('called matches lead the court queue and unscheduled matches stay visible', () => {
  const rows = [
    {code:'M1', court_id:'a', status:'planned', planned_start_at:'2026-11-02T08:00:00+08:00'},
    {code:'M2', court_id:'a', status:'ready', called_at:'2026-11-02T08:30:00+08:00', planned_start_at:'2026-11-02T09:00:00+08:00'},
    {code:'M3', court_id:'a', status:'planned', delay_active:true, planned_start_at:null},
  ];
  assert.deepEqual(courtMatches(rows, 'a').upcoming.map(m => m.code), ['M2','M1','M3']);
  assert.equal(rows[0].code, 'M1');
});

test('court board keeps multiple active matches visible for operator conflict review', () => {
  const rows = ['M1','M2'].map(code => ({code, court_id:'a', status:'in_progress'}));
  assert.equal(courtMatches(rows,'a').current.length, 2);
});

test('court board URLs restore the view with filters', () => {
  const state = {view:'courts', court:'a', date:'2026-11-02', division:'d', player:'', search:''};
  assert.deepEqual(queryState(stateSearch(state)), state);
});
