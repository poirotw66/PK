import {matchesPlayer} from './reader-state.mjs';

const instant = value => Number.isFinite(Date.parse(value)) ? new Date(value).toISOString() : null;
export function playerSchedule(snapshot, player) {
  return snapshot.matches.filter(match => matchesPlayer(match, player)).map(match => ({
    id: match.id, code: match.code, start: instant(match.planned_start_at), court: match.court_id || null,
  })).sort((a, b) => a.id.localeCompare(b.id));
}

export function scheduleChanges(previous, current) {
  const before = new Map(previous.map(match => [match.id, match]));
  const after = new Map(current.map(match => [match.id, match]));
  const changes = [];
  for (const id of new Set([...before.keys(), ...after.keys()])) {
    const from = before.get(id), to = after.get(id);
    if (from && to && from.start === to.start && from.court === to.court) continue;
    changes.push({id, kind: !from ? 'added' : !to ? 'removed' : 'moved', before: from || null, after: to || null});
  }
  return changes;
}

function validRecord(value) {
  return value?.format === 1 && Array.isArray(value.matches) && value.matches.length <= 4000
    && value.matches.every(match => match && typeof match.id === 'string' && match.id.length <= 200
      && typeof match.code === 'string' && match.code.length <= 200
      && (match.start === null || typeof match.start === 'string' && instant(match.start) === match.start)
      && (match.court === null || typeof match.court === 'string' && match.court.length <= 200))
    && new Set(value.matches.map(match => match.id)).size === value.matches.length;
}

// Persist only a selected player's public match IDs/codes, times and courts, scoped per workspace.
export class SeenSchedules {
  constructor(storage = null) {
    this.storage = storage;
    this.persistent = !!storage;
    this.records = new Map();
  }
  key(workspace, player) { return 'pk-schedule-seen-v1:' + encodeURIComponent(workspace) + ':' + encodeURIComponent(player); }
  read(key) {
    if (this.records.has(key)) return this.records.get(key);
    let record = null;
    let raw = null;
    try { raw = this.storage?.getItem(key); } catch { this.persistent = false; }
    if (raw && raw.length <= 1_000_000) {
      try { const value = JSON.parse(raw); if (validRecord(value)) record = value; } catch {}
    }
    if (record) this.records.set(key, record);
    return record;
  }
  has(workspace, player) { return !!this.read(this.key(workspace, player)); }
  invalidate() { this.records.clear(); }
  confirm(workspace, player, matches) {
    const key = this.key(workspace, player);
    const record = {format: 1, matches: matches.map(match => ({id: match.id, code: match.code, start: match.start, court: match.court}))};
    this.records.set(key, record);
    try { if (this.storage) this.storage.setItem(key, JSON.stringify(record)); }
    catch { this.persistent = false; }
  }
  check(workspace, player, matches) {
    const record = this.read(this.key(workspace, player));
    if (!record) this.confirm(workspace, player, matches);
    return {initial: !record, persistent: this.persistent, changes: record ? scheduleChanges(record.matches, matches) : []};
  }
}

export function changeDescription(change) {
  if (change.kind === 'added') return '新增比賽';
  if (change.kind === 'removed') return '已移出你的賽程';
  const labels = [];
  if (change.before?.start !== change.after?.start) {
    if (!change.after?.start) labels.push('時間待通知');
    else if (!change.before?.start) labels.push('已安排時間');
    else {
      const date = value => new Intl.DateTimeFormat('sv-SE', {timeZone:'Asia/Taipei'}).format(new Date(value));
      if (date(change.before.start) !== date(change.after.start)) labels.push('比賽日期已調整');
      else {const minutes=(Date.parse(change.after.start)-Date.parse(change.before.start))/60000;labels.push(`${minutes>0?'延後':'提早'} ${Math.abs(minutes)} 分鐘`);}
    }
  }
  if (change.before?.court !== change.after?.court) labels.push('更換場地');
  return labels.join(' · ') || '賽程已更新';
}
