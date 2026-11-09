"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import MatchTools from "./match-tools";
import type { ToolMode } from "./match-tools";
import type { ScheduleBoardProps } from "./schedule-board";
import type { Match } from "../lib/schedule/schema";
import { clock, editable, nearestOptions, placementOptions, withDraft } from "../lib/schedule/placements";
import { localTime } from "../lib/schedule/rules";

type Props = ScheduleBoardProps & { onDayChange: (day: string) => void; onFocusHandled: () => void };
export default function MobileSchedule({state,draft,day,court,matches,busy,onMove,onEdit,onDiscard,onCourtChange,onBatchMove,onLocate,focusRequest,onDayChange,onFocusHandled}: Props) {
    const [selectedId,setSelectedId] = useState<string|null>(null);
    const [open,setOpen] = useState(false), [targetDay,setTargetDay] = useState(day), [targetCourt,setTargetCourt] = useState("");
    const [destination,setDestination] = useState<{start:number;court:string}|null>(null);
    const [showAll,setShowAll] = useState(false), [feedback,setFeedback] = useState("");
    const [lastMove,setLastMove] = useState<{id:string;start:number;court:string}|null>(null);
    const [toolMode,setToolMode] = useState<ToolMode|null>(null);
    const dialog = useRef<HTMLDialogElement>(null);
    const working = useMemo(()=>withDraft(state,draft),[state,draft]);
    const selected = working.matches.find(match=>match.id===selectedId);
    const activeCourt = court || state.courts[0].id;
    const matching = new Set(matches.map(match=>match.id));
    const rows = working.matches.filter(match=>matching.has(match.id)&&match.assignment?.court===activeCourt&&localTime(match.assignment.start).startsWith(day)).sort((a,b)=>a.assignment!.start-b.assignment!.start||a.code.localeCompare(b.code));
    const options = useMemo(()=>open&&selectedId ? placementOptions(working,selectedId,targetDay):[],[open,working,selectedId,targetDay]);
    const available = options.filter(option=>option.kind==="available"&&(!targetCourt||option.court===targetCourt));
    const suggestions = showAll ? [...available].sort((a,b)=>a.start-b.start||a.court.localeCompare(b.court)) : nearestOptions(available,selected?.assignment?.start);
    const blockedReasons = [...new Set(options.filter(option=>option.kind==="blocked"&&(!targetCourt||option.court===targetCourt)).flatMap(option=>option.messages??[option.message]))].slice(0,3);
    const currentFeedback = !lastMove || draft[lastMove.id]?.start===lastMove.start && draft[lastMove.id]?.court===lastMove.court ? feedback : "";
    const target = destination&&available.find(option=>option.start===destination.start&&option.court===destination.court);
    const courtName = (id:string)=>state.courts.find(row=>row.id===id)?.name??id;
    useEffect(()=>{
        if(open&&selected&&dialog.current&&!dialog.current.open)dialog.current.showModal();
        else if(!open)dialog.current?.close();
    },[open,selected]);
    useEffect(()=>{
        if(!focusRequest)return;
        const match=working.matches.find(row=>row.id===focusRequest.id);
        if(!match)return;
        setToolMode(null);setSelectedId(match.id);setTargetDay(match.assignment?localTime(match.assignment.start).slice(0,10):day);setTargetCourt(match.assignment?.court??activeCourt);setDestination(null);setShowAll(false);setOpen(true);onFocusHandled();
        // Search requests are explicit; polling and draft updates must not reopen the sheet.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    },[focusRequest]);
    function choose(match:Match){
        setSelectedId(match.id);setTargetDay(day);setTargetCourt(match.assignment?.court??activeCourt);setDestination(null);setShowAll(false);setToolMode(null);setOpen(true);
    }
    function addDestination(){
        if(!selected||!target||busy)return;
        onMove({id:selected.id,start:target.start,court:target.court});
        setLastMove({id:selected.id,start:target.start,court:target.court});
        onDayChange(targetDay);onCourtChange(target.court);setOpen(false);
        setFeedback(`第 ${selected.code} 場已加入草案：${targetDay.slice(5).replace("-","/")} ${clock(target.start)} · ${courtName(target.court)}。請在下方檢查並儲存。`);
    }
    function openTool(mode:ToolMode){setOpen(false);setToolMode(mode);}
    const status = (match:Match)=>match.status==="completed"?"✓ 已完成":match.status==="in_progress"?"● 比賽中":draft[match.id]?"待儲存草案":editable(match)?"可調整":"已鎖定";
    return <section className="mobile-schedule" aria-label="手機賽程快速調整">
        <p className="mobile-schedule-guide">點選一場比賽，挑選空檔，再一起儲存。</p>
        <nav className="agenda-courts" aria-label="切換賽程場地">{state.courts.map(row=><button key={row.id} type="button" aria-pressed={row.id===activeCourt} onClick={()=>onCourtChange(row.id)}>{row.name}<small>{working.matches.filter(match=>match.assignment?.court===row.id&&localTime(match.assignment.start).startsWith(day)).length} 場</small></button>)}</nav>
        <div className="agenda-heading"><h2>{courtName(activeCourt)}</h2><span>{rows.length} 場符合條件</span></div>
        <p className="agenda-feedback" role="status" aria-live="polite">{currentFeedback}</p>
        <div className="agenda-matches">{rows.map(match=>{const original = state.matches.find(row=>row.id===match.id)?.assignment; return <button type="button" key={match.id} data-match-id={match.id} className={"agenda-match"+(match.status==="completed"?" is-completed":match.status==="in_progress"?" is-playing":draft[match.id]?" is-draft":"")} disabled={busy} onClick={()=>choose(match)}>
            <span className="agenda-match-top"><strong>{clock(match.assignment!.start)}</strong><span>{status(match)}</span></span>
            <span className="agenda-match-meta">第 {match.code} 場 · {match.division}</span>
            <span className="agenda-player">{match.side_a}<span>對</span>{match.side_b}</span>
            {draft[match.id]&&<span className="agenda-original">原：{original?`${clock(original.start)} · ${courtName(original.court)}`:"未排程"}</span>}
            <span className="agenda-action">{editable(match)?"找空檔並調整 →":"查看場次 →"}</span>
        </button>;})}</div>
        {!rows.length&&<div className="agenda-empty"><h3>這個場地沒有符合的場次</h3><p>可切換場地、日期，或清除上方搜尋與組別篩選。</p></div>}
        {selected&&<dialog ref={dialog} className="mobile-move-dialog" aria-labelledby="mobile-move-title" onCancel={event=>{event.preventDefault();if(!busy)setOpen(false);}}>
            <div className="mobile-move-header"><div><p>第 {selected.code} 場 · {selected.division}</p><h2 id="mobile-move-title">{editable(selected)?"選擇新時段":"場次資訊"}</h2></div><button type="button" aria-label="關閉時段選擇" disabled={busy} onClick={()=>setOpen(false)}>×</button></div>
            <div className="mobile-move-body"><p className="move-players">{selected.side_a}<span>對</span>{selected.side_b}</p><p className="move-current">目前：{selected.assignment?`${localTime(selected.assignment.start).replace("T"," ")} · ${courtName(selected.assignment.court)}`:"未排程"}</p>
            {editable(selected)?<><div className="move-filters"><label>移到哪一天<select value={targetDay} onChange={event=>{setTargetDay(event.target.value);setDestination(null);setShowAll(false);}}>{state.dates.map(date=><option key={date} value={date}>{date.slice(5).replace("-","/")}</option>)}</select></label><label>移到哪個場地<select value={targetCourt} onChange={event=>{setTargetCourt(event.target.value);setDestination(null);setShowAll(false);}}><option value="">全部場地</option>{state.courts.map(row=><option key={row.id} value={row.id}>{row.name}</option>)}</select></label></div>
            <h3>可用空檔 <span>{available.length} 個</span></h3><p className="move-guidance">已檢查場地占用與選手休息，先選時段再加入草案。</p>
            <div className="move-options">{suggestions.map(option=><button type="button" key={option.court+option.start} aria-pressed={target?.start===option.start&&target?.court===option.court} disabled={busy} onClick={()=>setDestination(option)}><strong>{clock(option.start)}</strong><span>{courtName(option.court)}</span></button>)}</div>
            {!available.length&&<div className="move-empty"><p>這天此場地沒有符合限制的空檔。可換一天或選擇其他場地。</p>{blockedReasons.length>0&&<><p>位置限制包含：</p><ul>{blockedReasons.map(reason=><li key={reason}>{reason}</li>)}</ul></>}</div>}
            {!showAll&&available.length>suggestions.length&&<button type="button" className="move-more" onClick={()=>setShowAll(true)}>查看全部 {available.length} 個空檔</button>}
            <button type="button" className="move-manual" disabled={busy} onClick={()=>{setOpen(false);onEdit(state.matches.find(row=>row.id===selected.id)!);}}>手動指定時間與場地</button>
            {draft[selected.id]&&<button type="button" className="move-manual" disabled={busy} onClick={()=>{const original=state.matches.find(row=>row.id===selected.id)?.assignment;onDiscard(selected.id);if(original){onDayChange(localTime(original.start).slice(0,10));onCourtChange(original.court);}setLastMove(null);setFeedback(`第 ${selected.code} 場已還原正式位置。`);setOpen(false);}}>還原這場的正式位置</button>}</>:<p className="move-empty">{status(selected)}，無法調整時間或場地。</p>}
            <details className="move-tools"><summary>更多場次工具</summary><button type="button" disabled={busy||!editable(selected)} onClick={()=>openTool("swap")}>交換場次</button><button type="button" disabled={busy} onClick={()=>openTool("players")}>選手前後場次</button></details></div>
            {editable(selected)&&<div className="mobile-move-footer"><p>{target?`新位置：${targetDay.slice(5).replace("-","/")} ${clock(target.start)} · ${courtName(target.court)}`:"請先選擇一個可用空檔"}</p><button type="button" className="primary" disabled={busy||!target} onClick={addDestination}>加入草案 →</button><small>尚未儲存，稍後可復原或一起檢查。</small></div>}
        </dialog>}
        {selected&&toolMode&&<MatchTools state={working} selected={selected} mode={toolMode} busy={busy} onMode={setToolMode} onClose={()=>setToolMode(null)} onSwap={onBatchMove} onLocate={onLocate}/>}
    </section>;
}
