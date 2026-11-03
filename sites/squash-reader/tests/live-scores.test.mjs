import test from 'node:test';
import assert from 'node:assert/strict';
import {validLiveScore,visibleLiveScore} from '../live-scores.mjs';
const record=()=>({match_id:'synthetic',started_at:'2030-01-01T00:00:00.000Z',version:1,updated_at:'2030-01-01T00:00:01.000Z',score:{rules:{best_of:3,target:11,margin:2},games:[],current:{side_a:10,side_b:10},winner:null}});
test('live summary validates deuce, games, match point and pending confirmation',()=>{
 const r=record();assert(validLiveScore(r));r.score.current.side_a=11;assert(validLiveScore(r));r.score.current.side_a=12;assert(!validLiveScore(r));
 r.score.games=[{side_a:12,side_b:10}];r.score.current={side_a:0,side_b:0};assert(validLiveScore(r));r.score.games.push({side_a:11,side_b:0});assert(!validLiveScore(r));r.score.winner='a';assert(validLiveScore(r));r.score.games.push({side_a:0,side_b:11});assert(!validLiveScore(r));
});
test('completed and restarted matches never show stale point scores',()=>{
 const r=record(),match={id:r.match_id,started_at:r.started_at,status:'in_progress'};assert.equal(visibleLiveScore(match,[r]),r);assert.equal(visibleLiveScore({...match,status:'completed'},[r]),null);assert.equal(visibleLiveScore({...match,started_at:'2030-01-01T00:01:00.000Z'},[r]),null);
});
