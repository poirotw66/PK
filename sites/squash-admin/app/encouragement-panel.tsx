"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Tournament } from "../lib/schedule/schema";

type Eligible = {entry_id:string;player_id:string;name:string;source_match_id:string;source_code:string};
type Draw = {seed:number;entry_ids:string[];bracket_entry_ids:(string|null)[];match_ids:string[]};
type Division = {
    division_id:string;division:string;status:"waiting"|"ready"|"drawn"|"blocked"|"insufficient";
    total:number;completed:number;pending:{id:string;code:string}[];eligible:Eligible[];
    excluded:{code:string;reason:string}[];blockers:string[];draw?:Draw;
};
type Qualification = {revision:string;sequence:number;divisions:Division[];unsupported:string[]};
type Change = {id:string;code:string;division:string;side_a:string;side_b:string;start_at:string|null;court:string|null};
type Plan = {
    id:string|null;can_apply:boolean;division:string;draw:Draw;changes:Change[];
    issues:{code:string;message:string;matches:string[]}[];qualification:Division;
};
type Preview = {plan:Plan;revision:string;sequence:number;division_id:string};
const reasonText:Record<string,string> = {
    FIRST_ROUND_WINNER:"首輪勝者",FIRST_ROUND_BYE:"首輪輪空",NO_FIRST_ROUND_APPEARANCE:"未參加籤表首輪實際對戰",
    UNPLAYED_FIRST_ROUND:"未賽棄權或雙方棄權",RETIREMENT_REQUIRES_REVIEW:"中途退賽，須人工核對",
};
const formatTime=(value:string|null)=>value?new Date(value).toLocaleString("zh-TW",{timeZone:"Asia/Taipei",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false}):"尚無可用時間";
function statusText(division:Division) {
    if(division.status==="drawn")return division.draw?.match_ids.length?"籤表已建立":"已核定不成賽";
    return {waiting:"等待首輪成績",ready:"可以抽籤",blocked:"需要核對",insufficient:"不足兩人"}[division.status];
}

