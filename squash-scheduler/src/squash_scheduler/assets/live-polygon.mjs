// Public snapshot only: no operator state, result mutation or alternate ranking logic.
const NS = 'http://www.w3.org/2000/svg';
const selectedMatches = new Map();
const outcomeLabels = {played: '完賽', walkover: '棄權／未到場', retired: '中途退賽', double_forfeit: '雙方棄權', cancelled: '已取消'};

export function scoreMarks(match) {
  const r = match.result;
  if (!r || match.status !== 'completed' || r.outcome === 'cancelled') return null;
  const wonA = !!match.side_a.entry_id && r.winner_entry_id === match.side_a.entry_id;
  if (r.outcome === 'double_forfeit') return ['棄', '棄'];
  if (r.outcome === 'walkover') return r.winner_entry_id ? (wonA ? ['勝', '棄'] : ['棄', '勝']) : null;
  if (r.outcome === 'retired') {
    if (!r.winner_entry_id) return null;
    // The final recorded game may be incomplete; do not turn its lead into a won game.
    return wonA ? ['勝', '退'] : ['退', '勝'];
  }
  const counts = ['side_a', 'side_b'].map((key, i) => String(r.games.filter(g => g[key] > g[i ? 'side_a' : 'side_b']).length));
  return r.games.length ? counts : null;
}

export function polygonModel(group, matches) {
  // Keep draw/registration order, never the changing ranking order.
  const members = new Map(group.rows.map(row => [row.entry_id, row]));
  const ordered = [];
  for (const m of matches) for (const side of [m.side_a, m.side_b]) {
    if (members.has(side.entry_id) && !ordered.includes(side.entry_id)) ordered.push(side.entry_id);
  }
  for (const id of members.keys()) if (!ordered.includes(id)) ordered.push(id);
  const count = ordered.length;
  if (count < 2 || count > 6) return null;
  const start = count === 4 ? -3 * Math.PI / 4 : -Math.PI / 2;
  const vertices = ordered.map((id, i) => {
    const angle = start + i * 2 * Math.PI / count;
    return {...members.get(id), x: 350 + 180 * Math.cos(angle), y: 270 + 180 * Math.sin(angle),
      labelX: 350 + 238 * Math.cos(angle), labelY: 270 + 224 * Math.sin(angle)};
  });
  const byId = new Map(vertices.map(v => [v.entry_id, v]));
  // Draw actual scheduled pairings only. Never invent a missing edge or result.
  const edges = matches.filter(m => byId.has(m.side_a.entry_id) && byId.has(m.side_b.entry_id) && m.side_a.entry_id !== m.side_b.entry_id)
    .map(match => ({match, a: byId.get(match.side_a.entry_id), b: byId.get(match.side_b.entry_id), marks: scoreMarks(match)}));
  return {vertices, edges};
}

function svg(tag, attrs = {}, text) {
  const el = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value));
  if (text !== undefined) el.textContent = text;
  return el;
}
function html(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}
function description(match, marks) {
  const status = match.result ? outcomeLabels[match.result.outcome] : match.status === 'in_progress' ? '進行中' : match.status === 'cancelled' || match.status === 'excluded' ? '已取消／排除' : '尚未確認賽果';
  return `${match.code} 場：${match.side_a.name}${marks ? ' ' + marks[0] : ''} 對 ${match.side_b.name}${marks ? ' ' + marks[1] : ''}，${status}`;
}

