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
test('a new workspace ignores the previous schedule and score pointers',async()=>{
  const e=cloudEnv();
  const old={...descriptor,workspace_id:'old-workspace',version:'previous-version'};
  await e.BUCKET.put('sync/current.json',JSON.stringify({authority:'cloud',descriptor:old}));
  await e.BUCKET.put('live/scores.json',JSON.stringify({matches:[{match_id:'old-match'}]}));
  assert.equal((await published(e)).workspace_id,snapshot.workspace_id);
  const scores=await (await get('/public/live-scores.json',e)).json();
  assert.equal(scores.schedule_version,snapshot.version);
  assert.deepEqual(scores.matches,[]);
  const previous=publication();
  previous.matches.push({...previous.matches[0],id:'old-match'});
  assert.equal((await cloud(previous,e)).status,409);
  assert.equal((await published(e)).version,snapshot.version);
  assert.equal((await cloud(publication(),e)).status,200);
  assert.deepEqual((await (await e.BUCKET.get('sync/current.json')).json()).descriptor,old);
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
  const item=e.BUCKET.objects.get(`workspaces/${snapshot.workspace_id}/sync/current.json`);const state=JSON.parse(item.raw);state.last_heartbeat_at='2020-01-01T00:00:00Z';item.raw=JSON.stringify(state);
  assert.equal((await (await get('/public/status.json',e)).json()).source_state,'offline');
  const head=await (await get('/public/version.json',e)).json();assert.equal((await get(head.snapshot_url,e)).status,200);
});
test('storage failures do not report a successful synchronization',async()=>{
  const e=env();e.BUCKET.put=async()=>{throw new Error('Unavailable');};
  assert.equal((await post('/sync/update',snapshot,e)).status,503);
});

