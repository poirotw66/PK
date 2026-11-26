// Rebuild cloud-affected standings from the complete published match results.
const cloudNotice = '雲端成績已更新；';
const automaticNotice = '依已登錄賽果自動計算；';
export function recalculateStandings(snapshot, configuration) {
  if (snapshot.workspace_id !== configuration.workspace_id) return snapshot;
  return {...snapshot, standings: snapshot.standings.map(group => {
    if (![cloudNotice, automaticNotice].some(prefix => group.notice.startsWith(prefix))) return group;
    const rules = configuration.stages[group.stage_id];
    if (!rules) return group;
    return calculateGroup(group, snapshot.matches, rules);
  })};
}

function calculateGroup(group, matches, rules) {
  const rows = group.rows.map(row => ({...row, rank:null, qualifies:false, points:0, wins:0, losses:0, forfeits:0, games_for:0, games_against:0, played_count:0, adjudicated_count:0}));
  const byId = new Map(rows.map(row => [row.entry_id, row]));
  const pointDifference = new Map(rows.map(row => [row.entry_id, 0]));
  const headToHead = new Map(), pairs = new Set();
  const groupMatches = matches.filter(match => match.stage_id === group.stage_id && byId.has(match.side_a.entry_id) && byId.has(match.side_b.entry_id));
  let confirmed = 0, needsReview = false, incomplete = false;
  for (const match of groupMatches) {
    const pair = [match.side_a.entry_id, match.side_b.entry_id].sort().join(':');
    if (pairs.has(pair) || match.side_a.entry_id === match.side_b.entry_id) needsReview = true;
    pairs.add(pair);
    if (['excluded','cancelled'].includes(match.status)) { needsReview = true; continue; }
    const result = match.result;
    if (!result || ['pending','cancelled'].includes(result.outcome)) { incomplete = true; continue; }
    const a = byId.get(match.side_a.entry_id), b = byId.get(match.side_b.entry_id);
    const specialKey = {walkover:'walkover_rule',retired:'retirement_rule',double_forfeit:'double_forfeit_rule'}[result.outcome];
    const special = specialKey ? rules[specialKey] : null;
    if (result.outcome !== 'played' && (!special || typeof special.include_in_rankings !== 'boolean' || typeof special.counts_as_played !== 'boolean' || typeof rules.ranking_denominator_rule?.[result.outcome] !== 'boolean')) { needsReview = true; continue; }
    const winner = result.winner_entry_id;
    if (result.outcome !== 'double_forfeit' && winner !== a.entry_id && winner !== b.entry_id) { needsReview = true; continue; }
    confirmed++;
    a.played_count += Number(special ? special.counts_as_played : true);
    b.played_count += Number(special ? special.counts_as_played : true);
    if (special && !special.include_in_rankings) { needsReview = true; continue; }
    const awards = result.outcome === 'double_forfeit' ? [special.side_a_points, special.side_b_points] : [a,b].map(row => special ? special[winner === row.entry_id ? 'winner_points' : 'loser_points'] : rules.scoring[winner === row.entry_id ? 'win_points' : 'loss_points']);
    if (!awards.every(value => Number.isFinite(value) && value >= 0)) { needsReview = true; continue; }
    for (const [index,row] of [a,b].entries()) {
      row.adjudicated_count += Number(special ? rules.ranking_denominator_rule[result.outcome] : true);
      row.points += awards[index];
      row.wins += Number(winner === row.entry_id);
      row.losses += Number(Boolean(winner) && winner !== row.entry_id);
      row.forfeits += Number(result.outcome === 'double_forfeit' || Boolean(special) && winner !== row.entry_id);
    }
    if (winner) headToHead.set(pair, winner);
    for (const game of result.games) {
      const x = game.side_a, y = game.side_b;
      pointDifference.set(a.entry_id, pointDifference.get(a.entry_id) + x - y);
      pointDifference.set(b.entry_id, pointDifference.get(b.entry_id) + y - x);
      // Played results contain only validated completed games, even when stage defaults are unset.
      if (result.outcome === 'played' || Math.max(x,y) === Math.max(rules.points_to_win_game, Math.min(x,y) + rules.win_by_points)) {
        a.games_for += Number(x > y); a.games_against += Number(y > x);
        b.games_for += Number(y > x); b.games_against += Number(x > y);
      }
    }
  }
  if (pairs.size !== rows.length * (rows.length - 1) / 2) needsReview = true;
  const ordered = [], ties = [];
  for (const points of [...new Set(rows.map(row => row.points))].sort((a,b) => b-a)) {
    const tied = rows.filter(row => row.points === points);
    if(tied.length>1){const ranking=tiedSubgroupOrder(tied.map(row=>row.entry_id),groupMatches,rules);tied.sort((a,b)=>ranking.ordered.indexOf(a.entry_id)-ranking.ordered.indexOf(b.entry_id));ties.push(...ranking.ties);}
    const firstRank = ordered.length + 1;
    tied.forEach((row,index) => {
      const shared = ties.find(tie => tie.includes(row.entry_id));
      row.rank = firstRank + (shared ? tied.findIndex(candidate => shared.includes(candidate.entry_id)) : index);
    });
    ordered.push(...tied);
  }
  const status = needsReview || !incomplete && ties.length ? 'manual_review' : incomplete ? 'provisional' : 'final';
  const notice = automaticNotice + (needsReview ? '部分場次或規則需賽務確認，以下為目前統計。' : incomplete ? '目前為暫列排名，後續賽果會自動更新。' : ties.length ? '全組完賽，同分選手並列，最終順序需賽務裁定。' : '全組完賽，排名已完成計算；晉級名單由賽務確認。');
  return {...group, status, notice, confirmed_matches:confirmed, total_matches:groupMatches.filter(match => !['excluded','cancelled'].includes(match.status)).length, tie_groups:ties, rows:ordered};
}

