const liveScoresKey = 'live/scores.json';
async function publishLiveScore(request, env) {
  if (!env.BUCKET) return json({error:'Storage unavailable'},503);
  const timestamp = request.headers.get('X-Sync-Time'), signature = request.headers.get('X-Sync-Signature');
  if (!env.CLOUD_PUBLISH_SECRET || !env.PUBLISH_SOURCE_ID || !/^\d{10}$/.test(timestamp || '') || Math.abs(Date.now()/1000-Number(timestamp)) > 120 || !/^[a-f0-9]{64}$/.test(signature || '')) return json({error:'Unauthorized'},401);
  let size=0;const chunks=[],reader=request.body?.getReader();
  if (reader) while(true) {const {value,done}=await reader.read();if(done)break;size+=value.byteLength;if(size>16384){await reader.cancel();return json({error:'Payload too large'},413);}chunks.push(value);}
  const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.byteLength;}
  const raw=new TextDecoder().decode(bytes),key=await crypto.subtle.importKey('raw',new TextEncoder().encode(env.CLOUD_PUBLISH_SECRET),{name:'HMAC',hash:'SHA-256'},false,['verify']);
  if(!await crypto.subtle.verify('HMAC',key,Uint8Array.from(signature.match(/../g),v=>parseInt(v,16)),new TextEncoder().encode(timestamp+'\n/sync/cloud-score\n'+raw)))return json({error:'Unauthorized'},401);
  let value;try{value=JSON.parse(raw);}catch{return json({error:'Invalid JSON'},400);}
  if(!exact(value,['source_id','record']) || value.source_id!==env.PUBLISH_SOURCE_ID || !validLiveScore(value.record) || Date.parse(value.record.updated_at)>Date.now()+120000)return json({error:'Invalid live score'},400);
  const {current}=await readCurrent(env);
  if(current?.authority!=='cloud'||current.cloud_source_id!==value.source_id)return json({error:'Publish the cloud schedule first'},409);
  const stored=await env.BUCKET.get('snapshots/'+current.descriptor.version+'.json');
  const snapshot=stored?await stored.json():null,record=value.record;
  const match=snapshot?.matches.find(match=>match.id===record.match_id);
  if(!match || match.status!=='in_progress' || match.started_at!==record.started_at || match.result)return json({error:'Match is no longer active'},409);
  for(let attempt=0;attempt<4;attempt++){
    const prior=await env.BUCKET.get(liveScoresKey),document=prior?await prior.json():{source_id:value.source_id,matches:[]};
    if(document.source_id!==value.source_id)return json({error:'Unexpected source'},409);
    const old=document.matches.find(row=>row.match_id===record.match_id);
    const ack=version=>({source_id:value.source_id,match_id:record.match_id,started_at:record.started_at,version});
    if(old && old.version>=record.version){
      if(old.started_at!==record.started_at)return json({error:'Stale scoring session'},409);
      if(old.version===record.version && JSON.stringify(old)!==JSON.stringify(record))return json({error:'Score version content mismatch'},409);
      return json(ack(old.version));
    }
    const matches=document.matches.filter(row=>row.match_id!==record.match_id);
    matches.push(record);
    const next=JSON.stringify({source_id:value.source_id,updated_at:new Date().toISOString(),matches});
    const saved=await env.BUCKET.put(liveScoresKey,next,{onlyIf:prior?{etagMatches:prior.etag}:{etagDoesNotMatch:'*'},httpMetadata:{contentType:'application/json'}});
    if(saved)return json(ack(record.version));
  }
  return json({error:'Concurrent score update; retry'},409);
}
async function publicLiveScores(env) {
  if(!env.BUCKET)return json({error:'Storage unavailable'},503);
  const object=await env.BUCKET.get(liveScoresKey),document=object?await object.json():null;
  const {current}=await readCurrent(env);
  return json({schedule_version:current?.descriptor.version??BOOTSTRAP.version,updated_at:document?.updated_at??null,matches:document?.matches??[]});
}
