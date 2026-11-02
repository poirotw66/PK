const json = (value, status = 200, headers = {}) => new Response(JSON.stringify(value), {status, headers:{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store',...headers}});
const currentKey = 'sync/current.json';
const hex = bytes => [...new Uint8Array(bytes)].map(b => b.toString(16).padStart(2,'0')).join('');

function validate(value, schema = SCHEMA) {
  if (schema.$ref) return validate(value, SCHEMA.$defs[schema.$ref.split('/').at(-1)]);
  if (schema.anyOf) {
    for (const choice of schema.anyOf) { try { validate(value,choice); return; } catch {} }
    throw new Error('Invalid union');
  }
  if ('const' in schema && value !== schema.const) throw new Error('Invalid constant');
  if (schema.enum && !schema.enum.includes(value)) throw new Error('Invalid enum');
  const type = schema.type;
  if (type === 'null' && value !== null || type === 'string' && typeof value !== 'string' || type === 'boolean' && typeof value !== 'boolean' || type === 'integer' && !Number.isInteger(value) || type === 'number' && (typeof value !== 'number' || !Number.isFinite(value))) throw new Error('Invalid type');
  if (typeof value === 'number' && schema.minimum !== undefined && value < schema.minimum) throw new Error('Invalid minimum');
  if (type === 'array') { if (!Array.isArray(value)) throw new Error('Invalid array'); for (const item of value) validate(item,schema.items); }
  if (type === 'object') {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid object');
    for (const key of schema.required || []) if (!(key in value)) throw new Error('Missing field');
    for (const [key,item] of Object.entries(value)) {
      if (!schema.properties?.[key]) { if (schema.additionalProperties === false) throw new Error('Unexpected field'); }
      else validate(item,schema.properties[key]);
    }
  }
}

async function authorized(request, raw, env) {
  const timestamp = request.headers.get('X-Sync-Time');
  const signature = request.headers.get('X-Sync-Signature');
  if (!env.SYNC_SECRET || !/^\d{10}$/.test(timestamp || '') || Math.abs(Date.now()/1000-Number(timestamp)) > 120 || !/^[a-f0-9]{64}$/.test(signature || '')) return false;
  const key = await crypto.subtle.importKey('raw',new TextEncoder().encode(env.SYNC_SECRET),{name:'HMAC',hash:'SHA-256'},false,['verify']);
  const bytes = Uint8Array.from(signature.match(/../g), v => parseInt(v,16));
  return crypto.subtle.verify('HMAC',key,bytes,new TextEncoder().encode(timestamp+'\n'+raw));
}

async function readCurrent(env) {
  const object = await env.BUCKET.get(currentKey);
  return {object, current:object ? await object.json() : null};
}

async function writeCurrent(env, current, previous) {
  const object = await env.BUCKET.put(currentKey,JSON.stringify(current),{onlyIf:previous ? {etagMatches:previous.etag} : {etagDoesNotMatch:'*'},httpMetadata:{contentType:'application/json'}});
  if (!object) throw new Error('Concurrent update');
}

async function ingest(request, env, path) {
  if (!env.BUCKET) return json({error:'Storage unavailable'},503);
  if (Number(request.headers.get('Content-Length')) > 2_000_000) return json({error:'Payload too large'},413);
  const chunks=[];let length=0;
  const reader=request.body?.getReader();
  if (reader) while (true) {
    const {value,done}=await reader.read();if(done)break;
    length+=value.byteLength;
    if(length>2_000_000){await reader.cancel();return json({error:'Payload too large'},413);}
    chunks.push(value);
  }
  const body=new Uint8Array(length);let offset=0;
  for(const chunk of chunks){body.set(chunk,offset);offset+=chunk.byteLength;}
  const raw=new TextDecoder().decode(body);
  if (!await authorized(request,raw,env)) return json({error:'Unauthorized'},401);
  let value;
  try { value=JSON.parse(raw); } catch { return json({error:'Invalid JSON'},400); }
  const {object,current} = await readCurrent(env);
  const now = new Date().toISOString();
  const expected = env.SYNC_WORKSPACE_ID || BOOTSTRAP.workspace_id;
  if (value.workspace_id !== expected) return json({error:'Unexpected workspace'},409);
  if (path === '/sync/heartbeat') {
    if (Object.keys(value).some(key=>!['version','workspace_id'].includes(key))) return json({error:'Unexpected field'},400);
    if (!current || current.descriptor.version !== value.version) return json({error:'Snapshot required'},409);
    try { await writeCurrent(env,{...current,last_heartbeat_at:now},object); } catch { return json({error:'Concurrent update; retry'},409); }
    return json({version:value.version,checked_at:now});
  }
  try {
    validate(value);
    if (!/^[a-f0-9]{64}$/.test(value.version) || !Number.isFinite(Date.parse(value.updated_at)) || Date.parse(value.updated_at)>Date.now()+120000) throw new Error('Invalid version or time');
  } catch { return json({error:'Invalid public snapshot'},400); }
  if (current && Date.parse(value.updated_at) < Date.parse(current.descriptor.updated_at)) return json({error:'Stale revision'},409);
  const snapshotKey = 'snapshots/'+value.version+'.json';
  const digest = hex(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw)));
  const stored = await env.BUCKET.put(snapshotKey,raw,{onlyIf:{etagDoesNotMatch:'*'},customMetadata:{sha256:digest},httpMetadata:{contentType:'application/json'}});
  if (!stored) {
    const existing = await env.BUCKET.head(snapshotKey);
    if (existing?.customMetadata?.sha256 !== digest) return json({error:'Version content mismatch'},409);
  }
  const descriptor = Object.fromEntries(['schema_version','version','workspace_id','revision_id','updated_at'].map(key=>[key,value[key]]));
  descriptor.snapshot_url='/public/snapshots/'+value.version+'.json';
  try { await writeCurrent(env,{descriptor,synced_at:current?.descriptor.version===value.version ? current.synced_at : now,last_heartbeat_at:now},object); }
  catch { return json({error:'Concurrent update; retry'},409); }
  return json({version:value.version,synced_at:now});
}

export async function handle(request, env) {
  const path = new URL(request.url).pathname;
  if (path.startsWith('/sync/')) {
    if (!['/sync/update','/sync/heartbeat'].includes(path)) return json({error:'Not found'},404);
    return request.method === 'POST' ? ingest(request,env,path) : json({error:'Method not allowed'},405);
  }
  if (!['GET','HEAD'].includes(request.method)) return json({error:'Method not allowed'},405);
  if (['/public/version.json','/public/status.json'].includes(path)) {
    if (!env.BUCKET) return json({error:'Storage unavailable'},503);
    const {current}=await readCurrent(env);
    if (path.endsWith('status.json')) return json({mode:'live',data_updated_at:current?.descriptor.updated_at || BOOTSTRAP.updated_at,synced_at:current?.synced_at || null,last_heartbeat_at:current?.last_heartbeat_at || null,source_state:!current ? 'awaiting' : Date.now()-Date.parse(current.last_heartbeat_at)<120000 ? 'online' : 'offline',version:current?.descriptor.version || BOOTSTRAP.version});
    const descriptor=current?.descriptor || BOOTSTRAP;
    const etag='"'+descriptor.version+'"';
    if (request.headers.get('If-None-Match')===etag) return new Response(null,{status:304,headers:{ETag:etag,'Cache-Control':'no-store'}});
    return json(descriptor,200,{ETag:etag});
  }
  if (/^\/public\/snapshots\/[a-f0-9]{64}\.json$/.test(path)) {
    const object=await env.BUCKET?.get(path.slice('/public/'.length));
    if (object) return new Response(request.method==='HEAD' ? null : object.body,{headers:{'Content-Type':'application/json; charset=utf-8','Cache-Control':'public, max-age=31536000, immutable'}});
  }
  const asset=ASSETS[path==='/' ? '/index.html' : path];
  if (!asset) return json({error:'Not found'},404);
  const body=Uint8Array.from(atob(asset.body),c=>c.charCodeAt(0));
  return new Response(request.method==='HEAD' ? null : body,{headers:{'Content-Type':asset.type,...(asset.type === 'application/pdf' ? {'Content-Disposition':'attachment; filename="'+path.split('/').pop()+'"','Content-Length':String(body.byteLength)} : {}),'Cache-Control':'no-cache','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"}});
}
export default {async fetch(request,env) {try{return await handle(request,env);}catch{return json({error:'Service temporarily unavailable'},503);}}};
