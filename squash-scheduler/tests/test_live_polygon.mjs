import test from 'node:test';
import assert from 'node:assert/strict';
import {polygonModel, scoreMarks} from '../src/squash_scheduler/assets/live-polygon.mjs';

const match = (a, b, result = null) => ({id:`${a}-${b}`,side_a:{entry_id:a},side_b:{entry_id:b},status:result ? 'completed':'planned',result});
const result = (outcome='played', winner_entry_id='b', games=[{side_a:8,side_b:11},{side_a:11,side_b:6},{side_a:9,side_b:11}]) => ({outcome,winner_entry_id,games});

test('scores follow actual side identities, including reversed winner and a zero', () => {
  assert.deepEqual(scoreMarks(match('a','b',result())), ['1','2']);
  assert.deepEqual(scoreMarks(match('a','b',result('played','a',[{side_a:11,side_b:4}]))), ['1','0']);
  assert.equal(scoreMarks(match('a','b')), null);
  assert.equal(scoreMarks({...match('a','b',result()),status:'in_progress'}), null);
});
test('adjudications never manufacture a played score', () => {
  assert.deepEqual(scoreMarks(match('a','b',result('walkover','a',[]))), ['勝','棄']);
  assert.deepEqual(scoreMarks(match('a','b',result('walkover','b',[]))), ['棄','勝']);
  assert.deepEqual(scoreMarks(match('a','b',result('double_forfeit',null,[]))), ['棄','棄']);
  assert.deepEqual(scoreMarks(match('a','b',result('retired','b'))), ['退','勝']);
  assert.deepEqual(scoreMarks(match('a','b',result('retired','b',[{side_a:5,side_b:3}]))), ['退','勝']);
  assert.deepEqual(scoreMarks(match('a','b',result('retired','a',[]))), ['勝','退']);
  assert.equal(scoreMarks(match('a','b',result('cancelled',null,[]))), null);
  assert.equal(scoreMarks(match('a','b',result('played','a',[]))), null);
});
test('complete pairings for 3, 4 and 5 players, and only the actual known matches', () => {
  for (const n of [3,4,5]) {
    const rows = Array.from({length:n},(_,i)=>({entry_id:String(i),name:`Player ${i}`}));
    const matches = rows.flatMap((a,i)=>rows.slice(i+1).map(b=>match(a.entry_id,b.entry_id)));
    const model = polygonModel({rows},matches);
    assert.equal(model.vertices.length,n);
    assert.equal(model.edges.length,n*(n-1)/2);
    const partial = polygonModel({rows},matches.slice(0,1));
    assert.equal(partial.edges.length,1);
    assert.equal(polygonModel({rows},[...matches,match('foreign','0')]).edges.length,matches.length);
    for (const v of model.vertices) assert.ok(v.labelX>=84 && v.labelX<=616 && v.labelY>=21 && v.labelY<=529);
  }
});
test('ranking changes do not move vertices or swap endpoint identities', () => {
  const rows = ['a','b','c'].map(entry_id=>({entry_id,name:entry_id}));
  const matches=[match('b','a'),match('c','a'),match('b','c')];
  const before=polygonModel({rows},matches), after=polygonModel({rows:[...rows].reverse()},matches);
  assert.deepEqual(before.vertices,after.vertices);
  assert.equal(before.edges[0].a.entry_id,'b');
  assert.equal(before.edges[0].b.entry_id,'a');
});