export function polygon(group, matches, makeCard, {compact = false} = {}) {
  const model = polygonModel(group, matches);
  if (!model || !model.edges.length) return null;
  const key = `${group.stage_id}:${group.pool_id || 'all'}`;
  const root = html('section', 'round-robin-graph'); root.dataset.poolId = group.pool_id || group.stage_id;
  root.append(html('h3', '', `${group.name} · 對戰圖`));
  if (compact) root.classList.add('is-compact');
  else root.append(html('p', 'muted polygon-key', '線端數字為靠近該選手的勝局數；點選連線查看逐局比分。勝／棄為判定結果，退為中途退賽。'), html('p', 'scroll-hint', '左右捲動可查看完整對戰圖'));
  const scroll = html('div', 'polygon-scroll'); scroll.dataset.stage = key;
  scroll.tabIndex = 0; scroll.setAttribute('aria-label', `${group.name}對戰圖，可左右捲動`);
  const xs = model.vertices.map(v=>v.labelX), ys = model.vertices.map(v=>v.labelY);
  const bounds = compact ? `${Math.min(...xs)-100} ${Math.min(...ys)-38} ${Math.max(...xs)-Math.min(...xs)+200} ${Math.max(...ys)-Math.min(...ys)+76}` : '0 0 700 550';
  const canvas = svg('svg', {viewBox: bounds, class: 'polygon-canvas', role: 'group', 'aria-label': `${group.name}循環賽對戰圖`});
  const lines = svg('g'), badges = svg('g'), names = svg('g');
  const detail = html('div', 'polygon-detail'); detail.hidden = true;
  const edgeElements = new Map();
  function select(match, focus = false) {
    selectedMatches.set(key, match.id);
    for (const [id, el] of edgeElements) { el.classList.toggle('is-selected', id === match.id); el.setAttribute('aria-pressed', String(id === match.id)); }
    const close = html('button', 'polygon-close', '關閉明細'); close.type = 'button';
    close.addEventListener('click', () => { selectedMatches.delete(key); detail.hidden = true; for (const el of edgeElements.values()) { el.classList.remove('is-selected'); el.setAttribute('aria-pressed', 'false'); } edgeElements.get(match.id)?.focus(); });
    detail.replaceChildren(makeCard(match), close); detail.hidden = false;
    if (focus) { detail.tabIndex = -1; detail.focus({preventScroll: true}); }
  }
  for (const {match, a, b, marks} of model.edges) {
    const confirmed = !!marks, special = confirmed && match.result.outcome !== 'played';
    const edge = svg('g', {class: `polygon-edge${confirmed ? ' is-confirmed' : ''}${special ? ' is-special' : ''}`, 'data-match-id': match.id, role: 'button', tabindex: 0, 'aria-label': description(match, marks), 'aria-pressed': 'false'});
    edge.append(svg('title', {}, description(match, marks)), svg('line', {x1:a.x,y1:a.y,x2:b.x,y2:b.y,class:'polygon-line'}), svg('line', {x1:a.x,y1:a.y,x2:b.x,y2:b.y,class:'polygon-hit'}));
    edge.addEventListener('click', () => select(match));
    edge.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(match, true); } });
    edgeElements.set(match.id, edge); lines.append(edge);
    if (marks) {
      const length = Math.hypot(b.x-a.x,b.y-a.y), inset = Math.min(.4, 80/length);
      marks.forEach((text, i) => {
        // Equal distance from each endpoint separates the four nearby scores in a pentagon.
        const t = i ? 1-inset : inset, x = a.x+(b.x-a.x)*t, y = a.y+(b.y-a.y)*t;
        // White backing keeps scores legible where complete-graph lines cross.
        const badge = svg('g', {class:`polygon-score${match.result.winner_entry_id === (i ? match.side_b : match.side_a).entry_id ? ' is-winner' : ''}`, 'data-match-id':match.id,'data-entry-id':(i ? b : a).entry_id,'aria-hidden':'true'});
        const width = text.length > 1 ? 44 : 30;
        badge.append(svg('rect',{x:x-width/2,y:y-15,width,height:30,rx:4}), svg('text',{x,y:y+1,'text-anchor':'middle','dominant-baseline':'middle'},text));
        badge.addEventListener('click', () => select(match)); badges.append(badge);
      });
    }
  }
  for (const v of model.vertices) {
    const name = svg('g', {'data-entry-id':v.entry_id,class:'polygon-player'});
    name.append(svg('line',{x1:v.x,y1:v.y,x2:v.labelX,y2:v.labelY}),svg('circle',{cx:v.x,cy:v.y,r:3}),svg('rect',{x:v.labelX-84,y:v.labelY-21,width:168,height:42,rx:4}));
    const text = svg('text',{x:v.labelX,y:v.labelY+1,'text-anchor':'middle','dominant-baseline':'middle'},v.name);
    if ([...v.name].length > (compact ? 5 : 9)) { text.setAttribute('textLength','150'); text.setAttribute('lengthAdjust','spacingAndGlyphs'); }
    name.append(text); names.append(name);
  }
  canvas.append(lines,badges,names); scroll.append(canvas); root.append(scroll,detail);
  const chosen = model.edges.find(e => e.match.id === selectedMatches.get(key));
  if (chosen) select(chosen.match);
  return root;
}
