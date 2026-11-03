import test from 'node:test';
import assert from 'node:assert/strict';
import {fixture} from './fixture.mjs';

const origin='http://127.0.0.1:8770';
let platform='',loginCookie='',csrf='';
async function api(path,body,options={}){
 const response=await fetch(origin+'/api/'+path,{method:body===undefined?'GET':'POST',redirect:'manual',headers:{Cookie:[platform,options.noLogin?'':loginCookie].filter(Boolean).join('; '),...(body===undefined?{}:{Origin:options.origin??origin,'Content-Type':'application/json','X-CSRF-Token':options.noCsrf?'':csrf})},body:body===undefined?undefined:JSON.stringify(body)});
 const text=await response.text();let value;try{value=JSON.parse(text);}catch{value={error:text};}return {status:response.status,value,cookie:response.headers.get('set-cookie')};
}
test('local full-stack authentication, persistence, conflict review and atomic saves',async()=>{
 const signIn=await fetch(origin+'/signin-with-chatgpt?return_to=/',{redirect:'manual'});
 platform=signIn.headers.get('set-cookie').split(';')[0];assert.ok(platform);
 assert.equal((await api('state')).status,401);
 const setup=await api('setup',{username:'synthetic-admin',password:'synthetic-test-password'});
 assert.equal(setup.status,200,JSON.stringify(setup.value));loginCookie=setup.cookie.split(';')[0];
 let status=await api('status');csrf=status.value.csrf;assert.ok(csrf);
 assert.equal((await api('setup',{username:'other-admin',password:'synthetic-test-password'})).status,409);
 assert.equal((await api('import',fixture(),{noCsrf:true})).status,403);
 assert.equal((await api('import',fixture(),{origin:'https://attacker.example'})).status,403);
 assert.equal((await api('import',fixture())).status,200);
 assert.equal((await api('import',fixture())).status,409);
 let current=(await api('state')).value,revision=current.revision;
 const updates=current.state.matches.map(m=>({id:m.id,start:m.assignment.start+15,court:m.assignment.court}));
 const invalid=await api('preview',{revision,updates:[{...updates[0],start:updates[1].start}],reason:'Synthetic invalid rest test'});
 assert.equal(invalid.status,200);assert.equal(invalid.value.can_apply,false);assert.equal(invalid.value.id,null);
 const a=await api('preview',{revision,updates,reason:'Synthetic batch test'});
 const b=await api('preview',{revision,updates,reason:'Synthetic concurrent test'});
 assert.equal(a.status,200,JSON.stringify(a.value));assert.equal(a.value.can_apply,true);
 assert.equal((await api('state')).value.revision,revision);
 const saved=await Promise.all([api('confirm',{id:a.value.id}),api('confirm',{id:b.value.id})]);
 assert.deepEqual(saved.map(v=>v.status).sort(),[200,409]);
 assert.equal((await api('confirm',{id:a.value.id})).status,409);
 current=(await api('state')).value;assert.notEqual(current.revision,revision);assert.equal(current.history.length,2);assert.equal(current.state.matches[0].assignment.start,updates[0].start);
 assert.equal((await api('preview',{revision,updates,reason:'Synthetic stale revision'})).status,409);
 assert.equal((await api('export',{})).status,404);
 assert.equal((await api('logout',{})).status,200);
 assert.equal((await api('state')).status,401);
 const wrong=await api('login',{username:'synthetic-admin',password:'wrong'});assert.equal(wrong.status,401);
 const login=await api('login',{username:'synthetic-admin',password:'synthetic-test-password'});assert.equal(login.status,200);loginCookie=login.cookie.split(';')[0];
 status=await api('status');csrf=status.value.csrf;
 assert.equal((await api('state')).value.revision,current.revision);
 console.log('Verified local D1 import, review, concurrent confirmation, history, logout and relogin.');
});