export function tiedSubgroupOrder(ids, records, rules={}) {
  const members=new Set(ids),totals=new Map(ids.map(id=>[id,[0,0,0,0]]));
  for(const match of records){const a=match.side_a.entry_id,b=match.side_b.entry_id;if(!members.has(a)||!members.has(b)||!match.result)continue;for(const game of match.result.games){const x=game.side_a,y=game.side_b,aa=totals.get(a),bb=totals.get(b);aa[2]+=x;aa[3]+=y;bb[2]+=y;bb[3]+=x;if(maximum(x,y)==Math.max(rules.points_to_win_game??11,Math.min(x,y)+(rules.win_by_points??2))){aa[0]+=Number(x>y);aa[1]+=Number(y>x);bb[0]+=Number(y>x);bb[1]+=Number(x>y);}}}
  function maximum(a,b){return Math.max(a,b);}
  const ratio=(n,d)=>d?[n,d]:n?[1,0]:null;
  const compare=(a,b)=>!a||!b?0:a[1]===0?(b[1]===0?0:1):b[1]===0?-1:Math.sign(a[0]*b[1]-b[0]*a[1]);
  const keys=new Map(ids.map(id=>{const t=totals.get(id);return[id,[ratio(t[0],t[1]),ratio(t[2],t[3])]];}));
  if(ids.some(id=>!keys.get(id)[0]))return{ordered:[...ids],ties:[[...ids]]};
  const cmp=(a,b)=>compare(keys.get(a)[0],keys.get(b)[0])||compare(keys.get(a)[1],keys.get(b)[1]);
  const ordered=[...ids].sort((a,b)=>-cmp(a,b)),groups=[];for(const id of ordered){const last=groups.at(-1);if(last&&!cmp(last[0],id))last.push(id);else groups.push([id]);}
  return{ordered,ties:groups.filter(group=>group.length>1)};
}
