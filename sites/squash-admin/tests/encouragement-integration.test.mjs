import assert from 'node:assert/strict';
import { mkdtemp, readdir, readFile, rm, mkdir, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import test from 'node:test';
import { createHash } from 'node:crypto';
import { Miniflare, Response as WorkerResponse } from 'miniflare';
import { fixture } from './fixture.mjs';

test('per-division encouragement qualification, draw, scheduling and final result reach the reader without altering completed scores', async t => {
 const readerRoot=resolve(process.env.PK_READER_ROOT || '../squash-reader');
 const source=await readFile(join(readerRoot,'dist/server/index.js'),'utf8');
 const head=JSON.parse(await readFile(join(readerRoot,'dist/public/version.json'),'utf8'));
 const template=JSON.parse(await readFile(join(readerRoot,'dist/public',head.snapshot_url),'utf8'));
 let schedule=fixture();
 schedule.players.push({id:'p3',name:'SyntheticPlayer003',rest:0},{id:'p4',name:'SyntheticPlayer004',rest:0});
 schedule.entries=schedule.players.map((player,index)=>({id:'e'+(index+1),player_id:player.id}));
 schedule.matches.forEach(match=>match.allowed_outcomes=['played','walkover','retired','double_forfeit']);
 schedule.matches.forEach(match=>match.stage='淘汰賽');
 const first=schedule.matches[0];first.slots={a:{kind:'entry',entry_id:'e1'},b:{kind:'entry',entry_id:'e2'}};first.entry_ids={a:'e1',b:'e2'};
 const second=schedule.matches[1];Object.assign(second,{side_a:'SyntheticPlayer003',side_b:'SyntheticPlayer004',players:['p3','p4'],assignment:{start:first.assignment.start,court:'b'},slots:{a:{kind:'entry',entry_id:'e3'},b:{kind:'entry',entry_id:'e4'}},entry_ids:{a:'e3',b:'e4'}});
 schedule.matches.push({...structuredClone(first),id:'m3',code:'3',side_a:'1場勝者',side_b:'2場勝者',players:[],dependencies:[{id:'m1',lag:60},{id:'m2',lag:60}],assignment:{start:first.assignment.start+75,court:'a'},slots:{a:{kind:'winner',match_id:'m1'},b:{kind:'winner',match_id:'m2'}},entry_ids:{a:null,b:null}});
 schedule.encouragement_sources={source_revision:schedule.source_revision,divisions:[{division_id:'division',division:'Synthetic division',stage_id:'stage',first_round:schedule.matches.slice(0,2).map(match=>({match_id:match.id,side_a:{entry_id:match.entry_ids.a,player_id:match.players[0],name:match.side_a},side_b:{entry_id:match.entry_ids.b,player_id:match.players[1],name:match.side_b}}))}]};
 let snapshot={...template,workspace_id:'synthetic-workflow',revision_id:'synthetic-source',name:'Synthetic workflow',dates:undefined,standings:[]};delete snapshot.dates;
 snapshot.divisions=[{...template.divisions[0],id:'division',name:'Synthetic division',entry_count:4,single_entry_name:null}];
 snapshot.stages=[{...template.stages[0],id:'stage',division_id:'division',type:'elimination',name:'淘汰賽'}];
 snapshot.courts=schedule.courts.map(court=>({id:court.id,name:court.name}));
 snapshot.matches=schedule.matches.map(match=>({...template.matches[0],id:match.id,code:match.code,division_id:'division',stage_id:'stage',round:match.id==='m3'?2:1,kind:'competitive',status:'planned',result:null,court_id:match.assignment.court,planned_start_at:new Date(match.assignment.start*60000).toISOString(),started_at:null,called_at:null,delay_active:false,delay_minutes:0,
  side_a:{name:match.side_a,entry_id:match.entry_ids.a,source_type:match.id==='m3'?'match_winner':'entry',source_match_id:match.id==='m3'?'m1':null,source_label:null,resolution:match.entry_ids.a?'confirmed':'pending'},
  side_b:{name:match.side_b,entry_id:match.entry_ids.b,source_type:match.id==='m3'?'match_winner':'entry',source_match_id:match.id==='m3'?'m2':null,source_label:null,resolution:match.entry_ids.b?'confirmed':'pending'}}));
 snapshot.version=createHash('sha256').update(JSON.stringify(snapshot)).digest('hex');
 const finalMatch=schedule.matches.find(match=>match.slots?.a.kind==='winner'&&match.slots?.b.kind==='winner');
 assert.ok(finalMatch,'The synthetic workflow requires two semifinals and a final.');
 const roles={first:finalMatch.slots.a.match_id,second:finalMatch.slots.b.match_id,final:finalMatch.id};
 const expected={first:schedule.matches.find(match=>match.id===roles.first).entry_ids.a,second:schedule.matches.find(match=>match.id===roles.second).entry_ids.a};
 const descriptor={...head,workspace_id:snapshot.workspace_id,revision_id:snapshot.revision_id,version:snapshot.version,snapshot_url:'/public/snapshots/'+snapshot.version+'.json'};
 const assets={[descriptor.snapshot_url]:{body:Buffer.from(JSON.stringify(snapshot)).toString('base64'),type:'application/json'}};
 const rewritten=source.replace(/const STORAGE_PREFIX=.*?;\n/,`const STORAGE_PREFIX=${JSON.stringify('workspaces/'+snapshot.workspace_id+'/')};\n`).replace(/^const ASSETS=.*?;\nconst BOOTSTRAP=.*?;\n/,`const ASSETS=${JSON.stringify(assets)};\nconst BOOTSTRAP=${JSON.stringify(descriptor)};\n`);
 const {default:reader}=await import('data:text/javascript;base64,'+Buffer.from(rewritten).toString('base64'));
 class Bucket { objects=new Map();counter=0;async get(key){const item=this.objects.get(key);return item?{...item,json:async()=>JSON.parse(item.raw),body:new Response(item.raw).body}:null;}async head(key){return this.get(key);}async put(key,raw,options={}){const previous=this.objects.get(key);if(options.onlyIf?.etagDoesNotMatch==='*'&&previous||options.onlyIf?.etagMatches&&previous?.etag!==options.onlyIf.etagMatches)return null;const item={etag:String(++this.counter),raw,customMetadata:options.customMetadata};this.objects.set(key,item);return item;} }
 const readerEnv={BUCKET:new Bucket(),PUBLISH_SOURCE_ID:'synthetic-workflow-source',CLOUD_PUBLISH_SECRET:'synthetic-workflow-secret'};
 const config=JSON.parse(await readFile('dist/server/wrangler.json','utf8')),server='dist/server';
 const modules=(await readdir(server,{recursive:true})).filter(file=>file.endsWith('.js')&&file!==config.main).map(file=>({type:'ESModule',path:join(server,file)}));
 const persistence=await mkdtemp(join(tmpdir(),'pk-integrated-reader-'));let newerAck=false,offline=false;
 const origin='https://workflow-admin.example.test';
 const runtime=new Miniflare({host:'127.0.0.1',modulesRoot:server,modules:[{type:'ESModule',path:join(server,config.main)},...modules],compatibilityDate:config.compatibility_date,compatibilityFlags:config.compatibility_flags,d1Databases:{DB:'workflow'},d1Persist:persistence,
  bindings:{APP_ORIGIN:origin,PRIMARY_ADMIN_EMAIL:'owner@example.test',PUBLISH_SOURCE_ID:readerEnv.PUBLISH_SOURCE_ID,CLOUD_PUBLISH_SECRET:readerEnv.CLOUD_PUBLISH_SECRET,READER_ORIGIN:'https://workflow-reader.example.test'},
  outboundService:async request=>{const raw=await request.text();if(offline)return new WorkerResponse(JSON.stringify({error:"synthetic offline"}),{status:503});if(newerAck){const parsed=JSON.parse(raw);return new WorkerResponse(JSON.stringify({source_id:readerEnv.PUBLISH_SOURCE_ID,sequence:parsed.sequence+5,version:'a'.repeat(64),error:'Superseded publication'}),{status:409});}const response=await reader.fetch(new Request(request.url,{method:request.method,headers:request.headers,body:raw}),readerEnv);const responseBody=await response.text();if(response.status>=400)console.error('Reader rejected:',response.status,responseBody);return new WorkerResponse(responseBody,{status:response.status,headers:response.headers});}});
 t.after(async()=>{await runtime.dispose();await rm(persistence,{recursive:true,force:true});});
 const db=await runtime.getD1Database('DB');for(const file of(await readdir('drizzle')).filter(file=>file.endsWith('.sql')).sort()){const sql=await readFile(join('drizzle',file),'utf8');await db.batch(sql.split('--> statement-breakpoint').map(statement=>db.prepare(statement.trim())));}
 let cookie='',csrf='';async function api(path,body){const headers={'oai-authenticated-user-id':'owner','oai-authenticated-user-email':'owner@example.test',Cookie:cookie};if(body!==undefined)Object.assign(headers,{Origin:origin,'Content-Type':'application/json','X-CSRF-Token':csrf});const response=await runtime.dispatchFetch(origin+'/api/'+path,{method:body===undefined?'GET':'POST',headers,body:JSON.stringify(body)});if(response.headers.get('set-cookie'))cookie=response.headers.get('set-cookie').split(';')[0];const value=await response.json();if(value.csrf)csrf=value.csrf;assert.equal(response.status,200,path+' '+JSON.stringify(value));return value;}
 async function publicState(){const response=await reader.fetch(new Request('https://workflow-reader.example.test/public/version.json'),readerEnv);const current=await response.json();return(await reader.fetch(new Request('https://workflow-reader.example.test'+current.snapshot_url),readerEnv)).json();}
 async function publish(){const control=await api('control');await api('publish',{revision:control.revision,sequence:control.sequence});const publication=await api('control');assert.equal(publication.pending,false,JSON.stringify(publication.error));}
 async function result(id,value){const control=await api('control');const plan=await api('result-preview',{revision:control.revision,sequence:control.sequence,match_id:id,result:value,reason:'Synthetic signed result sheet'});await api('result-confirm',{id:plan.id});const publication=await api('control');assert.equal(publication.pending,false,JSON.stringify(publication.error));}
 await api('status');await api('import',schedule);await publish();
 const rules={best_of:3,target:11,margin:2};
 let qualification=await api('encouragement-status');assert.equal(qualification.divisions[0].status,'waiting');
 await result('m1',{rules,winner:'a',games:[{side_a:11,side_b:4},{side_a:11,side_b:6}]});
 qualification=await api('encouragement-status');assert.equal(qualification.divisions[0].pending.length,1);
 await result('m2',{rules,winner:'a',games:[{side_a:11,side_b:3},{side_a:11,side_b:8}]});
 const before=(await api('state')).state;qualification=await api('encouragement-status');assert.equal(qualification.divisions[0].status,'ready');
 let control=await api('control');const plan=await api('encouragement-preview',{revision:control.revision,sequence:control.sequence,division_id:'division',seed:20261126});
 assert.equal(plan.can_apply,true,JSON.stringify(plan));assert.equal(plan.matches.length,1);
 assert.ok(plan.matches.every(match=>match.scoring_rules.best_of===3));
 assert.equal((await api('state')).state.matches.length,3,'Preview must not write the schedule');
 await api('encouragement-confirm',{id:plan.id});
 const after=(await api('state')).state;assert.equal(after.matches.length,4);
 for(const match of before.matches)assert.deepEqual(after.matches.find(row=>row.id===match.id),match,'Original matches must stay exact');
 await publish();const visible=await publicState();assert.equal(visible.matches.length,4);
 const added=visible.matches.find(match=>match.id===plan.matches[0].id);assert.ok(added);assert.equal(added.kind,'placement');
 assert.deepEqual(new Set([added.side_a.entry_id,added.side_b.entry_id]),new Set(['e2','e4']));
 const publicBefore=new Map(visible.matches.filter(match=>['m1','m2'].includes(match.id)).map(match=>[match.id,match.result]));
 await result(added.id,{rules,winner:'a',games:[{side_a:11,side_b:7},{side_a:11,side_b:8}]});
 const finished=await publicState();assert.equal(finished.matches.find(match=>match.id===added.id).status,'completed');
 for(const [id,value] of publicBefore)assert.deepEqual(finished.matches.find(match=>match.id===id).result,value);
 assert.equal((await api('encouragement-status')).divisions[0].status,'drawn');
 assert.equal((await api('control')).pending,false);
});
