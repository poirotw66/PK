// Shared validation for the public live-score channel and browser rendering.
export function validLiveScore(value) {
  const exact = (v, keys) => v && typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length === keys.length && keys.every(k => Object.hasOwn(v, k));
  const iso = v => typeof v === 'string' && Number.isFinite(Date.parse(v)) && new Date(v).toISOString() === v;
  if (!exact(value, ['match_id','started_at','version','updated_at','score']) || typeof value.match_id !== 'string' || !value.match_id || value.match_id.length > 100 || !iso(value.started_at) || !iso(value.updated_at) || !Number.isSafeInteger(value.version) || value.version < 1) return false;
  const s = value.score;
  if (!exact(s,['rules','games','current','winner']) || !exact(s.rules,['best_of','target','margin']) || ![3,5].includes(s.rules.best_of) || ![11,15].includes(s.rules.target) || s.rules.margin !== 2 || !Array.isArray(s.games) || s.games.length > s.rules.best_of || ![null,'a','b'].includes(s.winner)) return false;
  const validGame = g => exact(g,['side_a','side_b']) && [g.side_a,g.side_b].every(n => Number.isInteger(n) && n >= 0 && n <= 999);
  const terminal = g => Math.max(g.side_a,g.side_b) === Math.max(s.rules.target,Math.min(g.side_a,g.side_b)+2);
  const wins = {a:0,b:0}, needed = Math.floor(s.rules.best_of/2)+1;
  for (const game of s.games) {
    if (Math.max(wins.a,wins.b) >= needed || !validGame(game) || !terminal(game)) return false;
    wins[game.side_a > game.side_b ? 'a':'b']++;
  }
  const winner = wins.a === needed ? 'a' : wins.b === needed ? 'b' : null;
  if (winner !== s.winner || !validGame(s.current)) return false;
  if (winner) return s.current.side_a === 0 && s.current.side_b === 0;
  return Math.max(s.current.side_a,s.current.side_b) < Math.max(s.rules.target,Math.min(s.current.side_a,s.current.side_b)+2);
}
export function visibleLiveScore(match, records) {
  if (match.status !== 'in_progress') return null;
  const row = records.find(row => row.match_id === match.id && row.started_at === match.started_at);
  return row && validLiveScore(row) ? row : null;
}
