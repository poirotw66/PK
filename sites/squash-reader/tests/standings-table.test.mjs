import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

// Exercise the built renderer so build-time overrides cannot silently hide rows.
const source = await readFile(new URL('../dist/live.mjs', import.meta.url), 'utf8');
const renderer = source.slice(source.indexOf('function standingsGroup('), source.indexOf('function standingsView('));
function node(tag, text = null, className = '') {
  return {tag, text, className, dataset: {}, children: [], append(...children) { this.children.push(...children); }, setAttribute() {}};
}
const {standingsGroup} = vm.runInNewContext(renderer + ';({standingsGroup})', {node});
function find(root, tag) {
  return [...(root.tag === tag ? [root] : []), ...root.children.flatMap(child => find(child, tag))];
}
function group(notice = 'Provisional standings') {
  return {name: 'A', pool_id: 'pool-a', status: 'provisional', notice, scoring_label: 'Win: 2', confirmed_matches: 1, total_matches: 6, advancement_count: 2, tie_groups: [], rows: [{entry_id: 'entry-a', name: 'SyntheticPlayer001', status: 'active', qualifies: true, rank: 1, points: 99, wins: 88, losses: 77, forfeits: 66, games_for: 55, games_against: 44}]};
}
test('cloud results awaiting review keep the table and names without stale statistics or qualification badges', () => {
  const fixture = group('雲端成績已更新；積分與晉級名次待賽務核定。');
  fixture.status = 'manual_review';
  const result = standingsGroup(fixture);
  assert.equal(find(result, 'table').length, 1);
  assert.equal(find(result, 'th').length, 8);
  const cells = find(result, 'td');
  assert.equal(find(cells[0], 'span')[0].text, 'SyntheticPlayer001');
  assert.deepEqual(Array.from(cells.slice(1), cell => cell.text), ['待核定', '—', '—', '—', '—', '—', '—']);
  assert.equal(find(result, 'span').filter(span => span.className === 'qualifier-label').length, 0);
});
test('reviewed and ordinary provisional standings retain published statistics', () => {
  const result = standingsGroup(group());
  assert.deepEqual(Array.from(find(result, 'td').slice(1), cell => cell.text), ['1', '99', '88', '77', '66', '55', '44']);
  assert.equal(find(result, 'span').filter(span => span.className === 'qualifier-label').length, 1);
});
