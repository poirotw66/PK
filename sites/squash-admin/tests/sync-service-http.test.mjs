import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
const origin='http://127.0.0.1:8770';
const serviceToken='synthetic-unattended-sync-service-token';
test('a sessionless cloud retry recovers durable publication and score delivery after reader failure',async()=>{
 let failing=true;const received=[];
 const reader=http.createServer(async(request,response)=>{const chunks=[];for await(const chunk of request)chunks.push(chunk);const value=JSON.parse(Buffer.concat(chunks).toString());response.setHeader('Content-Type','application/json');if(failing){response.writeHead(503);response.end(JSON.stringify({error:'Synthetic reader outage'}));return;}received.push({path:request.url,value});response.end(JSON.stringify(request.url==='/sync/cloud-score'?{source_id:value.source_id,match_id:value.record.match_id,started_at:value.record.started_at,version:value.record.version}:{source_id:value.source_id,sequence:value.sequence,version:'a'.repeat(64)}));});
 await new Promise(resolve=>reader.listen(8780,'127.0.0.1',resolve));
 try {
  const signIn=await fetch(origin+'/signin-with-chatgpt?return_to=/',{redirect:'manual'}),platform=signIn.headers.get('set-cookie').split(';')[0];let login='',csrf='';
  async function api(path,body){const r=await fetch(origin+'/api/'+path,{method:body===undefined?'GET':'POST',headers:{Cookie:[platform,login].filter(Boolean).join('; '),...(body===undefined?{}:{Origin:origin,'Content-Type':'application/json','X-CSRF-Token':csrf})},body:body===undefined?undefined:JSON.stringify(body)});if(r.headers.get('set-cookie'))login=r.headers.get('set-cookie').split(';')[0];const value=await r.json();assert.equal(r.status,200,JSON.stringify(value));return value;}
  async function service(token=serviceToken,method='POST',body){return fetch(origin+'/api/sync-service',{method,headers:token?{Authorization:'Bearer '+token}:{},body});}
  assert.equal((await service(null)).status,401);assert.equal((await service('synthetic-wrong-service-token')).status,401);assert.equal((await service(serviceToken,'GET')).status,405);assert.equal((await service(serviceToken,'POST','{}')).status,400);
  csrf=(await api('status')).csrf;assert.ok(csrf);
  let control=await api('control');assert((await api('state')).state.name.startsWith('Synthetic'));for(const current of control.courts.filter(row=>row.current)){control=await api('live',{revision:control.revision,sequence:control.sequence,action:'cancel',court_id:current.id,match_id:current.current.id,reason:'Restore disposable fixture before service test'});}const court=control.courts.find(row=>row.queue.some(match=>match.eligible)),match=court.queue.find(match=>match.eligible);
  control=await api('live',{revision:control.revision,sequence:control.sequence,action:'start',court_id:court.id,match_id:match.id});assert.equal(control.pending,true);
  let score=await api('score?match_id='+encodeURIComponent(match.id));score=await api('score',{match_id:match.id,version:score.version,action:'a'});
  const saved=await api('state');await api('logout',{});login='';
  failing=false;
  const response=await service();assert.equal(response.status,200);const result=await response.json();assert.equal(result.before.publication_pending,true);assert.equal(result.before.scores_pending,1);assert.equal(result.pending,false);assert.equal(result.after.scores_pending,0);assert.equal(result.after.acknowledged_sequence,result.after.sequence);
  assert(received.some(row=>row.path==='/sync/cloud-publish'));assert.equal(received.find(row=>row.path==='/sync/cloud-score').value.record.version,score.version);
  const count=received.length,again=await(await service()).json();assert.equal(again.pending,false);assert.equal(received.length,count);
  csrf=(await api('status')).csrf;assert.ok(csrf);const restored=await api('state');assert.equal(restored.revision,saved.revision);assert.deepEqual(restored.state,saved.state);
  // Restore the disposable fixture through the ordinary authenticated operation.
  control=await api('control');await api('live',{revision:control.revision,sequence:control.sequence,action:'cancel',court_id:court.id,match_id:match.id,reason:'Synthetic service recovery verification'});
 } finally {await new Promise(resolve=>reader.close(resolve));}
});
