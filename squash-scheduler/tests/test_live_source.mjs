import test from 'node:test';
import assert from 'node:assert/strict';
import {HttpJsonSource, createSource, LiveReader} from '../src/squash_scheduler/assets/live-source.mjs';

const head = {schema_version: '1.0.0', version: 'v1', workspace_id: 'public-scope', revision_id: 'r1',
  updated_at: '2026-10-29T02:00:00Z', snapshot_url: 'snapshots/v1.json'};
const payload = {schema_version: head.schema_version, version: head.version, workspace_id: head.workspace_id,
  revision_id: head.revision_id, updated_at: head.updated_at, name: '虛構賽事', timezone: 'Asia/Taipei',
  publication_status: 'ready', notice: '賽況以賽務確認為準', divisions: [], stages: [], courts: [], matches: []};
const response = value => new Response(JSON.stringify(value), {status: 200});
const flush = () => new Promise(resolve => setImmediate(resolve));

test('same source adapter reads local API and static object storage without operator credentials', async () => {
  for (const origin of ['http://127.0.0.1:8768', 'https://public.example']) {
    const calls = [];
    const source = createSource({type: 'http-json', version_url: `${origin}/event/version.json`}, origin,
      async function (url, options) {
        assert.equal(this, globalThis);
        calls.push({url, options});
        return response(url.endsWith('version.json') ? head : payload);
      });
    assert.equal((await source.load()).snapshot.name, payload.name);
    assert.equal(calls[1].url, `${origin}/event/snapshots/v1.json`);
    assert.ok(calls.every(c => c.options.credentials === 'omit' && !('X-Squash-Token' in (c.options.headers || {}))));
  }
});

test('unchanged ETag or unchanged version never downloads full snapshot', async () => {
  let calls = 0;
  const source = new HttpJsonSource({version_url: '/public/version.json'}, 'http://localhost', async (_url, options) => {
    calls++;
    assert.equal(options.headers['If-None-Match'], '"v1"');
    return new Response(null, {status: 304});
  });
  assert.equal((await source.load(head)).snapshot, null);
  source.fetcher = async () => { calls++; return response(head); };
  assert.equal((await source.load(head)).snapshot, null);
  assert.equal(calls, 2);
});

test('mismatched snapshots and incompatible schema are rejected', async () => {
  const source = new HttpJsonSource({version_url: '/version.json'}, 'https://public.example',
    async url => response(url.endsWith('/version.json') ? head : {...payload, revision_id: 'wrong'}));
  await assert.rejects(source.load(), /正在更新/);
  source.fetcher = async () => response({...head, schema_version: '2.0.0'});
  await assert.rejects(source.load(), /格式不相容/);
});

test('snapshot URL cannot redirect a reader into a different origin or credential-bearing URL', async () => {
  for (const url of ['https://private.example/internal', 'https://user:secret@public.example/snapshot.json', 'javascript:alert(1)']) {
    const source = new HttpJsonSource({version_url: '/version.json'}, 'https://public.example',
      async () => response({...head, snapshot_url: url}));
    await assert.rejects(source.load(), /來源網址無效/);
  }
  assert.throws(() => createSource({type: 'operator-state'}), /不支援/);
});

test('malformed match content is rejected before reaching the view', async () => {
  const source = new HttpJsonSource({version_url: '/version.json'}, 'https://public.example',
    async url => response(url.endsWith('/version.json') ? head : {...payload, matches: [{id: 'broken'}]}));
  await assert.rejects(source.load(), /對戰資料不完整/);
});

test('malformed optional standings are rejected while older providers remain supported', async () => {
  const source = new HttpJsonSource({version_url: '/version.json'}, 'https://public.example',
    async url => response(url.endsWith('/version.json') ? head : {...payload, standings: [{stage_id:'s',name:'A組',notice:'待核定',scoring_label:'勝2分',status:'manual_review',rows:[{entry_id:'e',name:'虛構選手',points:null}]}]}));
  await assert.rejects(source.load(), /積分資料不完整/);
  source.fetcher = async url => response(url.endsWith('/version.json') ? head : payload);
  assert.equal((await source.load()).snapshot.name,payload.name);
});

test('overlapping polls are serialized and a stopped request cannot apply a late response', async () => {
  let release, calls = 0, applied = 0;
  const source = {load: async () => { calls++; return new Promise(resolve => { release = resolve; }); }};
  const reader = new LiveReader({source, onSnapshot: () => applied++, onStatus: () => {}, interval: 60000});
  reader.start();
  await reader.refresh();
  assert.equal(calls, 1);
  reader.stop();
  release({descriptor: head, snapshot: payload});
  await flush();
  assert.equal(applied, 0);
});

test('disconnect preserves last snapshot, recovery updates it, older same-workspace response is ignored', async () => {
  const states = [], values = [];
  let outcome = {descriptor: head, snapshot: payload};
  const source = {load: async () => { if (outcome instanceof Error) throw outcome; return outcome; }};
  const reader = new LiveReader({source, onSnapshot: value => values.push(value), onStatus: s => states.push(s), interval: 60000});
  reader.active = true;
  try {
    await reader.refresh();
    outcome = new Error('offline');
    await reader.refresh();
    assert.equal(states.at(-1).state, 'stale');
    assert.equal(reader.head.version, 'v1');
    outcome = {descriptor: {...head, version: 'v0', updated_at: '2026-10-28T00:00:00Z'}, snapshot: {...payload, version: 'v0'}};
    await reader.refresh();
    assert.equal(reader.head.version, 'v1');
    const next = {...head, version: 'v2', revision_id: 'r2', updated_at: '2026-10-29T03:00:00Z'};
    outcome = {descriptor: next, snapshot: {...payload, ...next}};
    await reader.refresh();
    assert.equal(reader.head.version, 'v2');
    assert.equal(states.at(-1).state, 'live');
    assert.equal(values.length, 2);
    // Another workspace can legitimately start at an older source revision.
    outcome = {descriptor: {...head, workspace_id: 'restored-scope'}, snapshot: {...payload, workspace_id: 'restored-scope'}};
    await reader.refresh();
    assert.equal(reader.head.workspace_id, 'restored-scope');
  } finally { reader.stop(); }
});
