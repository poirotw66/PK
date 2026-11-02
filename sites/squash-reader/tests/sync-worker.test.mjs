import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const moduleText=await readFile(new URL('../dist/server/index.js',import.meta.url),'utf8');
const {default:worker}=await import('data:text/javascript;base64,'+Buffer.from(moduleText).toString('base64'));
const descriptor=JSON.parse(await readFile(new URL('../dist/public/version.json',import.meta.url)));
const snapshot=JSON.parse(await readFile(new URL('../dist/public/'+descriptor.snapshot_url,import.meta.url)));
class Bucket {
  objects=new Map(); counter=0;
  async get(key) {
    const item=this.objects.get(key);if(!item)return null;
    return {...item,json:async()=>JSON.parse(item.raw),body:new Response(item.raw).body};
  }
  async head(key) {return this.get(key);}
  async put(key,raw,options={}) {
    const previous=this.objects.get(key);
    if(options.onlyIf?.etagDoesNotMatch==='*' && previous)return null;
    if(options.onlyIf?.etagMatches && previous?.etag!==options.onlyIf.etagMatches)return null;
    const item={etag:String(++this.counter),raw,customMetadata:options.customMetadata};
    this.objects.set(key,item);return item;
  }
}
const env=()=>({BUCKET:new Bucket(),SYNC_SECRET:'test-only-secret',SYNC_WORKSPACE_ID:snapshot.workspace_id});
async function post(path,value,e,timestamp=String(Math.floor(Date.now()/1000))) {
  const raw=JSON.stringify(value);
  const key=await crypto.subtle.importKey('raw',new TextEncoder().encode(e.SYNC_SECRET),{name:'HMAC',hash:'SHA-256'},false,['sign']);
  const signature=Buffer.from(await crypto.subtle.sign('HMAC',key,new TextEncoder().encode(timestamp+'\n'+raw))).toString('hex');
  return worker.fetch(new Request('https://example.test'+path,{method:'POST',body:raw,headers:{'X-Sync-Time':timestamp,'X-Sync-Signature':signature}}),e);
}
const get=(path,e,headers={})=>worker.fetch(new Request('https://example.test'+path,{headers}),e);

test('initial reads expose bundled public data with awaiting status',async()=>{
  const e=env();assert.equal((await (await get('/public/status.json',e)).json()).source_state,'awaiting');
  const head=await (await get('/public/version.json',e)).json();
  assert.equal((await (await get(head.snapshot_url,e)).json()).matches.length,snapshot.matches.length);
  assert.equal((await get('/api/state',e)).status,404);
});
test('signed update, immutable snapshot, ETag and heartbeat',async()=>{
  const e=env();assert.equal((await post('/sync/update',snapshot,e)).status,200);
  let status=await (await get('/public/status.json',e)).json();assert.equal(status.source_state,'online');assert(status.synced_at);
  const head=await (await get('/public/version.json',e)).json();
  assert.deepEqual(await (await get(head.snapshot_url,e)).json(),snapshot);
  assert.equal((await get('/public/version.json',e,{'If-None-Match':'"'+snapshot.version+'"'})).status,304);
  assert.equal((await post('/sync/heartbeat',{workspace_id:snapshot.workspace_id,version:snapshot.version},e)).status,200);
  assert.equal((await post('/sync/update',{...snapshot,name:'Altered under same version'},e)).status,409);
});
test('reject unauthenticated, expired, wrong-workspace and private fields',async()=>{
  const e=env();assert.equal((await worker.fetch(new Request('https://example.test/sync/update',{method:'POST',body:'{}'}),e)).status,401);
  assert.equal((await post('/sync/update',snapshot,e,'1000000000')).status,401);
  assert.equal((await post('/sync/update',{...snapshot,workspace_id:'other'},e)).status,409);
  assert.equal((await post('/sync/update',{...snapshot,actor:'private'},e)).status,400);
  const nested=structuredClone(snapshot);nested.matches[0].side_a.internal_path='private';
  assert.equal((await post('/sync/update',nested,e)).status,400);
});
test('reject older revisions and heartbeat needing a snapshot',async()=>{
  const e=env();assert.equal((await post('/sync/heartbeat',{workspace_id:snapshot.workspace_id,version:snapshot.version},e)).status,409);
  await post('/sync/update',snapshot,e);
  assert.equal((await post('/sync/update',{...snapshot,updated_at:'2020-01-01T00:00:00Z'},e)).status,409);
});
test('offline source keeps the last published snapshot available',async()=>{
  const e=env();await post('/sync/update',snapshot,e);
  const item=e.BUCKET.objects.get('sync/current.json');const state=JSON.parse(item.raw);state.last_heartbeat_at='2020-01-01T00:00:00Z';item.raw=JSON.stringify(state);
  assert.equal((await (await get('/public/status.json',e)).json()).source_state,'offline');
  const head=await (await get('/public/version.json',e)).json();assert.equal((await get(head.snapshot_url,e)).status,200);
});
test('storage failures do not report a successful synchronization',async()=>{
  const e=env();e.BUCKET.put=async()=>{throw new Error('Unavailable');};
  assert.equal((await post('/sync/update',snapshot,e)).status,503);
});
