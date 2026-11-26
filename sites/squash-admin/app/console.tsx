"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import type { Tournament } from "../lib/schedule/schema";
import ResultControl from "./result-control";
import DispatchBoard from "./dispatch-board";
import BackupPanel from "./backup-panel";
import AdvancementPanel from "./advancement-panel";
import CoachRulesPanel from "./coach-rules-panel";
import EncouragementPanel from "./encouragement-panel";
import LocalHandoff from "./local-handoff";
import PublicationPreview from "./publication-preview";
import type { ReviewSnapshot } from "./publication-preview";
import type { PublicationReview } from "../lib/publication-diff";

type SiteSignIn={email:string;sign_out_url:string};
type Status={imported:boolean;can_import:boolean;can_switch_draft:boolean;csrf:string;sign_in:SiteSignIn};
type State={revision:string;state:Tournament;issues:{key:string;message:string}[];history:{id:string;actor:string;reason:string;created:number}[]};
type Control={revision:string;sequence:number;publication_review:PublicationReview};
function LocalCreationGuide(){
    return <section className="local-creation-guide" aria-labelledby="local-creation-title"><div><h2 id="local-creation-title">逐組建賽與整體排程</h2><p>在賽務電腦完成名單、賽制與籤位，再交接到這個後台。</p><ol><li>逐組輸入選手名單、選擇賽制並產生初步籤表。</li><li>調整各組籤位，完成後微調整體賽程順序。</li><li>匯出地端交接檔，在後台核對變更後發布。</li></ol><p className="local-creation-note">先在賽務電腦執行專案的 START-LOCAL 啟動檔，再用同一台電腦開啟下方介面。</p></div><a className="local-creation-link" href="http://127.0.0.1:8765/setup" target="_blank" rel="noreferrer">開啟地端逐組建賽</a></section>;
}
export default function Console({screen="live"}:{screen?:"live"|"schedule"}) {
    const [status,setStatus]=useState<Status|null>(null),[data,setData]=useState<State|null>(null),[blockedSignIn,setBlockedSignIn]=useState<SiteSignIn|null>(null);
    const [busy,setBusy]=useState(false),[message,setMessage]=useState(""),[success,setSuccess]=useState(false),[handoffOpen,setHandoffOpen]=useState(false);
    const [publication,setPublication]=useState<ReviewSnapshot|null>(null),[publicationStale,setPublicationStale]=useState(false),[switchFile,setSwitchFile]=useState<File|null>(null);
    const api=useCallback(async<T=unknown,>(path:string,body?:unknown):Promise<T>=>{
        const response=await fetch("/api/"+path,{method:body===undefined?"GET":"POST",credentials:"same-origin",headers:body===undefined?{}:{"Content-Type":"application/json","X-CSRF-Token":status?.csrf??""},body:body===undefined?undefined:JSON.stringify(body)});
        const value=await response.json() as {error?:string;sign_in?:SiteSignIn};
        if(!response.ok){
            if(path==="status"&&response.status===403&&value.sign_in){setBlockedSignIn(value.sign_in);setStatus(null);}
            if(response.status===401){setPublication(null);setHandoffOpen(false);setStatus(null);}
            throw new Error(value.error??"目前無法完成操作。");
        }
        return value as T;
    },[status?.csrf]);
    const load=useCallback(async()=>{const next=await api<Status>("status");setBlockedSignIn(null);setStatus(next);if(next.imported)setData(await api<State>("state"));else setData(null);},[api]);
    useEffect(()=>{void Promise.resolve().then(load).catch(error=>setMessage(error instanceof Error?error.message:"無法讀取賽程。"));},[load]);
    async function run(action:()=>Promise<void>){setBusy(true);setMessage("");setSuccess(false);try{await action();}catch(error){setMessage(error instanceof Error?error.message:"操作未完成。");}finally{setBusy(false);}}
    async function previewPublication(){const current=await api<Control>("control");setPublication({...current.publication_review,revision:current.revision,sequence:current.sequence});setPublicationStale(false);}
    async function publish(){if(!publication)return;const current=await api<Control>("control");if(current.revision!==publication.revision||current.sequence!==publication.sequence){setPublicationStale(true);throw new Error("賽程或成績已更新，請重新載入發布內容。");}await api("publish",{revision:publication.revision,sequence:publication.sequence});setPublication(null);await load();setSuccess(true);setMessage("預定賽程已保存，公開頁面同步中。");}
    const title=screen==="live"?"成績登錄":"賽程安排";
    const notice=message?<p role={success?"status":"alert"} className={"notice "+(success?"success":"error")}>{message}</p>:null;
    const header=<><header className="app-header compact-header"><Link className="wordmark" href="/">PK<span>賽務管理台</span></Link><div className="header-right"><span className="private-badge">私人管理後台</span>{status&&<><span className="account">{status.sign_in.email}</span><button disabled={busy} onClick={()=>void run(async()=>{await api("logout",{});window.open(status.sign_in.sign_out_url,"_top");})}>登出</button></>}</div></header><nav className="admin-navigation" aria-label="管理功能"><Link href="/schedule" aria-current={screen==="schedule"?"page":undefined}><strong>賽程安排</strong></Link><Link href="/live" aria-current={screen==="live"?"page":undefined}><strong>成績登錄</strong></Link></nav></>;
    if(blockedSignIn)return <>{header}<main id="main-content" tabIndex={-1} className="onboarding"><h1>此帳號沒有管理權限</h1><p>目前登入身分為 <strong>{blockedSignIn.email}</strong>。<a href={blockedSignIn.sign_out_url} target="_top">重新登入</a></p><p>請改用已獲授權的 ChatGPT 帳號登入管理台。</p></main></>;
    if(!status)return <>{header}<main id="main-content" tabIndex={-1} className="workspace coach-workspace" aria-busy="true"><section className="heading"><div><h1>{title}</h1><p role="status">正在讀取賽事資料…</p></div><button disabled={!message||busy} onClick={()=>void run(load)}>重新整理</button></section>{notice}<section className="live-control coach-loading"><h2>{screen==="live"?"全部比賽場次":"現場場地安排"}</h2><p>正在確認登入與最新版本…</p>{message&&<a href="/temporary-login" target="_top">重新登入</a>}</section></main></>;
    if(!status.imported)return <>{header}<main id="main-content" tabIndex={-1} className="onboarding"><h1>建立與匯入賽事</h1><p>名單、賽制、籤位與初步賽程由地端「逐組建賽」完成，再將已核對的賽事送到這個後台。</p>{notice}{status.can_import&&<LocalCreationGuide/>}{status.can_import?<section className="import-panel"><h2>匯入地端初版賽程</h2><label>選擇賽程 JSON<input type="file" accept=".json,application/json" disabled={busy} onChange={event=>{const file=event.target.files?.[0];if(!file)return;void run(async()=>{if(file.size>1024*1024)throw new Error("檔案不得超過 1 MB。");let payload:unknown;try{payload=JSON.parse(await file.text());}catch{throw new Error("請選擇有效的賽程 JSON。");}await api("import",payload);await load();});}}/></label></section>:<p>請等待主管理員匯入賽程。</p>}{status.can_import&&<BackupPanel api={api} onChanged={load} disabled={busy}/>}</main></>;
    if(!data)return <>{header}<main id="main-content" tabIndex={-1} className="workspace coach-workspace"><h1>{title}</h1>{notice}<p role="status">正在讀取賽程…</p><button disabled={busy} onClick={()=>void run(load)}>重新載入</button></main></>;
    const state=data.state;
    return <>{header}<main id="main-content" tabIndex={-1} className="workspace coach-workspace"><section className="heading"><div><h1>{title}</h1><p>{state.name}</p><p>{screen==="live"?"直接填寫各局比分，登錄後自動移到已完成列表。":"選擇場地加入待賽順序；原預定時間繼續作為進度參考。"}</p></div><div className="heading-actions"><button disabled={busy} onClick={()=>void run(load)}>重新整理</button>{screen==="schedule"&&<button disabled={busy} onClick={()=>void run(previewPublication)}>發布預定賽程</button>}</div></section>{notice}
        {screen==="schedule"&&status.can_import&&<LocalCreationGuide/>}
        {status.can_import&&<EncouragementPanel state={state} revision={data.revision} api={api} onChanged={load} onPublish={previewPublication}/>}
        {screen==="live"?<ResultControl canCorrect={status.can_import} state={state} revision={data.revision} api={api} onChanged={load}/>:<DispatchBoard state={state} revision={data.revision} api={api} onChanged={load}/>}
        <details className="coach-tools"><summary>賽事工具與資料交接</summary><p>逐組名單、賽制及籤位調整請在地端「逐組建賽」操作，確認後核對交接。現場派場不會改動預定時間。</p>
            {status.can_import&&<><button disabled={busy} onClick={()=>setHandoffOpen(true)}>地端交接</button><CoachRulesPanel state={state} revision={data.revision} api={api} onChanged={load}/><AdvancementPanel state={state} revision={data.revision} api={api} onChanged={load}/><BackupPanel api={api} onChanged={load} disabled={busy}/></>}
            {status.can_switch_draft&&<section className="import-panel"><h2>切換既有暫定賽程</h2><label>選擇公開站相同的賽程 JSON<input type="file" accept=".json,application/json" disabled={busy} onChange={event=>setSwitchFile(event.target.files?.[0]??null)}/></label><button disabled={busy||!switchFile} onClick={()=>void run(async()=>{if(!switchFile)return;if(switchFile.size>512*1024)throw new Error("檔案不得超過 512 KB。");await api("switch-draft",{base_revision:data.revision,payload:await switchFile.text()});setSwitchFile(null);await load();setSuccess(true);setMessage("暫定賽程已切換，請核對並發布。");})}>確認切換</button></section>}
            <details className="coach-history"><summary>最近操作紀錄</summary>{data.history.map(item=><p key={item.id}>{new Date(item.created).toLocaleString("zh-TW",{timeZone:"Asia/Taipei"})} · {item.actor} · {item.reason}</p>)}</details>
        </details>
        <footer className="revision">版本 {data.revision.slice(0,8)} · Asia/Taipei</footer>
    </main>
    {handoffOpen&&status.can_import&&<LocalHandoff state={state} api={api} onClose={()=>setHandoffOpen(false)} onChanged={load} onSaved={()=>{setSuccess(true);setMessage("地端交接已儲存，請核對並發布預定賽程。");}}/>}
    {publication&&<PublicationPreview review={publication} stale={publicationStale||publication.revision!==data.revision} busy={busy} draftCount={0} error={!success?message:""} onClose={()=>setPublication(null)} onReload={()=>void run(previewPublication)} onPublish={()=>void run(publish)}/>}
    </>;
}
