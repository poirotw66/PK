"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import MatchTools from "./match-tools";
import type { ToolMode } from "./match-tools";
import { useIsMobile } from "../hooks/use-mobile";
import type { CSSProperties } from "react";
import type { Match, Tournament, Update } from "../lib/schedule/schema";
import { localTime, parseLocal } from "../lib/schedule/rules";
import { clock, editable, nearestOptions, nextAvailablePosition, occupies, placementOptions, withDraft } from "../lib/schedule/placements";
export type ScheduleBoardProps = {
    state: Tournament;
    draft: Record<string, Update>;
    day: string;
    court: string;
    matches: Match[];
    busy: boolean;
    onMove: (update: Update) => void;
    onEdit: (match: Match) => void;
    onDiscard: (id: string) => void;
    onCourtChange: (id: string) => void;
    onBatchMove: (updates: Update[], focusId: string) => void;
    onLocate: (match: Match) => void;
    focusRequest: {
        id: string;
        sequence: number;
    } | null;
};
export default function ScheduleBoard({ state, draft, day, court, matches, busy, onMove, onEdit, onDiscard, onCourtChange, focusRequest, onBatchMove, onLocate }: ScheduleBoardProps) {
    const boardMain = useRef<HTMLElement>(null);
    const boardScroll = useRef<HTMLDivElement>(null);
    const reasonPanel = useRef<HTMLElement>(null);
    const [blockedPosition, setBlockedPosition] = useState<{
        start: number;
        court: string;
    } | null>(null);
    const [slotFocus, setSlotFocus] = useState<{
        start: number;
        court: string;
        sequence: number;
    } | null>(null);
    const isMobile = useIsMobile(761);
    const [toolMode, setToolMode] = useState<ToolMode | null>(null);
    const [sheetOpen, setSheetOpen] = useState(false);
    const sheet = useRef<HTMLDialogElement>(null);
    useEffect(() => {
        if (sheetOpen && isMobile && sheet.current && !sheet.current.open)
            sheet.current.showModal();
        else
            sheet.current?.close();
    }, [sheetOpen, isMobile]);
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [feedback, setFeedback] = useState("");
    const [feedbackPosition, setFeedbackPosition] = useState<Update | null>(null);
    const working = useMemo(() => withDraft(state, draft), [state, draft]);
    const selected = working.matches.find(match => match.id === selectedId);
    const options = useMemo(() => selectedId ? placementOptions(working, selectedId, day) : [], [working, selectedId, day]);
    const optionsByCell = useMemo(() => new Map(options.map(option => [option.court + ":" + option.start, option])), [options]);
    const matchingIds = new Set(matches.map(match => match.id));
    const activeCourt = court || state.courts[0].id;
    const shownCourts = state.courts.filter(row => isMobile ? row.id === activeCourt : !court || row.id === court);
    const slotHeight = 112;
    const displayGrid = state.grid * Math.ceil(15 / state.grid);
    const first = parseLocal(day + "T" + state.first_start);
    const last = parseLocal(day + "T" + state.last_start);
    const times = Array.from({ length: Math.floor((last - first) / displayGrid) + 1 }, (_, index) => first + index * displayGrid);
    const visible = working.matches.filter(match => match.assignment && localTime(match.assignment.start).startsWith(day));
    const suggestions = nearestOptions(options, selected?.assignment?.start);
    const availableCount = options.filter(option => option.kind === "available").length;
    const label = (match: Match) => `第 ${match.code} 場 · ${match.side_a} 對 ${match.side_b}`;
    const choose = (match: Match) => { setToolMode(null); setSelectedId(match.id); setSheetOpen(false); setFeedback(""); setBlockedPosition(null); };
    function scrollToElement(element: HTMLElement | null) {
        const container = boardScroll.current;
        if (!element || !container)
            return;
        boardMain.current?.scrollIntoView({ block: "start", behavior: "auto" });
        const target = element.getBoundingClientRect(), frame = container.getBoundingClientRect();
        container.scrollTo({ top: Math.max(0, container.scrollTop + target.top - frame.top - 60), left: Math.max(0, container.scrollLeft + target.left - frame.left - 58), behavior: "auto" });
        element.focus({ preventScroll: true });
    }
    useEffect(() => {
        if (!focusRequest)
            return;
        let scrollFrame = 0;
        const selectionFrame = requestAnimationFrame(() => {
            setToolMode(null);
            setSelectedId(focusRequest.id);
            setSheetOpen(false);
            setBlockedPosition(null);
            setFeedback("");
            scrollFrame = requestAnimationFrame(() => scrollToElement(boardScroll.current?.querySelector<HTMLElement>(`[data-match-id="${CSS.escape(focusRequest.id)}"]`) ?? null));
        });
        return () => { cancelAnimationFrame(selectionFrame); cancelAnimationFrame(scrollFrame); };
    }, [focusRequest]);
    useEffect(() => {
        if (!slotFocus)
            return;
        const frame = requestAnimationFrame(() => scrollToElement(boardScroll.current?.querySelector<HTMLElement>(`[data-slot-start="${slotFocus.start}"][data-court="${CSS.escape(slotFocus.court)}"]`) ?? null));
        return () => cancelAnimationFrame(frame);
    }, [slotFocus]);
    useEffect(() => {
        if (!blockedPosition)
            return;
        const frame = requestAnimationFrame(() => reasonPanel.current?.scrollIntoView({ block: "nearest", behavior: "auto" }));
        return () => cancelAnimationFrame(frame);
    }, [blockedPosition]);
    const inspected = blockedPosition ? optionsByCell.get(blockedPosition.court + ":" + blockedPosition.start) : null;
    const showReason = !!selected && !!blockedPosition && inspected?.kind !== "available" && inspected?.kind !== "current";
    const reasonMessages = inspected?.messages?.length ? inspected.messages : [inspected?.message ?? "此場已開打、完成或鎖定，無法移動。"];
    const nextPosition = blockedPosition ? nextAvailablePosition(options, blockedPosition.start, blockedPosition.court) : null;
    function viewNextPosition() {
        if (!nextPosition)
            return;
        onCourtChange(nextPosition.court);
        setSlotFocus(previous => ({ ...nextPosition, sequence: (previous?.sequence ?? 0) + 1 }));
        setBlockedPosition(null);
    }
    function move(start: number, courtId: string) {
        if (!selected || busy)
            return;
        const option = optionsByCell.get(courtId + ":" + start);
        if (option?.kind !== "available") {
            setFeedbackPosition(null);
            setFeedback("");
            setBlockedPosition({ start, court: courtId });
            return;
        }
        setSheetOpen(false);
        setBlockedPosition(null);
        if (isMobile)
            onCourtChange(courtId);
        onMove({ id: selected.id, start, court: courtId });
        setFeedbackPosition({ id: selected.id, start, court: courtId });
        setFeedback(`第 ${selected.code} 場已移入 ${clock(start)} · ${state.courts.find(row => row.id === courtId)?.name} 的草案，可繼續點其他空檔調整。`);
    }
    function openTool(mode: ToolMode) {
        sheet.current?.close();
        setSheetOpen(false);
        setToolMode(mode);
    }
    const toolsActions = selected && <div className="match-tool-actions" aria-label="所選場次工具"><button type="button" disabled={busy || !editable(selected) || !selected.assignment} onClick={() => openTool("swap")}>交換場次</button><button type="button" disabled={busy} onClick={() => openTool("players")}>選手前後場次</button></div>;
    const panelContent = <>      {selected ? <><div className="placement-heading"><p className="eyebrow">移動第 {selected.code} 場</p><button type="button" aria-label={isMobile ? "關閉建議空檔" : "取消選取場次"} onClick={() => {
                if (isMobile)
                    setSheetOpen(false);
                else {
                    setSelectedId(null);
                    setFeedback("");
                }
            }}>×</button></div><h2>{selected.side_a}<span>對</span>{selected.side_b}</h2><p className="placement-division">{selected.division} · {selected.stage}</p><p className="placement-current">{selected.assignment ? `${localTime(selected.assignment.start).replace("T", " ")} · ${state.courts.find(row => row.id === selected.assignment!.court)?.name}` : "尚未排程"}</p>{toolsActions}
      {editable(selected) ? <><div className="placement-count"><strong>{availableCount}</strong><span>個可用位置 · {day.slice(5).replace("-", "/")}</span></div><p className="placement-help">直接點時間表的綠色空檔，或選下方建議位置。可切換上方日期查看其他天。</p><h3>距離目前時間最近</h3><div className="placement-suggestions">{suggestions.map(option => <button key={option.court + option.start} disabled={busy} onClick={() => move(option.start, option.court)}><strong>{clock(option.start)}</strong><span>{state.courts.find(row => row.id === option.court)?.name}</span><b>移入草案 →</b></button>)}</div>{!suggestions.length && <p className="placement-empty">這天沒有符合限制的空檔，可切換日期，或先移動其他場次騰出位置。</p>}<button className="manual-edit" disabled={busy} onClick={() => { setSheetOpen(false); onEdit(state.matches.find(row => row.id === selected.id)!); }}>手動指定日期、時間與場地</button>{draft[selected.id] && <button className="manual-edit" disabled={busy} onClick={() => onDiscard(selected.id)}>還原這場的正式位置</button>}</> : <p className="placement-empty">此場已開打、完成或鎖定，無法移動。</p>}</> : <><p className="eyebrow">不必再逐場輸入時間</p><h2>想把哪場移到空檔？</h2><ol className="placement-steps"><li><b>1</b><span>點選時間表上的比賽</span></li><li><b>2</b><span>綠色空檔就是可放的位置</span></li><li><b>3</b><span>點一下加入草案，最後一起檢查儲存</span></li></ol><p className="placement-help">即使篩選特定組別，其他場次仍會保留在表上，避免把已占用的場地看成空檔。</p></>}
      <div className="placement-feedback" role="status" aria-live="polite">{feedbackPosition && (!draft[feedbackPosition.id] || selected?.assignment?.start !== feedbackPosition.start || selected?.assignment?.court !== feedbackPosition.court) ? "" : feedback}</div>
    </>;
    return <div className="board-workbench">
    <section ref={boardMain} className="board-main" aria-label="場地空檔與比賽時段">
      <div className="board-instructions"><p><strong>先點比賽，再點空檔</strong><span>每 {displayGrid} 分鐘一格；儲存前會再檢查整份賽程。</span></p><div className="board-legend"><span className="legend-free">空檔</span><span className="legend-valid">可移入</span><span className="legend-completed">✓ 已完成</span><span className="legend-playing">比賽中</span><span className="legend-muted">其他篩選外場次</span></div></div>
      {isMobile && <>
        <nav className="mobile-courts" aria-label="切換場地">{state.courts.map(row => <button key={row.id} aria-pressed={activeCourt === row.id} onClick={() => onCourtChange(row.id)} title={row.name}><span>{row.name}</span><small>{visible.filter(match => match.assignment?.court === row.id).length} 場</small></button>)}</nav>
        {selected && <div className="mobile-selection"><div><strong>第 {selected.code} 場</strong><span>{selected.side_a} 對 {selected.side_b}</span></div><button disabled={busy} onClick={() => setSheetOpen(true)}>建議空檔 <b>{availableCount}</b></button><button className="selection-close" aria-label="取消選取場次" onClick={() => { setSelectedId(null); setFeedback(""); setBlockedPosition(null); }}>×</button></div>}{selected && toolsActions}
      </>}
      {showReason && <section ref={reasonPanel} className="placement-reason" role="status" aria-live="polite" aria-label="空檔限制原因"><div className="reason-heading"><strong>{clock(blockedPosition!.start)} · {state.courts.find(row => row.id === blockedPosition!.court)?.name} 不能放入</strong><button type="button" aria-label="關閉限制原因" onClick={() => setBlockedPosition(null)}>×</button></div><ul>{reasonMessages.map((message, index) => <li key={index}>{message}</li>)}</ul>{nextPosition ? <div className="reason-next"><p>{nextPosition.court === blockedPosition!.court ? "此場地下一個可移入空檔" : "其他場地可移入空檔"}：<b>{clock(nextPosition.start)} · {state.courts.find(row => row.id === nextPosition.court)?.name}</b></p><button type="button" onClick={viewNextPosition}>查看這個空檔 →</button></div> : <p>今天沒有更晚的可移入空檔，可切換日期或先調整其他場次。</p>}</section>}
      <div ref={boardScroll} className="board-scroll" tabIndex={0} aria-label="場地時間表，可上下左右捲動">
        <div className="court-board" style={{ "--court-count": shownCourts.length, "--slot-count": times.length, "--slot-height": `${slotHeight}px` } as CSSProperties}>
          <div className="board-corner">時間</div>{shownCourts.map(row => <div className="board-court-heading" key={row.id}><span>{row.name}</span><small>{day.slice(5).replace("-", "/")}</small></div>)}
          <div className="board-hours">{times.map(start => <div key={start}>{clock(start)}</div>)}</div>
          {shownCourts.map(row => <div key={row.id} className="board-lane">
            {times.map(start => {
                const option = optionsByCell.get(row.id + ":" + start);
                const occupied = visible.some(match => occupies(match, start, row.id));
                const open = row.windows.some(window => window.start <= start && start + displayGrid <= window.end);
                const kind = selected ? option?.kind ?? "blocked" : occupied ? "occupied" : open ? "free" : "closed";
                const text = selected ? option?.kind === "available" ? "移到這裡" : option?.kind === "current" ? "目前位置" : occupied ? "" : "不可放入" : occupied ? "" : open ? "空檔" : "未開放";
                const title = selected ? option?.message : open ? "先點選要移動的比賽，再選這個空檔" : "場地未開放";
                return <button type="button" className={"board-cell is-" + kind} key={start} data-slot-start={start} data-court={row.id} disabled={busy || !selected || occupied} title={title} aria-label={`${clock(start)} · ${row.name} · ${title}`} onClick={() => move(start, row.id)}>{text}</button>;
            })}
            {visible.filter(match => match.assignment!.court === row.id).map(match => {
                const assignment = match.assignment!;
                const collisions = visible.filter(other => other.assignment!.court === row.id && other.assignment!.start < assignment.start + match.play + match.buffer && other.assignment!.start + other.play + other.buffer > assignment.start).sort((a, b) => a.id.localeCompare(b.id));
                const lane = collisions.findIndex(other => other.id === match.id);
                const matched = matchingIds.has(match.id);
                const locked = !editable(match);
                return <button type="button" key={match.id} data-match-id={match.id} className={"board-match" + (match.status === "completed" ? " is-completed" : match.status === "in_progress" ? " is-playing" : "") + (selectedId === match.id ? " is-picked" : "") + (draft[match.id] ? " is-draft" : "") + (!matched ? " is-dimmed" : "") + (collisions.length > 1 ? " is-overlap" : "")} style={{ top: `${(assignment.start - first) / displayGrid * slotHeight + 3}px`, height: `${Math.max(48, (match.play + match.buffer) / displayGrid * slotHeight - 6)}px`, left: `calc(${lane / collisions.length * 100}% + 4px)`, width: `calc(${100 / collisions.length}% - 8px)` }} aria-label={`${label(match)} · ${clock(assignment.start)} · ${row.name}${match.status === "completed" ? " · 已完成" : match.status === "in_progress" ? " · 比賽中" : ""}${locked ? " · 無法移動" : " · 點選後可選空檔"}`} aria-pressed={selectedId === match.id} disabled={busy} onClick={() => choose(match)}><span className="board-match-meta"><b>{clock(assignment.start)} · {match.code}</b><span>{match.status === "completed" ? "✓ 已完成" : match.status === "in_progress" ? "● 比賽中" : draft[match.id] ? "草案" : locked ? "已鎖定" : ""}</span></span><strong>{match.side_a} <span>對</span> {match.side_b}</strong><small>{match.division}</small></button>;
            })}
          </div>)}
        </div>
      </div>
    </section>
    {selected && toolMode && <MatchTools key={selected.id} state={working} selected={selected} mode={toolMode} busy={busy} onMode={setToolMode} onClose={() => setToolMode(null)} onSwap={onBatchMove} onLocate={onLocate}/>}
    {isMobile ? selected && <dialog ref={sheet} className="placement-dialog" aria-label="建議空檔" onCancel={event => { event.preventDefault(); setSheetOpen(false); }}>{panelContent}</dialog> : <aside className="placement-panel" aria-label="移動比賽與建議空檔">{panelContent}</aside>}
  </div>;
}
