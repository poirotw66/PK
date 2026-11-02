import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import vm from 'node:vm';

const context = vm.createContext({crypto:webcrypto});
vm.runInContext(readFileSync(new URL('../src/squash_scheduler/assets/draft-storage.js',import.meta.url),'utf8'),context);
const Store = context.ScheduleDraftStore;
const content = {baseRevisionId:'revision-a',updates:[{match_id:'match-a',local_start:'2026-10-30T09:00',court_id:'court-a'}],reason:'人工調整賽程'};
function environment() {
  const values = new Map(), queues = new Map();
  const storage = {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
  const locks = {request(key,job){const next=(queues.get(key)||Promise.resolve()).catch(()=>{}).then(job);queues.set(key,next.catch(()=>{}));return next;}};
  const make = (writerId,workspaceId='workspace-a')=>new Store({workspaceId,writerId,storage,locks});
  return {values,storage,locks,make};
}

test('minimal versioned record round-trips without actor or credentials',async()=>{
  const e=environment(),a=e.make('a');assert.equal(a.read(),null);
  await a.save(content);const record=JSON.parse(e.values.get(a.key));
  assert.deepEqual(Object.keys(record).sort(),['base_revision_id','generation','reason','saved_at','updates','version','workspace_id','writer_id']);
  assert.equal(record.reason,content.reason);assert.deepEqual(record.updates,content.updates);
  const b=e.make('b');assert.equal(b.read().base_revision_id,'revision-a');
  await b.save({...content,reason:'明確接手'});assert.equal(b.record.reason,'明確接手');
});

test('simultaneous tab writes serialize and refuse silent replacement',async()=>{
  const e=environment(),a=e.make('a'),b=e.make('b');a.read();b.read();
  const results=await Promise.allSettled([a.save(content),b.save({...content,reason:'other tab'})]);
  assert.equal(results.filter(r=>r.status==='fulfilled').length,1);
  assert.equal(results.find(r=>r.status==='rejected').reason.code,'DRAFT_STORAGE_CONFLICT');
  assert.equal(JSON.parse(e.storage.getItem(a.key)).writer_id,'a');
  b.read();await b.save({...content,reason:'explicit takeover'});
  await assert.rejects(a.save(content),{code:'DRAFT_STORAGE_CONFLICT'});
  await assert.rejects(a.clear(),{code:'DRAFT_STORAGE_CONFLICT'});
  assert.equal(JSON.parse(e.storage.getItem(a.key)).writer_id,'b');
});

test('clear invalidates queued saves and isolates workspace keys',async()=>{
  const e=environment(),a=e.make('a'),b=e.make('b','workspace-b');a.read();b.read();
  const save=a.save(content),clear=a.clear();await Promise.all([save,clear]);
  assert.equal(e.storage.getItem(a.key),null);
  await a.save(content);await b.save({...content,reason:'different workspace'});await a.clear();
  assert.equal(e.storage.getItem(a.key),null);assert.equal(b.read().reason,'different workspace');
});

test('malformed, foreign, duplicate, oversized and impossible-date records are rejected',async()=>{
  const e=environment(),a=e.make('a');a.read();await a.save(content);
  const raw=e.storage.getItem(a.key),record=JSON.parse(raw);
  for(const value of ['{',JSON.stringify({...record,workspace_id:'foreign'}),JSON.stringify({...record,actor:'private'}),JSON.stringify({...record,updates:[...record.updates,...record.updates]}),JSON.stringify({...record,updates:[{...record.updates[0],local_start:'2026-02-30T09:00'}]}),JSON.stringify({...record,reason:'a'.repeat(2001)})]){
    assert.throws(()=>Store.decode(value,'workspace-a'),{code:'DRAFT_STORAGE_INVALID'});
  }
});

test('storage restrictions, quota errors and missing locks fail explicitly',async()=>{
  const e=environment(),a=e.make('a');a.read();
  e.storage.setItem=()=>{throw Error('quota');};await assert.rejects(a.save(content),{code:'DRAFT_STORAGE_UNAVAILABLE'});
  const b=new Store({workspaceId:'workspace-b',writerId:'b',storage:e.storage,locks:null});b.read();
  await assert.rejects(b.save(content),{code:'DRAFT_STORAGE_UNAVAILABLE'});
  e.storage.getItem=()=>{throw Error('blocked');};assert.throws(()=>a.read(),{code:'DRAFT_STORAGE_UNAVAILABLE'});
});
