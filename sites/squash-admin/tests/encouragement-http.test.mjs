import assert from 'node:assert/strict';
import {readdir,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import test from 'node:test';
import {Miniflare} from 'miniflare';
import {encouragementFixture} from './encouragement-fixture.mjs';

test('encouragement checks update after results and enforce session, CSRF, CAS, expiry and one-use confirmation',async t=>{
 const origin='https://encouragement.example.test',server='dist/server',config=JSON.parse(await readFile(server+'/wrangler.json','utf8'));
 const modules=(await readdir(server,{recursive:true})).filter(file=>file.endsWith('.js')&&file!==config.main).map(file=>({type:'ESModule',path:join(server,file)}));
 const runtime=new Miniflare({host:'127.0.0.1',modulesRoot:server,modules:[{type:'ESModule',path:join(server,config.main)},...modules],compatibilityDate:config.compatibility_date,compatibilityFlags:config.compatibility_flags,d1Databases:{DB:'encouragement-http'},bindings:{APP_ORIGIN:origin,PRIMARY_ADMIN_EMAIL:'owner@example.test',ADDITIONAL_ADMIN_EMAIL:'invited@example.test',PUBLISH_SOURCE_ID:'encouragement-test'}});
 t.after(()=>runtime.dispose());const db=await runtime.getD1Database('DB');for(const file of(await readdir('drizzle')).filter(file=>file.endsWith('.sql')).sort())await db.batch((await readFile(join('drizzle',file),'utf8')).split('--> statement-breakpoint').map(s=>db.prepare(s.trim())));
 function client(user='owner'){
  let cookie='',csrf='';return async(path,body,noCsrf=false)=>{const headers={'oai-authenticated-user-id':user,'oai-authenticated-user-email':user==='owner'?'owner@example.test':'invited@example.test',Cookie:cookie};if(body!==undefined)Object.assign(headers,{Origin:origin,'Content-Type':'application/json','X-CSRF-Token':noCsrf?'':csrf});const response=await runtime.dispatchFetch(origin+'/api/'+path,{method:body===undefined?'GET':'POST',headers,body:JSON.stringify(body)});const value=await response.json();if(response.headers.get('set-cookie'))cookie=response.headers.get('set-cookie').split(';')[0];if(value.csrf)csrf=value.csrf;return {status:response.status,body:value};};
 }
 const api=client(),otherSession=client(),invited=client('invited');await api('status');const state=encouragementFixture();state.matches[1].status='planned';delete state.matches[1].result;
 assert.equal((await api('import',state)).status,200);await invited('status');await otherSession('status');
 const request=async()=>{const q=(await api('encouragement-status')).body;return {revision:q.revision,sequence:q.sequence,division_id:'d0',seed:123};};
 assert.deepEqual((await api('encouragement-status')).body.divisions.map(q=>q.status),['waiting','waiting']);assert.equal((await api('encouragement-preview',await request())).status,409);
 let control=(await api('control')).body;assert.equal((await api('publish',{revision:control.revision,sequence:control.sequence})).status,200);
 control=(await api('control')).body;const result={rules:{best_of:3,target:11,margin:2},winner:'a',outcome:'played',games:[{side_a:11,side_b:5},{side_a:11,side_b:6}]};
 assert.equal((await api('quick-result',{revision:control.revision,sequence:control.sequence,match_id:'m2',expected_result:null,result})).status,200);
 assert.deepEqual((await api('encouragement-status')).body.divisions.map(q=>q.status),['ready','waiting']);
 assert.equal((await invited('encouragement-preview',await request())).status,403);assert.equal((await api('encouragement-preview',await request(),true)).status,403);
 const expired=await api('encouragement-preview',await request());assert.equal(expired.status,200,JSON.stringify(expired.body));await db.prepare('UPDATE previews SET created=? WHERE id=?').bind(Date.now()-700000,expired.body.id).run();assert.equal((await api('encouragement-confirm',{id:expired.body.id})).status,409);
 const wrongSession=await api('encouragement-preview',await request());assert.equal((await otherSession('encouragement-confirm',{id:wrongSession.body.id})).status,409);
 const stale=await api('encouragement-preview',await request());await db.prepare("UPDATE tournament SET revision='concurrent-revision' WHERE id=1").run();assert.equal((await api('encouragement-confirm',{id:stale.body.id})).status,409);
 const before=(await api('state')).body;const preview=await api('encouragement-preview',await request());assert.equal(preview.status,200,JSON.stringify(preview.body));assert.equal(preview.body.can_apply,true);assert.equal((await api('state')).body.revision,before.revision);
 const saved=await api('encouragement-confirm',{id:preview.body.id});assert.equal(saved.status,200,JSON.stringify(saved.body));assert.equal(saved.body.requires_publication,true);const after=(await api('state')).body;assert.deepEqual(after.state.matches.slice(0,before.state.matches.length),before.state.matches);assert.equal(after.state.matches.length,before.state.matches.length+1);assert.equal((await api('encouragement-status')).body.divisions[1].status,'waiting');assert.equal((await api('encouragement-confirm',{id:preview.body.id})).status,409);assert.equal((await api('encouragement-preview',await request())).status,409);
 const audit=await db.prepare('SELECT actor,changes FROM revisions WHERE id=?').bind(after.revision).first();assert.ok(audit.actor);assert.match(audit.changes,/encouragement_draw/);
});
