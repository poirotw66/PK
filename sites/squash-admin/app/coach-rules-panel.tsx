"use client";
import { useState } from "react";
import type { Tournament } from "../lib/schedule/schema";

type Plan={id:string;can_apply:boolean;changes:{id:string;code:string;before:3|5;after:3|5}[]};
type RulesState=Tournament&{coach_profile?:{version:1;championship_ids:string[]}};
export default function CoachRulesPanel({state,revision,api,onChanged}:{state:Tournament;revision:string;api:<T>(path:string,body?:unknown)=>Promise<T>;onChanged:()=>Promise<void>}) {
    const configured=(state as RulesState).coach_profile?.championship_ids??state.matches.filter(match=>(match as typeof match&{championship?:boolean}).championship).map(match=>match.id);
    const [selected,setSelected]=useState<string[]|null>(null),[plan,setPlan]=useState<Plan|null>(null),[basis,setBasis]=useState(""),[busy,setBusy]=useState(false),[error,setError]=useState(""),[message,setMessage]=useState("");
    const ids=selected??configured,stale=!!plan&&basis!==revision;
    async function preview(){setBusy(true);setError("");setMessage("");try{const control=await api<{revision:string;sequence:number}>("control");if(control.revision!==revision)throw new Error("賽程或成績已更新，請先重新整理。");setPlan(await api<Plan>("coach-rules-preview",{revision,sequence:control.sequence,championship_ids:ids}));setBasis(revision);}catch(error){setError(error instanceof Error?error.message:"無法預覽賽制。");}finally{setBusy(false);}}
    async function confirm(){if(!plan)return;setBusy(true);setError("");try{await api("coach-rules-confirm",{id:plan.id});setPlan(null);setSelected(null);setMessage("賽制已儲存，請核對並發布預定賽程。");try{await onChanged();}catch{setMessage("賽制已儲存，暫時無法重新載入；不必重複套用。");}}catch(error){setError(error instanceof Error?error.message:"無法儲存賽制。");}finally{setBusy(false);}}
    return <details className="coach-rules-panel"><summary>設定冠軍賽與教練賽制</summary><p>選擇各組冠軍賽，採五局三勝；其餘未完賽場次採三局兩勝。已登錄成績的場次會保留原賽制。</p><p>舊版賽程需要先指定冠軍賽，才能顯示正確的五局輸入欄位。已有籤表標記的場次會保留設定。</p>
        {error&&<p role="alert" className="notice error">{error}</p>}{message&&<p role="status" className="notice success">{message}</p>}
        {!plan?<><label>冠軍賽場次（可複選）<select multiple size={Math.min(10,Math.max(4,state.matches.length))} value={ids} disabled={busy} onChange={event=>setSelected(Array.from(event.target.selectedOptions,option=>option.value))}>{state.matches.map(match=><option key={match.id} value={match.id} disabled={match.championship!==undefined}>第 {match.code} 場 · {match.division} · {match.side_a} 對 {match.side_b}{match.result?"（已登錄）":""}</option>)}</select></label><p>已選 {ids.length} 場。電腦可按住 Ctrl 或 Command 複選。</p><button disabled={busy} onClick={()=>void preview()}>{busy?"核對中…":"預覽賽制變更"}</button></>:<><h3>賽制變更預覽</h3>{plan.changes.length?<ul>{plan.changes.map(change=><li key={change.id}>第 {change.code} 場：{change.before===5?"五局三勝":"三局兩勝"} → {change.after===5?"五局三勝":"三局兩勝"}</li>)}</ul>:<p>沒有需要更換局數的場次，確認後保存冠軍賽設定與教練規則。</p>}{stale&&<p role="alert">賽程或成績已更新，請返回重新預覽。</p>}<div className="dialog-actions"><button disabled={busy} onClick={()=>setPlan(null)}>返回選擇</button><button className="primary" disabled={busy||stale||!plan.can_apply} onClick={()=>void confirm()}>{busy?"儲存中…":"確認套用賽制"}</button></div></>}
    </details>;
}