export default function EncouragementPanel({state,revision,api,onChanged,onPublish}:{
    state:Tournament;revision:string;api:<T>(path:string,body?:unknown)=>Promise<T>;
    onChanged:()=>Promise<void>;onPublish:()=>Promise<void>;
}) {
    const [data,setData]=useState<Qualification|null>(null),[preview,setPreview]=useState<Preview|null>(null);
    const [busy,setBusy]=useState(false),[checking,setChecking]=useState(false),[error,setError]=useState(""),[message,setMessage]=useState("");
    const [announcement,setAnnouncement]=useState("");
    const [savedDivision,setSavedDivision]=useState("");
    const requests=useRef(0),lock=useRef(false),mounted=useRef(true);
    const refresh=useCallback(async()=>{
        const stamp=++requests.current;
        setChecking(true);
        try {
            const next=await api<Qualification>("encouragement-status");
            if(!mounted.current||stamp!==requests.current)return null;
            const ready=next.divisions.filter(division=>division.status==="ready");
            setAnnouncement(ready.length?`${ready.map(division=>division.division).join("、")}首輪成績已齊全，可以產生鼓勵賽籤表。`:"");
            setData(next);return next;
        } finally {if(mounted.current&&stamp===requests.current)setChecking(false);}
    },[api]);
    useEffect(()=>{
        mounted.current=true;
        return()=>{mounted.current=false;};
    },[]);
    useEffect(()=>{void Promise.resolve().then(refresh).catch(error=>{if(mounted.current)setError(error instanceof Error?error.message:"無法核對鼓勵賽資格。");});},[refresh,revision]);
    useEffect(()=>{
        const timer=setInterval(()=>{
            if(lock.current||preview||document.hidden)return;
            void refresh().catch(()=>{});
        },30000);
        return()=>clearInterval(timer);
    },[refresh,preview]);
    async function generate(divisionId:string) {
        if(lock.current)return;
        lock.current=true;setBusy(true);setError("");setMessage("");
        try {
            const latest=await refresh();
            if(!latest)return;
            const plan=await api<Plan>("encouragement-preview",{revision:latest.revision,sequence:latest.sequence,division_id:divisionId});
            if(mounted.current)setPreview({plan,revision:latest.revision,sequence:latest.sequence,division_id:divisionId});
        } catch(error){if(mounted.current)setError(error instanceof Error?error.message:"無法產生鼓勵賽預覽。");}
        finally{lock.current=false;if(mounted.current)setBusy(false);}
    }
    async function confirm() {
        if(!preview?.plan.id||lock.current)return;
        lock.current=true;setBusy(true);setError("");setMessage("");requests.current++;
        try {
            await api("encouragement-confirm",{id:preview.plan.id});
            const division=preview.plan.division;
            setPreview(null);setSavedDivision(division);setMessage(`${division}鼓勵賽已儲存，請預覽發布內容。`);
            try{await onChanged();await refresh();}catch{setMessage(`${division}鼓勵賽已儲存，暫時無法取得最新資料；請重新核對，不必再次抽籤。`);}
        } catch(error){setError(error instanceof Error?error.message:"無法儲存鼓勵賽。");await refresh().catch(()=>{});}
        finally{lock.current=false;if(mounted.current)setBusy(false);}
    }
    async function publish() {
        if(lock.current)return;
        lock.current=true;setBusy(true);setError("");
        try{await onPublish();}catch(error){setError(error instanceof Error?error.message:"無法取得發布內容。");}
        finally{lock.current=false;if(mounted.current)setBusy(false);}
    }
    const ready=data?.divisions.filter(division=>division.status==="ready")??[];
    const stale=!!preview&&!!data&&(preview.revision!==data.revision||preview.sequence!==data.sequence);
    const selected=preview?.plan;
    const names=new Map(selected?.qualification.eligible.map(entry=>[entry.entry_id,entry.name])??[]);
    const courtName=(court:string|null)=>state.courts.find(value=>value.id===court)?.name??court??"尚無可用場地";
    return <section className="encouragement-panel" aria-labelledby="encouragement-title">
        <div className="encouragement-heading"><div><h2 id="encouragement-title">鼓勵賽資格檢查</h2><p>各組獨立核對；該組首輪成績齊全即可抽籤，不必等待其他組。</p></div><button disabled={busy||checking} onClick={()=>{setError("");void refresh().catch(error=>setError(error instanceof Error?error.message:"無法重新核對。"));}}>{checking?"核對中…":"重新核對資格"}</button></div>
        <p className="encouragement-announcement" role="status" aria-live="polite" aria-atomic="true">{announcement}</p>
        {error&&<p className="notice error" role="alert">{error}</p>}{message&&<p className="notice success" role="status">{message}</p>}
        {!data&&<p>正在核對各組首輪成績…</p>}
        {data&&!data.divisions.length&&<p>目前沒有可核對的直接淘汰組別。先循環後淘汰的組別不辦鼓勵賽。</p>}
        {!!data?.unsupported.length&&<details className="encouragement-eligibility"><summary>其他組別適用狀態</summary><p>未列入本次資格檢查：{data.unsupported.join("、")}。</p><p>先循環後淘汰的組別不辦鼓勵賽。若直接淘汰組別未顯示，請先補齊正式首輪籤表來源。</p></details>}
        {!!ready.length&&<p className="encouragement-ready-summary">{ready.length} 組可抽籤。抽籤後會預覽對戰與參考時間，確認儲存後再發布。</p>}
        {data&&<div className="encouragement-divisions">{data.divisions.map(division=><article key={division.division_id} className={division.status==="ready"?"encouragement-division is-ready":"encouragement-division"}>
            <div className="encouragement-division-heading"><h3>{division.division}</h3><strong className="encouragement-state">{statusText(division)}</strong></div>
            <p>首輪成績 {division.completed}／{division.total} 場 · 已確認合格敗者 {division.eligible.length} 人</p>
            {!!division.pending.length&&<p className="encouragement-pending">待登錄場次：{division.pending.map(match=>match.code).join("、")}。</p>}
            {!!division.blockers.length&&<ul className="encouragement-blockers">{division.blockers.map((text,index)=><li key={index}>{text}</li>)}</ul>}
            {division.status==="insufficient"&&<p>首輪已核對，合格敗者不足兩人，不會建立虛構對戰。</p>}
            {division.draw&&<p>{division.draw.match_ids.length?`已建立 ${division.draw.match_ids.length} 場，僅取鼓勵賽冠軍。`:"合格敗者不足兩人，沒有需要安排的鼓勵賽場次。"}</p>}
            <details className="encouragement-eligibility"><summary>查看資格名單與規則</summary><p>直接淘汰制首輪實際出賽敗者皆可參加，包含種子；首輪輪空、未賽棄權或雙方棄權不列入。中途退賽須核對。先循環後淘汰的組別不適用。</p>
                {!!division.eligible.length?<ul>{division.eligible.map(entry=><li key={entry.entry_id}>{entry.name}<span>第 {entry.source_code} 場敗者</span></li>)}</ul>:<p>尚無已確認的合格敗者。</p>}
                {!!division.excluded.length&&<ul>{division.excluded.map((entry,index)=><li key={`${entry.code}-${index}`}>第 {entry.code} 場：{reasonText[entry.reason]??entry.reason}</li>)}</ul>}
            </details>
            <div className="encouragement-actions">{(division.status==="ready"||division.status==="insufficient")&&<button className="primary" disabled={busy||checking||!!preview} onClick={()=>void generate(division.division_id)}>{division.status==="insufficient"?"核對不成賽處理":"產生籤表與安排預覽"}</button>}</div>
        </article>)}</div>}
        {(savedDivision||data?.divisions.some(division=>division.status==="drawn"))&&<div className="encouragement-actions"><button disabled={busy} onClick={()=>void publish()}>預覽發布內容</button></div>}
        {selected&&<section className="encouragement-preview" aria-labelledby="encouragement-preview-title"><div className="encouragement-preview-heading"><h3 id="encouragement-preview-title">{selected.division}鼓勵賽預覽</h3><p>抽籤種子 {selected.draw.seed} · {selected.changes.length} 場 · 三局兩勝 · 不打三四名</p></div>
            <p>既有成績及已開賽場次保留。以下僅為本組新增場次，尚未儲存或發布。</p>
            {!!selected.draw.bracket_entry_ids.length&&<ol className="encouragement-draw-slots" aria-label="抽籤籤位">{selected.draw.bracket_entry_ids.map((entry,index)=><li key={index}><span>籤位 {index+1}</span><strong>{entry?names.get(entry)??"選手待核對":"輪空"}</strong></li>)}</ol>}
            {selected.changes.length?<div className="encouragement-match-list">{selected.changes.map(change=><article key={change.id}><div><p>第 {change.code} 場</p><h4>{change.side_a}<span>對</span>{change.side_b}</h4></div><p><time>{formatTime(change.start_at)}</time><span>{courtName(change.court)}</span></p></article>)}</div>:<p>合格敗者不足兩人，不產生比賽或假成績；確認後留下本組核對紀錄。</p>}
            {!!selected.issues.length&&<div className="encouragement-plan-issues" role="alert"><h4>目前無法完成安排</h4><ul>{selected.issues.map((issue,index)=><li key={`${issue.code}-${index}`}>{issue.message}</li>)}</ul><p>請先調整可用時間或既有未開賽安排，再重新產生預覽。此預覽不會寫入賽程。</p></div>}
            {stale&&<p className="notice error" role="alert">成績、賽程或發布版本已更新，請重新產生預覽。</p>}
            <div className="encouragement-actions"><button disabled={busy} onClick={()=>setPreview(null)}>返回資格檢查</button><button disabled={busy} onClick={()=>void generate(preview!.division_id)}>重新產生預覽</button><button className="primary" disabled={busy||stale||!selected.can_apply||!selected.id} onClick={()=>void confirm()}>{busy?"儲存中…":selected.changes.length?"確認籤表與安排":"確認本組不成賽"}</button></div>
        </section>}
    </section>;
}
