"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { scorePoint, tally } from "../lib/scoring";
import type { Score } from "../lib/scoring";
type ScoreState = { version: number; score: Score; updated_at: string | null; sync_pending:boolean; sync_error:string|null };
export default function Scoreboard({ match, court, api, onClose, onFinish }: {
    match: { id:string; code:string; division:string; side_a:string; side_b:string };
    court: string;
    api: <T>(path:string, body?:unknown)=>Promise<T>;
    onClose: ()=>void;
    onFinish: (version:number)=>Promise<void>;
}) {
    const dialog = useRef<HTMLDialogElement>(null);
    const operation = useRef({active:false, epoch:0});
    const [data,setData] = useState<ScoreState|null>(null), [error,setError] = useState(""), [busy,setBusy] = useState(false), [confirm,setConfirm] = useState<number|null>(null);
    const [needsRefresh,setNeedsRefresh] = useState(false);
    const refresh = useCallback(async()=>{
        const next = await api<ScoreState>("score?match_id="+encodeURIComponent(match.id));
        setData(next);
        setNeedsRefresh(false);
        return next;
    },[api,match.id]);
    useEffect(()=>{ dialog.current?.showModal(); },[]);
    useEffect(()=>{
        let mounted=true, loading=false;
        const load=async()=>{
            if(operation.current.active || loading) return;
            loading=true;
            const epoch=operation.current.epoch;
            try {
                const next=await api<ScoreState>("score?match_id="+encodeURIComponent(match.id));
                if(!mounted || operation.current.active || epoch!==operation.current.epoch) return;
                setData(prior=>!prior||next.version>=prior.version?next:prior);
                setNeedsRefresh(false);
                if(next.sync_pending) void api("score-sync",{match_id:match.id}).catch(()=>{});
            } catch(e) {
                if(mounted && !operation.current.active && epoch===operation.current.epoch) setError(previous=>previous||(e as Error).message);
            } finally { loading=false; }
        };
        void load();
        const timer=setInterval(load,2000);
        return()=>{mounted=false;clearInterval(timer);};
    },[api,match.id]);
    async function point(action:"a"|"b"|"undo") {
        if(!data||operation.current.active||needsRefresh)return;
        let optimistic:Score;
        try { optimistic=scorePoint(data.score,action); }
        catch(e) { setError((e as Error).message);return; }
        const previous=data;
        operation.current={active:true,epoch:operation.current.epoch+1};
        setBusy(true);setError("");setConfirm(null);
        setData({...previous,score:optimistic,sync_pending:true,sync_error:null});
        try {
            setData(await api<ScoreState>("score",{match_id:match.id,version:previous.version,action}));
        } catch(e) {
            setData(previous);
            setNeedsRefresh(true);
            setError((e as Error).message);
            try { await refresh(); }
            catch { setError("暫時無法確認得分是否已儲存。請重新讀取比分，核對後再繼續。"); }
        } finally { operation.current.active=false;setBusy(false); }
    }
    async function recover() {
        if(operation.current.active)return;
        operation.current={active:true,epoch:operation.current.epoch+1};
        setBusy(true);
        try { await refresh();setError(""); }
        catch(e) { setError((e as Error).message); }
        finally { operation.current.active=false;setBusy(false); }
    }
    const state=data?tally(data.score):null;
    return <dialog ref={dialog} className="scoreboard" aria-labelledby="scoreboard-title" onCancel={e=>{e.preventDefault();if(!busy)onClose();}}>
        <div className="scoreboard-header"><p className="eyebrow">{court} · 第 {match.code} 場</p><h2 id="scoreboard-title">比賽計分</h2><p>{match.division}</p><button disabled={busy} onClick={onClose}>返回場地列表</button></div>
        {error&&<p className="notice error" role="alert">{error}</p>}
        {needsRefresh&&<button disabled={busy} onClick={recover}>重新讀取比分</button>}
        {!data||!state?<p role="status">載入計分紀錄中…</p>:<>
            <p className="score-rules">{data.score.rules.best_of===3?"三局兩勝":"五局三勝"} · 每局 {data.score.rules.target} 分 · 領先 2 分勝出</p>
            <p className="score-stage" role="status">{state.winner?"勝負已定，請確認成績":`第 ${state.games.length+1} 局`}<strong>局數 {state.wins.a}：{state.wins.b}</strong></p>
            <div className="score-players">{(["a","b"] as const).map(side=><div className="score-player" key={side}>
                <h3>{side==="a"?match.side_a:match.side_b}</h3>
                <strong className="score-number">{state.winner?state.games.at(-1)![side==="a"?"side_a":"side_b"]:state.current[side==="a"?"side_a":"side_b"]}</strong>
                <button className="primary score-add" disabled={busy||needsRefresh||!!state.winner} onClick={()=>point(side)} aria-label={`${side==="a"?match.side_a:match.side_b} 加一分`}>＋1 分</button>
            </div>)}</div>
            <button disabled={busy||needsRefresh||!data.score.points.length} onClick={()=>point("undo")}>↶ 復原上一分</button>
            {!!state.games.length&&<ol className="score-games">{state.games.map((game,index)=><li key={index}>第 {index+1} 局 <strong>{game.side_a}：{game.side_b}</strong></li>)}</ol>}
            <p className="score-saved" role="status">{needsRefresh?"等待核對最新比分":busy?"儲存中，請稍候…":data.updated_at?`已儲存 · ${new Date(data.updated_at).toLocaleTimeString("zh-TW",{hour12:false})}`:"每次加分都會儲存到後台。"}</p>
            {!busy && !needsRefresh && data.sync_pending && <p className="notice" role="status">{data.sync_error ?? "比分已儲存，正在同步賽場看板…"}</p>}
            {!busy && !needsRefresh && !data.sync_pending && <p className="score-saved">已同步賽場看板・約 2 秒自動更新</p>}
            {state.winner&&<div className="score-finish"><h3>勝方：{state.winner==="a"?match.side_a:match.side_b}</h3><p>確認後結束比賽、釋出場地，並自動同步成績到參賽者網站。</p>{confirm===data.version?<><p>請再次核對雙方姓名與每局比分。</p><button disabled={busy||needsRefresh} onClick={()=>setConfirm(null)}>繼續核對</button><button className="primary" disabled={busy||needsRefresh} onClick={async()=>{if(operation.current.active)return;operation.current={active:true,epoch:operation.current.epoch+1};setBusy(true);setError("");try{await onFinish(data.version);}catch(e){setError((e as Error).message);setConfirm(null);await refresh().catch(()=>{});}finally{operation.current.active=false;setBusy(false);}}}>確認登錄並同步成績</button></>:<button className="primary" disabled={busy||needsRefresh} onClick={()=>setConfirm(data.version)}>登錄完賽成績 →</button>}</div>}
        </>}
    </dialog>;
}
