// Local UI preview only. Production always uses the Sites-provisioned R2 binding.
import http from 'node:http';
import {readFile} from 'node:fs/promises';
const code=await readFile(new URL('../dist/server/index.js',import.meta.url),'utf8');
const {default:worker}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
const objects=new Map();let sequence=0;
const bucket={
  async get(key){const item=objects.get(key);return item ? {...item,json:async()=>JSON.parse(item.raw),body:new Response(item.raw).body} : null;},
  async head(key){return this.get(key);},
  async put(key,raw,options={}){const old=objects.get(key);if(options.onlyIf?.etagDoesNotMatch==='*'&&old||options.onlyIf?.etagMatches&&old?.etag!==options.onlyIf.etagMatches)return null;const item={raw,etag:String(++sequence),customMetadata:options.customMetadata};objects.set(key,item);return item;}
};
const env={BUCKET:bucket,SYNC_SECRET:'local-preview-only',CLOUD_PUBLISH_SECRET:'local-cloud-preview-only',PUBLISH_SOURCE_ID:'local-cloud-source'};
const port=Number(process.env.PORT||8780);
const descriptor=JSON.parse(await readFile(new URL('../dist/public/version.json',import.meta.url)));
objects.set('sync/current.json',{etag:'seed',raw:JSON.stringify({descriptor:{...descriptor,snapshot_url:'/public/'+descriptor.snapshot_url},synced_at:new Date().toISOString(),last_heartbeat_at:process.argv.includes('--offline') ? '2020-01-01T00:00:00Z' : new Date().toISOString()})});
if(process.env.PREVIEW_SNAPSHOT){const snapshot=JSON.parse(await readFile(process.env.PREVIEW_SNAPSHOT,'utf8'));const key='snapshots/'+snapshot.version+'.json';objects.set(key,{etag:'fixture',raw:JSON.stringify(snapshot)});objects.set('sync/current.json',{etag:'fixture-head',raw:JSON.stringify({descriptor:{...descriptor,version:snapshot.version,snapshot_url:'/public/'+key},synced_at:new Date().toISOString(),last_heartbeat_at:new Date().toISOString()})});}
http.createServer(async(req,res)=>{
  const chunks=[];for await(const chunk of req)chunks.push(chunk);
  const response=await worker.fetch(new Request('http://127.0.0.1:'+port+req.url,{method:req.method,headers:req.headers,...(!['GET','HEAD'].includes(req.method)?{body:Buffer.concat(chunks)}:{})}),env);
  res.writeHead(response.status,Object.fromEntries(response.headers));res.end(Buffer.from(await response.arrayBuffer()));
}).listen(port,'127.0.0.1',()=>console.log('Local preview: http://127.0.0.1:'+port+'/'));
