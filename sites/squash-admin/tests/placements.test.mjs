import test from 'node:test';
import assert from 'node:assert/strict';
import {fixture} from './fixture.mjs';
import {mergeDraft,withDraft,placementOptions,nearestOptions,occupies} from '../outputs/placements.mjs';
import {validate,review,parseLocal} from '../outputs/rules.mjs';

const start=parseLocal('2030-01-01T08:00');
test('slot guide agrees with full validation for every advertised position',()=>{
 const s=fixture();
 for(const option of placementOptions(s,'m1',s.dates[0])) {
  if(option.kind!=='available')continue;
  const result=review(s,[{id:'m1',start:option.start,court:option.court}]);
  assert.equal(result.can_apply,true,JSON.stringify(option));
 }
});
test('occupied courts remain occupied across division filters and buffered intervals',()=>{
 const s=fixture();s.matches[1].division='Other synthetic division';
 const options=placementOptions(s,'m1',s.dates[0]);
 assert.equal(options.find(o=>o.start===start+75&&o.court==='b').kind,'occupied');
 assert.equal(occupies(s.matches[1],start+80,'b'),true);
 assert.equal(occupies(s.matches[1],start+90,'b'),false);
});
test('rest, dependency, daily limits and same-day pools block misleading empty cells',()=>{
 const s=fixture();
 let options=placementOptions(s,'m1',s.dates[0]);
 assert.equal(options.find(o=>o.start===start+30&&o.court==='a').kind,'blocked');
 options=placementOptions(s,'m1',s.dates[1]);
 assert.equal(options.filter(o=>o.kind==='available').length,0);
 s.matches[1].dependencies=[{id:'m1',lag:0}];
 options=placementOptions(s,'m1',s.dates[0]);
 assert.equal(options.find(o=>o.start===start+150&&o.court==='a').kind,'blocked');
});
test('scoped validation includes dependencies pointing to a moved match',()=>{
 const s=fixture();s.matches[1].players=[];s.matches[1].dependencies=[{id:'m1',lag:0}];s.matches[0].assignment.start=start+90;
 const all=validate(s),scoped=validate(s,new Set(['m1']));
 assert.deepEqual(scoped.map(i=>i.key).sort(),all.filter(i=>i.matches.includes('m1')).map(i=>i.key).sort());
 assert.ok(scoped.some(i=>i.code==='DEPENDENCY'));
});
test('locked and completed matches never offer illegal placements',()=>{
 const s=fixture();s.matches[0].lock_time=true;
 assert.ok(placementOptions(s,'m1',s.dates[0]).filter(o=>o.kind==='available').every(o=>o.start===start));
 s.matches[0].lock_court=true;assert.deepEqual(placementOptions(s,'m1',s.dates[0]),[]);
 s.matches[0].lock_time=false;s.matches[0].lock_court=false;s.matches[0].status='completed';assert.deepEqual(placementOptions(s,'m1',s.dates[0]),[]);
});
test('draft merge preserves other edits and moving home removes the change',()=>{
 const s=fixture(),draft={m2:{id:'m2',start:start+120,court:'a'}};
 const copy=structuredClone(draft);
 let next=mergeDraft(s,draft,[{id:'m1',start:start+15,court:'b'}]);
 assert.deepEqual(draft,copy);assert.equal(Object.keys(next).length,2);
 assert.equal(withDraft(s,next).matches[0].assignment.start,start+15);assert.equal(s.matches[0].assignment.start,start);
 next=mergeDraft(s,next,[{id:'m1',start,court:'a'}]);assert.deepEqual(next,draft);
});
test('suggestions are stable, nearest first, and exclude blocked/current cells',()=>{
 const options=[{start:100,court:'b',kind:'available'},{start:95,court:'a',kind:'blocked'},{start:90,court:'b',kind:'available'},{start:100,court:'a',kind:'current'}];
 const copy=structuredClone(options),result=nearestOptions(options,92,2);
 assert.deepEqual(result.map(o=>o.start),[90,100]);assert.deepEqual(options,copy);
});
