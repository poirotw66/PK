import test from 'node:test';
import assert from 'node:assert/strict';
import {recalculateStandings} from '../standings.mjs';
const rules = {scoring:{win_points:2,loss_points:1},points_to_win_game:11,win_by_points:2,ranking_denominator_rule:{walkover:true,retired:true,double_forfeit:true},walkover_rule:{include_in_rankings:true,counts_as_played:false,winner_points:2,loser_points:0},retirement_rule:{include_in_rankings:true,counts_as_played:true,winner_points:2,loser_points:0},double_forfeit_rule:{include_in_rankings:true,counts_as_played:false,side_a_points:0,side_b_points:0}};
const configuration = {workspace_id:'synthetic',stages:{rr:rules}};
function fixture(ids=['a','b','c']) {
 const rows=ids.map(entry_id=>({entry_id,name:'SyntheticPlayer'+entry_id,status:'active',rank:null,qualifies:false,points:99}));
 const matches=ids.flatMap((a,i)=>ids.slice(i+1).map(b=>({id:a+b,stage_id:'rr',status:'planned',side_a:{entry_id:a},side_b:{entry_id:b},result:null})));
 return {workspace_id:'synthetic',standings:[{stage_id:'rr',notice:'雲端成績已更新；',rows,tie_groups:[]}],matches};
}
function finish(snapshot,id,winner,outcome='played',games=null) {
 const match=snapshot.matches.find(row=>row.id===id);match.status='completed';
 match.result={outcome,winner_entry_id:winner,games:games??(outcome==='played'?[{side_a:winner===match.side_a.entry_id?11:0,side_b:winner===match.side_b.entry_id?11:0},{side_a:winner===match.side_a.entry_id?11:0,side_b:winner===match.side_b.entry_id?11:0}]:[])};
}
const calculate = snapshot => recalculateStandings(snapshot,configuration).standings[0];
test('a partial pool immediately shows points, games and provisional numeric ranks',()=>{
 const s=fixture();finish(s,'ab','b');const before=structuredClone(s),g=calculate(s);
 assert.equal(g.status,'provisional');assert.equal(g.confirmed_matches,1);
 assert.deepEqual(g.rows.map(r=>[r.entry_id,r.rank,r.points,r.wins,r.losses,r.games_for,r.games_against]),[['b',1,2,1,0,2,0],['a',2,1,0,1,0,2],['c',3,0,0,0,0,0]]);
 assert.deepEqual(s,before);assert.deepEqual(recalculateStandings(recalculateStandings(s,configuration),configuration).standings[0],g);
});
test('a completed two-player pool finishes automatically without advancing the next round',()=>{
 const s=fixture(['a','b']);finish(s,'ab','a');const g=calculate(s);
 assert.equal(g.status,'final');assert.deepEqual(g.rows.map(r=>r.rank),[1,2]);assert(g.rows.every(r=>!r.qualifies));
});
test('two-player point ties use head-to-head before game or point difference',()=>{
 const s=fixture(['a','b','c','d']);for(const [id,w]of [['ab','a'],['ac','c'],['ad','a'],['bc','b'],['bd','b'],['cd','d']])finish(s,id,w);
 const g=calculate(s);assert.equal(g.status,'final');assert.deepEqual(g.rows.map(r=>r.entry_id),['a','b','d','c']);assert.deepEqual(g.tie_groups,[]);
});
test('three-way ties remain shared numeric ranks and only require a decision when complete',()=>{
 const s=fixture();let g=calculate(s);assert.equal(g.status,'provisional');assert.deepEqual(g.rows.map(r=>r.rank),[1,1,1]);
 finish(s,'ab','a');finish(s,'bc','b');finish(s,'ac','c');g=calculate(s);assert.equal(g.status,'manual_review');assert.deepEqual(g.rows.map(r=>r.points),[3,3,3]);assert.deepEqual(g.rows.map(r=>r.rank),[1,1,1]);assert.equal(g.tie_groups[0].length,3);
});
test('two-player ties without a direct result use game difference then point difference',()=>{
 const s=fixture(['a','b','c','d']);finish(s,'ac','a');finish(s,'bd','b','played',[{side_a:11,side_b:9},{side_a:11,side_b:9}]);
 const g=calculate(s);assert.equal(g.rows[0].entry_id,'a');assert.equal(g.rows[1].entry_id,'b');assert.equal(g.rows[0].points,g.rows[1].points);
 s.matches.find(m=>m.id==='bd').result.games=[{side_a:11,side_b:0},{side_a:0,side_b:11},{side_a:11,side_b:0}];
 assert.equal(calculate(s).rows[0].entry_id,'a');
});
test('walkovers, retirements and double forfeits honor stage rules and appearance counts',()=>{
 for(const [outcome,expected]of [['walkover',0],['retired',1],['double_forfeit',0]]){
  const s=fixture(['a','b']);finish(s,'ab',outcome==='double_forfeit'?null:'a',outcome);const g=calculate(s),a=g.rows.find(r=>r.entry_id==='a'),b=g.rows.find(r=>r.entry_id==='b');
  assert.equal(a.points,outcome==='double_forfeit'?0:2);assert.equal(b.points,0);assert.equal(b.forfeits,1);assert.equal(a.played_count,expected);assert.equal(a.adjudicated_count,1);
 }
});
test('unsupported special rules and missing or excluded matches retain review without inventing a final rank',()=>{
 const s=fixture();finish(s,'ab','a','walkover');const config=structuredClone(configuration);delete config.stages.rr.walkover_rule;
 assert.equal(recalculateStandings(s,config).standings[0].status,'manual_review');
 s.matches.pop();assert.equal(calculate(s).status,'manual_review');
 const excluded=fixture();excluded.matches[0].status='excluded';assert.equal(calculate(excluded).status,'manual_review');
});
test('unrelated workspaces and previously published manual decisions are preserved',()=>{
 const s=fixture();s.workspace_id='other';assert.equal(recalculateStandings(s,configuration),s);
 s.workspace_id='synthetic';s.standings[0].notice='Approved manual order';assert.equal(calculate(s),s.standings[0]);
});

test('validated played games count when synthetic stage target defaults are unset',()=>{
 const s=fixture();finish(s,'ab','b');const config=structuredClone(configuration);config.stages.rr.points_to_win_game=null;
 const winner=recalculateStandings(s,config).standings[0].rows.find(row=>row.entry_id==='b');assert.equal(winner.games_for,2);assert.equal(winner.games_against,0);
});