const cloudEnv=()=>({...env(),CLOUD_PUBLISH_SECRET:'cloud-test-only',PUBLISH_SOURCE_ID:'test-source'});
function publication(sequence=1){const now=new Date().toISOString();return {source_id:'test-source',sequence,schedule_revision:'cloud-revision',published_at:now,live_updated_at:null,created_at:now,matches:snapshot.matches.map(match=>({id:match.id,code:match.code,division:snapshot.divisions.find(d=>d.id===match.division_id).name,side_a:match.side_a.resolution==='pending'?'Pending qualification':match.side_a.name,side_b:match.side_b.resolution==='pending'?'Pending qualification':match.side_b.name,status:match.status,court_id:match.court_id,planned_start_at:match.planned_start_at?new Date(match.planned_start_at).toISOString():null,started_at:match.started_at}))};}
async function cloud(value,e,timestamp=String(Math.floor(Date.now()/1000))){const raw=JSON.stringify(value),key=await crypto.subtle.importKey('raw',new TextEncoder().encode(e.CLOUD_PUBLISH_SECRET),{name:'HMAC',hash:'SHA-256'},false,['sign']);const signature=Buffer.from(await crypto.subtle.sign('HMAC',key,new TextEncoder().encode(timestamp+'\n/sync/cloud-publish\n'+raw))).toString('hex');return worker.fetch(new Request('https://example.test/sync/cloud-publish',{method:'POST',body:raw,headers:{'X-Sync-Time':timestamp,'X-Sync-Signature':signature}}),e);}
async function published(e){const head=await(await get('/public/version.json',e)).json();return (await get(head.snapshot_url,e)).json();}
test('cloud publication preserves public identities, metadata and prevents legacy overwrite',async()=>{
 const e=cloudEnv(),p=publication();const response=await cloud(p,e);assert.equal(response.status,200,JSON.stringify(await response.clone().json()));
 const result=await published(e);assert.equal(result.matches.length,snapshot.matches.length);assert.deepEqual(result.divisions,snapshot.divisions);assert.deepEqual(result.standings,snapshot.standings);assert.deepEqual(result.matches[0].side_a,snapshot.matches[0].side_a);
 assert.equal((await(await get('/public/status.json',e)).json()).source_state,'cloud');
 assert.equal((await post('/sync/update',snapshot,e)).status,409);assert.equal((await post('/sync/heartbeat',{workspace_id:snapshot.workspace_id,version:snapshot.version},e)).status,409);
});
test('cloud sequence is idempotent and older requests cannot overwrite newer state',async()=>{
 const e=cloudEnv(),one=publication(),two=publication(2);await cloud(one,e);const initial=await published(e);
 assert.equal((await cloud(one,e)).status,200);assert.equal((await published(e)).version,initial.version);
 assert.equal((await cloud({...one,schedule_revision:'changed'},e)).status,409);await cloud(two,e);
 const stale=await cloud(one,e);assert.equal(stale.status,409);assert.equal((await stale.json()).sequence,2);
});
test('cloud updates planned assignment and explicit live state, then clears the court on completion',async()=>{
 const e=cloudEnv(),p=publication();const m=p.matches.find(m=>{const old=snapshot.matches.find(x=>x.id===m.id);return old.side_a.resolution==='confirmed'&&old.side_b.resolution==='confirmed'&&old.side_a.entry_id&&old.side_b.entry_id;});m.status='in_progress';m.started_at=new Date().toISOString();p.live_updated_at=m.started_at;
 assert.equal((await cloud(p,e)).status,200);assert.equal((await published(e)).matches.find(x=>x.id===m.id).status,'in_progress');
 const next=structuredClone(p);next.sequence++;next.matches.find(x=>x.id===m.id).status='completed';assert.equal((await cloud(next,e)).status,200);const finished=(await published(e)).matches.find(x=>x.id===m.id);assert.equal(finished.status,'completed');assert.equal(finished.result,null);
});
test('cloud rejects secrets, participant drift, unresolved starts, bad authorization and storage races',async()=>{
 const e=cloudEnv();assert.equal((await cloud({...publication(),actor:'private'},e)).status,400);
 const p=publication();p.matches[0].private_path='secret';assert.equal((await cloud(p,e)).status,409);
 const drift=publication();const m=drift.matches.find(m=>snapshot.matches.find(x=>x.id===m.id).side_a.resolution==='confirmed');m.side_a='Unexpected player';assert.equal((await cloud(drift,e)).status,409);
 const unresolved=publication();const unknown=unresolved.matches.find(m=>snapshot.matches.find(x=>x.id===m.id).side_a.resolution==='pending');unknown.status='in_progress';unknown.started_at=new Date().toISOString();assert.equal((await cloud(unresolved,e)).status,409);
 assert.equal((await cloud(publication(),e,'1000000000')).status,401);
 e.BUCKET.put=async()=>null;assert.equal((await cloud(publication(),e)).status,409);
});

