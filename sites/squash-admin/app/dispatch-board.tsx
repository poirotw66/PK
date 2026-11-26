"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Tournament } from "../lib/schedule/schema";
import type { QuickMatch } from "./quick-result-row";

type CourtQueue = {id:string;name:string;matches:QuickMatch[];blocked_reason?:string|null};
type Control = {revision:string;sequence:number;dispatch:{courts:CourtQueue[];unassigned:QuickMatch[];completed:QuickMatch[]}};
const reference = (value:string|null) => value ? new Date(value).toLocaleString("zh-TW",{timeZone:"Asia/Taipei",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false}) : "尚無參考時間";
const courtButton = (court:CourtQueue) => court.name.replace(/\s*場地\s*$/u,"");
export default function DispatchBoard({ state, revision, api, onChanged }: {
    state:Tournament;revision:string;api:<T>(path:string,body?:unknown)=>Promise<T>;onChanged:()=>Promise<void>;
}) {
    const [data,setData]=useState<Control|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(""),[message,setMessage]=useState("");
    const [now,setNow]=useState<Date|null>(null),[refreshError,setRefreshError]=useState("");
    const lock=useRef(false),request=useRef(0),inFlight=useRef<Promise<void>|null>(null);
    const invalidate=useCallback(()=>{request.current++;},[]);
    const refresh=useCallback(()=>{
        if(inFlight.current)return inFlight.current;
        const stamp=++request.current;
        const reading=api<Control>("control").then(next=>{
            if(stamp===request.current){setData(next);setRefreshError("");}
        }).catch(error=>{
            if(stamp===request.current)setRefreshError(error instanceof Error?error.message:"暫時無法更新場地安排。");
            throw error;
        }).finally(()=>{if(inFlight.current===reading)inFlight.current=null;});
        inFlight.current=reading;
        return reading;
    },[api]);
    useEffect(()=>{
        let active=true;
        void (async()=>{await inFlight.current?.catch(()=>{});if(active&&!lock.current)await refresh().catch(()=>{});})();
        const poll=()=>{if(document.visibilityState==="visible"&&!lock.current)void refresh().catch(()=>{});};
        const timer=setInterval(poll,15000);
        document.addEventListener("visibilitychange",poll);window.addEventListener("focus",poll);
        return()=>{active=false;clearInterval(timer);document.removeEventListener("visibilitychange",poll);window.removeEventListener("focus",poll);invalidate();};
    },[refresh,revision,invalidate]);
    useEffect(()=>{const first=setTimeout(()=>setNow(new Date()),0),timer=setInterval(()=>setNow(new Date()),1000);return()=>{clearTimeout(first);clearInterval(timer);};},[]);
    async function move(match:QuickMatch,courtId:string|null,position?:number) {
        if(!data||lock.current)return;
        lock.current=true;setBusy(true);setError("");setMessage("");request.current++;
        try {
            const saved=await api<Control|{ok:boolean;revision:string}>("dispatch",{revision:data.revision,sequence:data.sequence,match_id:match.id,court_id:courtId,...(position===undefined?{}:{position})});
            if("dispatch" in saved){setData(saved);setRefreshError("");}else {await inFlight.current?.catch(()=>{});await refresh();}
            setMessage(`第 ${match.code} 場${courtId?"場地順序已更新":"已移回待安排場次"}。`);
            try{await onChanged();}catch{setMessage(`第 ${match.code} 場已儲存，暫時無法取得最新資料，請重新整理。`);}
        } catch(error){setError(error instanceof Error?error.message:"安排未完成。");await inFlight.current?.catch(()=>{});await refresh().catch(()=>{});}
        finally{lock.current=false;setBusy(false);}
    }
    const courts:CourtQueue[]=data?.dispatch.courts??state.courts.map(court=>({id:court.id,name:court.name,matches:[]}));
    const unassigned=data?.dispatch.unassigned??[];
    return <section className="dispatch-workspace" aria-labelledby="dispatch-title">
        <div className="dispatch-intro"><h2 id="dispatch-title">現場場地安排</h2><p>從場次清單選擇場地，依序加入待賽名單。每場地最多安排接下來三場，參考時間保持原排程。頁面開啟時每 15 秒核對最新完賽與候場順序。</p></div>
        {refreshError&&<p role="status" className="notice error">場地列表暫未更新，仍顯示上次資料。{refreshError} <button type="button" disabled={busy} onClick={()=>void refresh().catch(()=>{})}>重試更新場地</button></p>}
        {error&&<p role="alert" className="notice error">{error}</p>}{message&&<p role="status" className="notice success">{message}</p>}
        <div className="dispatch-courts">{courts.map(court=><section className="dispatch-court" key={court.id} aria-labelledby={`dispatch-${court.id}`}><div className="dispatch-court-heading"><h3 id={`dispatch-${court.id}`}>{court.name}</h3><span>{court.matches.length}／3 場</span></div>
            {court.blocked_reason&&<p className="dispatch-blocked" role="status">{court.blocked_reason}</p>}
            {!court.matches.length&&<p className="dispatch-empty">尚未安排場次</p>}
            <ol>{court.matches.map((match,index)=><li key={match.id} className={index===0?"dispatch-current":""}>
                {index===0&&<p className="dispatch-now"><strong>{court.blocked_reason?"等待調整":"目前場上"}</strong><time>{now?now.toLocaleTimeString("zh-TW",{timeZone:"Asia/Taipei",hour:"2-digit",minute:"2-digit",hour12:false}):"現在時間"}</time></p>}
                <p className="dispatch-meta">第 {match.code} 場 · {match.division}</p><h4>{match.side_a}<span>對</span>{match.side_b}</h4><p className="dispatch-reference">參考 {reference(match.planned_start)}</p>
                <div className="dispatch-card-actions"><button type="button" disabled={busy||index===0} onClick={()=>void move(match,court.id,index-1)}>往前</button><button type="button" disabled={busy||index===court.matches.length-1} onClick={()=>void move(match,court.id,index+1)}>往後</button><button type="button" disabled={busy} onClick={()=>void move(match,null)}>移回清單</button></div>
                <div className="dispatch-transfer" role="group" aria-label={`更正第 ${match.code} 場場地`}>{courts.filter(other=>other.id!==court.id).map(other=><button type="button" key={other.id} disabled={busy||other.matches.length>=3} onClick={()=>void move(match,other.id)}>改至 {courtButton(other)}</button>)}</div>
            </li>)}</ol>
        </section>)}</div>
        <section className="dispatch-unassigned" aria-labelledby="dispatch-unassigned-title"><div className="dispatch-list-heading"><h2 id="dispatch-unassigned-title">場次清單</h2><p>{unassigned.length} 場待安排</p></div>{!data&&<p role="status">正在讀取最新場次…</p>}{data&&!unassigned.length&&<p>目前沒有待安排的場次。</p>}
            {unassigned.map(match=><article className="dispatch-match" key={match.id}><div><p className="dispatch-meta">第 {match.code} 場 · {match.division}</p><h3>{match.side_a}<span>對</span>{match.side_b}</h3><p className="dispatch-reference">參考 {reference(match.planned_start)}</p>{match.blocked_reason&&<p className="dispatch-blocked">{match.blocked_reason}</p>}</div>
                <div className="dispatch-assign" role="group" aria-label={`第 ${match.code} 場選擇場地`}>{courts.map(court=><button key={court.id} type="button" disabled={busy||match.eligible===false||court.matches.length>=3} title={court.matches.length>=3?"此場地已安排三場":match.blocked_reason??undefined} onClick={()=>void move(match,court.id)}>{courtButton(court)}</button>)}</div>
            </article>)}
        </section>
        {!!data?.dispatch.completed.length&&<details className="dispatch-finished"><summary>已完成 {data.dispatch.completed.length} 場</summary>{data.dispatch.completed.map(match=><p key={match.id}>第 {match.code} 場 · {match.side_a} 對 {match.side_b}</p>)}</details>}
    </section>;
}
