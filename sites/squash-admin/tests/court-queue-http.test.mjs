import test from 'node:test';
import assert from 'node:assert/strict';
// Only targets the disposable loopback fixture; never production.
const origin='http://127.0.0.1:8770';
test('protected pages load directly and court queues cannot start another court match',async()=>{
 const signIn=await fetch(origin+'/signin-with-chatgpt?return_to=/live',{redirect:'manual'});
 const platform=signIn.headers.get('set-cookie').split(';')[0];let login='',csrf='';
 async function api(path,body){const response=await fetch(origin+'/api/'+path,{method:body===undefined?'GET':'POST',headers:{Cookie:[platform,login].filter(Boolean).join('; '),...(body===undefined?{}:{Origin:origin,'Content-Type':'application/json','X-CSRF-Token':csrf})},body:body===undefined?undefined:JSON.stringify(body)});if(response.headers.get('set-cookie'))login=response.headers.get('set-cookie').split(';')[0];return {status:response.status,value:await response.json()};}
 assert.equal((await api('control')).status,401);
 csrf=(await api('status')).value.csrf;assert.ok(csrf);
 for(const path of ['/live','/schedule']){const response=await fetch(origin+path,{headers:{Cookie:[platform,login].join('; ')}});assert.equal(response.status,200);}
 const state=(await api('state')).value,control=(await api('control')).value;
 for(const court of control.courts){for(const match of court.queue){assert.equal(state.state.matches.find(row=>row.id===match.id).assignment.court,court.id);}}
 const court=control.courts.find(c=>c.queue.some(m=>m.eligible)),match=court.queue.find(m=>m.eligible),other=control.courts.find(c=>c.id!==court.id&&!c.current);
 const response=await api('live',{revision:control.revision,sequence:control.sequence,action:'start',court_id:other.id,match_id:match.id});assert.equal(response.status,409);assert.match(response.value.error,/其他場地/);
 const unchanged=(await api('state')).value;assert.equal(unchanged.revision,state.revision);assert.deepEqual(unchanged.state,state.state);
});