test('confirmed scores publish the winner and games and cannot be overwritten',async()=>{
 const e=cloudEnv(),p=publication(),m=p.matches.find(row=>{const old=snapshot.matches.find(x=>x.id===row.id);return old.side_a.resolution==='confirmed'&&old.side_b.resolution==='confirmed'&&old.side_a.entry_id&&old.side_b.entry_id;});
 m.status='completed';m.started_at=new Date().toISOString();m.result={rules:{best_of:3,target:11,margin:2},winner:'b',games:[{side_a:10,side_b:12},{side_a:7,side_b:11}]};
 const response=await cloud(p,e);assert.equal(response.status,200,JSON.stringify(await response.clone().json()));
 const result=(await published(e)).matches.find(row=>row.id===m.id);assert.equal(result.result.winner_entry_id,result.side_b.entry_id);assert.deepEqual(result.result.games,m.result.games);
 const retry=structuredClone(p);retry.sequence++;assert.equal((await cloud(retry,e)).status,200);
 const replaced=structuredClone(p);replaced.sequence+=2;replaced.matches.find(row=>row.id===m.id).result={...m.result,winner:'a',games:[{side_a:11,side_b:0},{side_a:11,side_b:0}]};assert.equal((await cloud(replaced,e)).status,409);
 const legacy=publication(4);legacy.matches.find(row=>row.id===m.id).status='completed';assert.equal((await cloud(legacy,e)).status,200);assert.deepEqual((await published(e)).matches.find(row=>row.id===m.id).result,result.result);
 const groups=(await published(e)).standings.filter(group=>group.stage_id===result.stage_id&&group.rows.some(row=>row.entry_id===result.side_a.entry_id)&&group.rows.some(row=>row.entry_id===result.side_b.entry_id));
 assert(groups.length > 0);
 for(const group of groups){
  assert.equal(group.status,'provisional');assert(group.rows.every(row=>!row.qualifies&&Number.isInteger(row.rank)));assert(group.notice.startsWith('依已登錄賽果自動計算；'));
  const winner=group.rows.find(row=>row.entry_id===result.side_b.entry_id),loser=group.rows.find(row=>row.entry_id===result.side_a.entry_id);
  assert.equal(winner.points,2);assert.equal(winner.wins,1);assert.equal(winner.games_for,2);assert.equal(winner.rank,1);
  assert.equal(loser.points,1);assert.equal(loser.losses,1);assert.equal(loser.games_against,2);
 }
});
test('finishing a complete pool publishes final numeric standings and repeat publications preserve totals',async()=>{
 const e=cloudEnv(),p=publication(),group=snapshot.standings.find(group=>group.rows.length>=3);
 const order=new Map(group.rows.map((row,index)=>[row.entry_id,index]));
 let count=0;
 for(const match of snapshot.matches.filter(match=>match.stage_id===group.stage_id&&order.has(match.side_a.entry_id)&&order.has(match.side_b.entry_id))){
  const patch=p.matches.find(row=>row.id===match.id),winner=order.get(match.side_a.entry_id)<order.get(match.side_b.entry_id)?'a':'b';
  patch.status='completed';patch.result={rules:{best_of:3,target:11,margin:2},winner,games:[{side_a:winner==='a'?11:0,side_b:winner==='b'?11:0},{side_a:winner==='a'?11:0,side_b:winner==='b'?11:0}]};count++;
 }
 assert.equal((await cloud(p,e)).status,200);
 const result=(await published(e)).standings.find(row=>row.pool_id===group.pool_id&&row.stage_id===group.stage_id);
 assert.equal(result.status,'final');assert.equal(result.confirmed_matches,count);assert.deepEqual(result.rows.map(row=>row.rank),group.rows.map((_,index)=>index+1));
 const again=structuredClone(p);again.sequence++;assert.equal((await cloud(again,e)).status,200);
 assert.deepEqual((await published(e)).standings.find(row=>row.pool_id===group.pool_id&&row.stage_id===group.stage_id),result);
});
test('scoring rejects unfinished, impossible, mismatched and private result fields',async()=>{
 for(const change of [m=>m.result.games[0].side_b=11,m=>m.result.winner='a',m=>m.status='in_progress',m=>m.result.actor='private',m=>m.result.games.push({side_a:0,side_b:11})]){
  const e=cloudEnv(),p=publication(),m=p.matches.find(row=>{const old=snapshot.matches.find(x=>x.id===row.id);return old.side_a.resolution==='confirmed'&&old.side_b.resolution==='confirmed'&&old.side_a.entry_id&&old.side_b.entry_id;});
  m.status='completed';m.result={rules:{best_of:3,target:11,margin:2},winner:'b',games:[{side_a:10,side_b:12},{side_a:7,side_b:11}]};change(m);assert([400,409].includes((await cloud(p,e)).status));
 }
});

