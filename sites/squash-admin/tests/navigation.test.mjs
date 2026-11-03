import test from 'node:test';
import assert from 'node:assert/strict';
import {fixture} from './fixture.mjs';
import {searchMatches} from '../outputs/navigation.mjs';
import {nextAvailablePosition,placementOptions} from '../outputs/placements.mjs';

function crossDay(){const state=fixture();state.matches.push({...structuredClone(state.matches[0]),id:'next-day',code:'NEXT-DAY',group:null,division:'Synthetic second division',assignment:{start:state.anchors[state.dates[1]]+300,court:'b'}});return state;}
test('name search crosses all event dates and courts',()=>{
 const state=crossDay(),before=structuredClone(state);
 assert.equal(searchMatches(state,{},'syntheticplayer001').length,3);
 const next=searchMatches(state,{},'NEXT-DAY');assert.equal(next.length,1);assert.equal(next[0].assignment.court,'b');assert.equal(next[0].assignment.start,state.anchors[state.dates[1]]+300);
 assert.deepEqual(state,before);
});
test('exact full-width match codes take priority over partial names and codes',()=>{
 const state=crossDay();assert.deepEqual(searchMatches(state,{},' １ ').map(match=>match.id),['m1']);
 state.matches[2].code='1';assert.equal(searchMatches(state,{},'1').length,2);
 assert.deepEqual(searchMatches(state,{},''),[]);assert.deepEqual(searchMatches(state,{},'not-present'),[]);
});
test('draft locations are searchable and used for navigation',()=>{
 const state=fixture(),start=state.anchors[state.dates[1]]+120;
 const draft={m1:{id:'m1',start,court:'b'}};
 const results=searchMatches(state,draft,'2030-01-02');assert.equal(results.length,1);assert.deepEqual(results[0].assignment,{start,court:'b'});
 assert.notEqual(state.matches[0].assignment.start,start);
});
test('multiple search terms match division and player together',()=>{
 const state=crossDay();assert.deepEqual(searchMatches(state,{},'SyntheticPlayer001 second').map(match=>match.id),['next-day']);
});
test('blocked rest slots expose all validation messages and a valid forward position',()=>{
 const state=fixture(),start=state.matches[0].assignment.start;
 const options=placementOptions(state,'m1',state.dates[0]);
 const blocked=options.find(option=>option.court==='a'&&option.start===start+30);
 assert.equal(blocked.kind,'blocked');assert.ok(blocked.messages.some(message=>message.includes('休息')));
 const next=nextAvailablePosition(options,start+30,'a');assert.equal(next.start,start+150);assert.equal(next.court,'a');assert.equal(next.kind,'available');
});
test('forward guidance excludes current, blocked and earlier positions',()=>{
 const options=[{start:10,court:'a',kind:'available'},{start:30,court:'a',kind:'current'},{start:40,court:'a',kind:'blocked'},{start:50,court:'b',kind:'available'}];
 const before=structuredClone(options);assert.equal(nextAvailablePosition(options,20,'a').court,'b');assert.deepEqual(options,before);
 assert.equal(nextAvailablePosition(options,60,'a'),null);
});
