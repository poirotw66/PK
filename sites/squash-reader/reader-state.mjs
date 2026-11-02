// Public reader state is encoded in URLs; no private identity or operator data.
export const views = new Set(['schedule', 'bracket', 'standings']);

export function playerNames(snapshot) {
  const names = new Set(snapshot.divisions.map(d => d.single_entry_name).filter(Boolean));
  for (const match of snapshot.matches) {
    for (const side of [match.side_a, match.side_b]) {
      if (side.entry_id && side.resolution === 'confirmed') names.add(side.name);
    }
  }
  return [...names].sort((a, b) => a.localeCompare(b, 'zh-Hant'));
}

export function matchesPlayer(match, name) {
  return [match.side_a, match.side_b].some(side => side.entry_id && side.resolution === 'confirmed' && side.name === name);
}

export function queryState(search) {
  const params = new URLSearchParams(search);
  return {
    player: params.get('player') || '',
    search: params.get('q') || '',
    division: params.get('division') || '',
    court: params.get('court') || '',
    date: params.get('date') || '',
    view: views.has(params.get('view')) ? params.get('view') : 'schedule',
  };
}

export function stateSearch(state) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries({player:state.player, q:state.player ? '' : state.search, date:state.date, division:state.division, court:state.court, view:state.view})) {
    if (value) params.set(key, value);
  }
  return '?' + params.toString();
}

// Compare instants rather than timestamp strings with potentially different offsets.
export function orderedMatches(matches, courtOrder = new Map()) {
  const instant = match => Number.isFinite(Date.parse(match.planned_start_at)) ? Date.parse(match.planned_start_at) : Infinity;
  return [...matches].sort((a,b) => instant(a)-instant(b) || ((courtOrder.get(a.court_id) ?? Number.MAX_SAFE_INTEGER) - (courtOrder.get(b.court_id) ?? Number.MAX_SAFE_INTEGER)) || a.code.localeCompare(b.code,undefined,{numeric:true}));
}
