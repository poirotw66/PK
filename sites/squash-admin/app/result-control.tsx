"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Tournament } from "../lib/schedule/schema";
import type { ScoringResult } from "../lib/scoring";
import QuickResultRow from "./quick-result-row";
import type { QuickMatch } from "./quick-result-row";

type Control = {
    revision: string; sequence: number; pending: boolean; error: string | null;
    dispatch?: { courts: {id:string;name:string;matches:QuickMatch[]}[]; unassigned:QuickMatch[]; completed:QuickMatch[] };
};
export default function ResultControl({ state, revision, canCorrect = false, api, onChanged }: {
    state: Tournament; revision: string; canCorrect?: boolean;
    api:<T>(path:string,body?:unknown)=>Promise<T>; onChanged:()=>Promise<void>;
}) {
    const [data,setData]=useState<Control|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(""),[message,setMessage]=useState("");
    const lock=useRef(false),mounted=useRef(true),request=useRef(0);
    const invalidate=useCallback(()=>{request.current++;},[]);
    const refresh=useCallback(async()=>{const stamp=++request.current;const next=await api<Control>("control");if(mounted.current&&stamp===request.current)setData(next);},[api]);
    useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;invalidate();};},[invalidate]);
    useEffect(()=>{void refresh().catch(error=>setError(error instanceof Error?error.message:"無法讀取場次。"));},[refresh,revision]);
    const fallback:QuickMatch[]=state.matches.map(match=>({...match,planned_start:match.assignment?new Date(match.assignment.start*60000).toISOString():null,eligible:match.players.length===2,blocked_reason:match.players.length===2?null:"雙方選手尚未確認"}));
    const fromControl=data?.dispatch?[...data.dispatch.courts.flatMap(court=>court.matches),...data.dispatch.unassigned,...data.dispatch.completed]:fallback;
    const all=[...new Map(fromControl.map(match=>[match.id,match])).values()].sort((a,b)=>a.code.localeCompare(b.code,undefined,{numeric:true}));
    const pending=all.filter(match=>!match.result),completed=all.filter(match=>!!match.result);
    async function save(match:QuickMatch,result:ScoringResult,expected:ScoringResult|null) {
        if(lock.current||!data)throw new Error("另一場正在儲存，請稍後再按登錄。");
        lock.current=true;request.current++;setBusy(true);setError("");setMessage("");
        try {
            const next=await api<Control>("quick-result",{revision:data.revision,sequence:data.sequence,match_id:match.id,result,expected_result:expected});
            setData(next);setMessage(`第 ${match.code} 場已儲存。`);
            try {await onChanged();}catch{setMessage(`第 ${match.code} 場已儲存，暫時無法重新載入；不必重複登錄。`);}
        } catch(error) {
            await refresh().catch(()=>{});
            throw error;
        } finally {lock.current=false;setBusy(false);}
    }
    return <section className="quick-results" aria-labelledby="quick-results-title">
        <div className="quick-results-heading"><h2 id="quick-results-title">全部比賽場次</h2><p role="status">待登錄 {pending.length} 場 · 已登錄 {completed.length} 場</p></div>
        {error&&<p role="alert" className="notice error">{error}</p>}{message&&<p role="status" className="notice success">{message}</p>}
        {data?.pending&&<p role="status" className="quick-sync-note">成績已保存，公開頁面正在等待同步。<button disabled={busy} onClick={async()=>{setError("");try{await api("sync-retry",{});await refresh();}catch(error){setError(error instanceof Error?error.message:"同步未完成。");}}}>重試同步</button></p>}
        {data?.error&&<p role="status" className="notice error">{data.error}</p>}
        <div className="quick-results-columns"><section aria-labelledby="pending-result-title"><h3 id="pending-result-title">待登錄</h3>{!data&&<p role="status">正在讀取最新場次…</p>}{!pending.length&&<p>全部場次已登錄。</p>}{pending.map(match=><QuickResultRow key={match.id} match={match} busy={busy||!data} canCorrect={canCorrect} onSave={save}/>)}</section>
        <section aria-labelledby="completed-result-title"><h3 id="completed-result-title">已登錄</h3>{!completed.length&&<p>尚無已登錄成績。</p>}{completed.map(match=><QuickResultRow key={match.id} match={match} busy={busy||!data} canCorrect={canCorrect} onSave={save}/>)}</section></div>
    </section>;
}
