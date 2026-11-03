"use client";
import { useMemo, useRef, useState } from "react";
import type { Match, Tournament, Update } from "../lib/schedule/schema";
import { searchMatches } from "../lib/schedule/navigation";
import { localTime } from "../lib/schedule/rules";
type Props = {
    state: Tournament;
    draft: Record<string, Update>;
    query: string;
    onQuery: (value: string) => void;
    onLocate: (match: Match) => void;
};
export default function MatchSearch({ state, draft, query, onQuery, onLocate }: Props) {
    const [open, setOpen] = useState(false);
    const [limit, setLimit] = useState(20);
    const input = useRef<HTMLInputElement>(null);
    const results = useMemo(() => searchMatches(state, draft, query), [state, draft, query]);
    const expanded = open && !!query.trim();
    function locate(match: Match) {
        if (!match.assignment)
            return;
        input.current?.blur();
        setOpen(false);
        onLocate(match);
    }
    return <div className="match-search"><form role="search" onSubmit={event => { event.preventDefault(); const first = results.find(match => match.assignment); if (first)
        locate(first); }}>
    <label>搜尋並定位<input ref={input} type="search" placeholder="姓名、場號或組別" value={query} autoComplete="off" aria-controls="match-search-results" onFocus={() => setOpen(true)} onKeyDown={event => { if (event.key === "Escape")
        setOpen(false); }} onChange={event => { onQuery(event.target.value); setOpen(true); setLimit(20); }}/></label>
    <button type="submit" disabled={!results.some(match => match.assignment)} title="定位第一個已排程結果">定位</button>
  </form>{expanded && <section id="match-search-results" className="search-results" aria-label="跨日期搜尋結果"><div className="search-results-heading"><span role="status">共 {results.length} 場 · 搜尋所有日期與場地</span><button type="button" onClick={() => setOpen(false)} aria-label="收合搜尋結果">×</button></div><p>點結果或按 Enter，切換日期、場地並定位場次。</p><div className="search-results-list">{results.slice(0, limit).map(match => <button type="button" key={match.id} disabled={!match.assignment} onClick={() => locate(match)}><strong>第 {match.code} 場 · {match.side_a} 對 {match.side_b}</strong><span>{match.assignment ? `${localTime(match.assignment.start).replace("T", " ")} · ${state.courts.find(row => row.id === match.assignment!.court)?.name}` : "尚未排程，無法定位"}</span><small>{match.division} · {match.stage}</small></button>)}{!results.length && <p className="search-no-results">所有比賽日期皆沒有符合的場次，請換個姓名或場號。</p>}{results.length > limit && <button className="search-more" type="button" onClick={() => setLimit(limit + 20)}>顯示更多（還有 {results.length - limit} 場）</button>}</div></section>}</div>;
}
