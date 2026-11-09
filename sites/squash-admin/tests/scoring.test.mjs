import test from 'node:test';
import assert from 'node:assert/strict';
import {defaultScoringRules,tally,scorePoint,finishedResult,validateResult} from '../outputs/scoring.mjs';
const empty=()=>({rules:defaultScoringRules,points:[]});
const game=(a,b)=>[...Array(Math.min(a,b)).fill(['a','b']).flat(),...Array(a-b>0?a-b:b-a).fill(a>b?'a':'b')];
test('deuce, automatic game progression, undo and persisted replay',()=>{
 let s={...empty(),points:game(10,10)};assert.equal(tally(s).games.length,0);
 s=scorePoint(s,'a');assert.equal(tally(s).games.length,0);
 s=scorePoint(s,'a');assert.deepEqual(tally(s).games,[{side_a:12,side_b:10}]);assert.deepEqual(tally(s).current,{side_a:0,side_b:0});
 const undo=scorePoint(s,'undo');assert.deepEqual(tally(undo).current,{side_a:11,side_b:10});assert.equal(tally(undo).games.length,0);
 s.points.push(...game(3,11),...game(11,5));const result=finishedResult(JSON.parse(JSON.stringify(s)));assert.equal(result.winner,'a');assert.equal(result.games.length,3);assert.throws(()=>scorePoint(s,'b'));
 assert.equal(tally(scorePoint(s,'undo')).winner,null);
});
test('incomplete, excessive, tied, post-victory and mismatched results are rejected',()=>{
 assert.throws(()=>finishedResult(empty()));assert.throws(()=>scorePoint(empty(),'undo'));
 for(const games of [[{side_a:11,side_b:10},{side_a:11,side_b:0}],[{side_a:12,side_b:0},{side_a:11,side_b:0}],[{side_a:11,side_b:0},{side_a:11,side_b:0},{side_a:0,side_b:11}],[{side_a:11,side_b:11},{side_a:11,side_b:0}]])assert.throws(()=>validateResult({rules:defaultScoringRules,winner:'a',games}));
 assert.throws(()=>validateResult({rules:defaultScoringRules,winner:'b',games:[{side_a:11,side_b:0},{side_a:11,side_b:0}]}));
});