async function liveScore(record,e,timestamp=String(Math.floor(Date.now()/1000)),extra={}) {
 const raw=JSON.stringify({source_id:e.PUBLISH_SOURCE_ID,record,...extra}),key=await crypto.subtle.importKey('raw',new TextEncoder().encode(e.CLOUD_PUBLISH_SECRET),{name:'HMAC',hash:'SHA-256'},false,['sign']);
 const signature=Buffer.from(await crypto.subtle.sign('HMAC',key,new TextEncoder().encode(timestamp+'\n/sync/cloud-score\n'+raw))).toString('hex');
 return worker.fetch(new Request('https://example.test/sync/cloud-score',{method:'POST',body:raw,headers:{'X-Sync-Time':timestamp,'X-Sync-Signature':signature}}),e);
}
function activeScoreFixture() {
 const p=publication(),used=new Set(),active=[];
 for(const row of p.matches){const match=snapshot.matches.find(m=>m.id===row.id);if(active.length===2)break;if([match.side_a,match.side_b].some(s=>s.resolution!=='confirmed'||!s.entry_id||used.has(s.entry_id)))continue;row.status='in_progress';row.court_id=snapshot.courts[active.length].id;row.started_at=new Date().toISOString();used.add(match.side_a.entry_id);used.add(match.side_b.entry_id);active.push(row);}
 const record=row=>({match_id:row.id,started_at:row.started_at,version:1,updated_at:new Date().toISOString(),score:{rules:{best_of:3,target:11,margin:2},games:[],current:{side_a:0,side_b:0},winner:null}});
 return {p,active,records:active.map(record)};
}
test('live points, undo and parallel courts merge without rewriting the schedule',async()=>{
 const e=cloudEnv(),{p,records}=activeScoreFixture();await cloud(p,e);const version=(await published(e)).version;
 assert.equal((await get('/public/live-scores.json',e)).status,200);assert.equal((await(await get('/public/live-scores.json',e)).json()).schedule_version,version);
 const concurrent=await Promise.all(records.map(row=>liveScore(row,e)));assert(concurrent.every(r=>r.status===200));
 assert.equal((await(await get('/public/live-scores.json',e)).json()).matches.length,2);
 const point=structuredClone(records[0]);point.version=2;point.score.current.side_a=1;assert.equal((await liveScore(point,e)).status,200);
 const undo=structuredClone(records[0]);undo.version=3;assert.equal((await liveScore(undo,e)).status,200);
 const old=await liveScore(point,e);assert.equal(old.status,200);assert.equal((await old.json()).version,3);
 const visible=(await(await get('/public/live-scores.json',e)).json()).matches.find(m=>m.match_id===undo.match_id);assert.equal(visible.score.current.side_a,0);
 assert.equal((await published(e)).version,version);
 const duplicate=structuredClone(undo);duplicate.score.current.side_a=1;assert.equal((await liveScore(duplicate,e)).status,409);
 assert.equal((await liveScore(undo,e,'1000000000')).status,401);assert.equal((await liveScore(undo,e,undefined,{actor:'private'})).status,400);
});
test('live channel rejects invalid points, private fields, ended and restarted sessions',async()=>{
 const e=cloudEnv(),{p,active,records}=activeScoreFixture();assert.equal((await liveScore(records[0],e)).status,409);await cloud(p,e);
 for(const change of [r=>r.score.current.side_a=11,r=>r.score.current.side_a=-1,r=>r.score.points=['a'],r=>r.actor='private',r=>r.score.winner='a']){const r=structuredClone(records[0]);change(r);assert.equal((await liveScore(r,e)).status,400);}
 await liveScore(records[0],e);const end=structuredClone(p);end.sequence++;end.matches.find(m=>m.id===active[0].id).status='completed';await cloud(end,e);assert.equal((await liveScore(records[0],e)).status,409);
 const restart=structuredClone(p);restart.sequence+=2;restart.matches.find(m=>m.id===active[0].id).started_at=new Date(Date.now()+1000).toISOString();await cloud(restart,e);
 assert.equal((await liveScore({...records[0],version:2},e)).status,409);
 const fresh={...records[0],version:3,started_at:restart.matches.find(m=>m.id===active[0].id).started_at};assert.equal((await liveScore(fresh,e)).status,200);
 e.BUCKET.put=async()=>{throw new Error('Unavailable');};assert.equal((await liveScore({...fresh,version:4},e)).status,503);
});

test('feature pages load directly, reload safely and preserve unknown-route errors',async()=>{
 const e=env();for(const [path,page]of [['/','live'],['/live','live'],['/live/','live'],['/schedule','schedule'],['/brackets','bracket'],['/standings','standings'],['/schedule?player=SyntheticPlayer001','schedule']]){const response=await get(path,e);assert.equal(response.status,200,path);const html=await response.text();assert(html.includes('data-page="'+page+'"'));assert(html.includes('aria-label="主要功能"'));assert(html.includes('href="/schedule"'));}
 assert.equal((await get('/unknown-page',e)).status,404);
});
