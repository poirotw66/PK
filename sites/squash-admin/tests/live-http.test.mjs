import test from 'node:test';
import assert from 'node:assert/strict';
const origin='http://127.0.0.1:8770',reader='http://127.0.0.1:8780';
test('local cloud publication, durable scores, stale protection, finish and public persistence',async()=>{
 const auth=await fetch(origin+'/signin-with-chatgpt?return_to=/',{redirect:'manual'});const platform=auth.headers.get('set-cookie').split(';')[0];let login='',csrf='';
 async function api(path,body,options={}){const r=await fetch(origin+'/api/'+path,{method:body===undefined?'GET':'POST',headers:{Cookie:[platform,login].filter(Boolean).join('; '),...(body===undefined?{}:{Origin:origin,'Content-Type':'application/json','X-CSRF-Token':options.noCsrf?'':csrf})},body:body===undefined?undefined:JSON.stringify(body)});if(r.headers.get('set-cookie'))login=r.headers.get('set-cookie').split(';')[0];return {status:r.status,value:await r.json()};}
 async function liveVersion(matchId,version){for(let retry=0;retry<100;retry++){const row=(await(await fetch(reader+'/public/live-scores.json')).json()).matches.find(m=>m.match_id===matchId);if(row?.version>=version)return row;await new Promise(resolve=>setTimeout(resolve,50));}assert.fail('Background score publication did not complete');}
 async function snapshot(){const head=await(await fetch(reader+'/public/version.json')).json();return(await fetch(new URL(head.snapshot_url,reader))).json();}
 assert.equal((await api('control')).status,401);
 csrf=(await api('status')).value.csrf;assert.ok(csrf);
 let control=(await api('control')).value;
 assert.equal((await api('publish',{revision:control.revision,sequence:control.sequence},{noCsrf:true})).status,403);
 let published=await api('publish',{revision:control.revision,sequence:control.sequence});assert.equal(published.status,200);assert.equal(published.value.pending,false,JSON.stringify(published.value.error));control=published.value;
 assert.equal((await api('publish',{revision:control.revision,sequence:control.sequence-1})).status,409);
 assert.equal((await snapshot()).matches.length,3);
 const court=control.courts.find(c=>c.queue.some(m=>m.eligible)),match=court.queue.find(m=>m.eligible);const input={revision:control.revision,sequence:control.sequence,action:'start',court_id:court.id,match_id:match.id};
 let started=await api('live',input);assert.equal(started.status,200);assert.equal(started.value.pending,false);assert.equal(started.value.courts.find(c=>c.id===court.id).current.id,match.id);assert.equal((await snapshot()).matches.find(m=>m.id===match.id).status,'in_progress');assert.equal((await api('live',input)).status,409);
 control=started.value;const cancel={revision:control.revision,sequence:control.sequence,action:'cancel',court_id:court.id,match_id:match.id};assert.equal((await api('live',cancel)).status,400);
 let canceled=await api('live',{...cancel,reason:'Synthetic mistaken-start test'});assert.equal(canceled.status,200);assert.equal((await snapshot()).matches.find(m=>m.id===match.id).started_at,null);
 control=canceled.value;started=await api('live',{...input,revision:control.revision,sequence:control.sequence});assert.equal(started.status,200);control=started.value;
 let score=(await api('score?match_id='+encodeURIComponent(match.id))).value;
 assert.equal((await api('live',{revision:control.revision,sequence:control.sequence,action:'end',court_id:court.id,match_id:match.id,score_version:score.version})).status,409);
 const staleVersion=score.version;
 const concurrent=await Promise.all(['a','b'].map(action=>api('score',{match_id:match.id,version:staleVersion,action})));
 assert.deepEqual(concurrent.map(row=>row.status).sort(),[200,409]);
 score=(await api('score?match_id='+encodeURIComponent(match.id))).value;assert.equal(score.score.points.length,1);
 score=(await api('score',{match_id:match.id,version:score.version,action:'undo'})).value;
 for(let point=0;point<22;point++){const added=await api('score',{match_id:match.id,version:score.version,action:'a'});assert.equal(added.status,200);score=added.value;assert.equal(score.sync_pending,true);const live=await liveVersion(match.id,score.version);assert.equal(live.version,score.version);}
 assert.equal((await api('score',{match_id:match.id,version:score.version,action:'b'})).status,409);
 assert.equal((await api('score?match_id='+encodeURIComponent(match.id))).value.score.points.length,22);
 assert.equal((await api('live',{revision:control.revision,sequence:control.sequence,action:'end',court_id:court.id,match_id:match.id,score_version:score.version-1})).status,409);
 const ended=await api('live',{revision:control.revision,sequence:control.sequence,action:'end',court_id:court.id,match_id:match.id,score_version:score.version});assert.equal(ended.status,200);assert.equal(ended.value.pending,false);assert.equal((await snapshot()).matches.find(m=>m.id===match.id).status,'completed');assert.deepEqual((await snapshot()).matches.find(m=>m.id===match.id).result.games,[{side_a:11,side_b:0},{side_a:11,side_b:0}]);assert.equal((await api('state')).value.state.matches.find(m=>m.id===match.id).status,'completed');
 assert.equal((await api('control')).value.courts.find(c=>c.id===court.id).current,null);
 assert.equal((await api('score',{match_id:match.id,version:score.version,action:'undo'})).status,409);
 console.log('Verified synthetic admin D1 → signed reader R2 → public snapshot without production mutations.');
});
