import test from 'node:test';
import assert from 'node:assert/strict';
import {orderedMatches} from '../reader-state.mjs';

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
