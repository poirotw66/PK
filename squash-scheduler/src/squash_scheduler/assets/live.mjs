import {createSource, LiveReader} from './live-source.mjs';
import {polygon, scoreMarks} from './live-polygon.mjs';

const $ = id => document.getElementById(id);
let data = null, view = hashView() || 'bracket', limit = 40, reader;
let navTargets = [], navSignature = '', navFrame = 0, pendingHash = !!location.hash;
let navigationJump = null;
const expandedOverviewPools = new Set();
const compactNavigation = matchMedia('(max-width:1100px)');
const viewNames = {bracket:'晉級圖表', standings:'積分與排名', schedule:'賽程與成績'};
const statuses = {planned: '待比賽', ready: '已就緒', in_progress: '比賽中', completed: '已完成', cancelled: '已取消', excluded: '不列入'};
const outcomes = {played: '完賽', walkover: '棄權／未到場', retired: '中途退賽', double_forfeit: '雙方棄權', cancelled: '已取消'};
const svgNS = 'http://www.w3.org/2000/svg';

function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined && text !== null) el.textContent = text;
  if (className) el.className = className;
  return el;
}

function time(value, date = true) {
  if (!value) return '未排程';
  return new Intl.DateTimeFormat('zh-TW', {timeZone: data.timezone,
    ...(date ? {month: '2-digit', day: '2-digit'} : {}), hour: '2-digit', minute: '2-digit', hourCycle: 'h23'}).format(new Date(value));
}

function day(value) {
  return value ? new Intl.DateTimeFormat('sv-SE', {timeZone: data.timezone, year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date(value)) : '';
}

function label(collection, id) { return data[collection].find(row => row.id === id)?.name || '未排程'; }
function state(match) {
  if (match.delay_active) return `延誤 ${match.delay_minutes} 分鐘`;
  if (match.status === 'ready' && match.called_at) return '已叫號';
  return statuses[match.status] || '待確認';
}

function visible() {
  const query = $('search').value.trim().toLocaleLowerCase();
  return data.matches.filter(m => (!$('division').value || m.division_id === $('division').value) &&
    (!$('date').value || day(m.planned_start_at) === $('date').value) &&
    (!$('court').value || m.court_id === $('court').value) &&
    (!query || [m.code, m.side_a.name, m.side_b.name].some(v => v.toLocaleLowerCase().includes(query))));
}

function options(id, rows, all) {
  const el = $(id), selected = el.value;
  const next = [['', all], ...rows];
  if (JSON.stringify([...el.options].map(o => [o.value, o.textContent])) === JSON.stringify(next)) return;
  el.replaceChildren(...next.map(([value, title]) => { const o = node('option', title); o.value = value; return o; }));
  el.value = next.some(([value]) => value === selected) ? selected : '';
}

function panel(title, subtitle) {
  const el = node('section', null, 'panel'), heading = node('div', null, 'panel-heading');
  heading.append(node('h2', title), node('p', subtitle, 'muted'));
  el.append(heading);
  return el;
}

function hashId() {
  try { return decodeURIComponent(location.hash.slice(1)); } catch { return ''; }
}

function hashView() { return /^reader-(bracket|standings|schedule)-/.exec(hashId())?.[1]; }

function navigationTarget(el, key, group, title) {
  el.id = `reader-${view}-${key}`;
  el.classList.add('reader-jump-target');
  el.tabIndex = -1;
  el.dataset.navGroup = group;
  el.dataset.navLabel = title;
  return el;
}

function markNavigation(id) {
  const previous = $('section-nav').querySelector('[aria-current=location]')?.dataset.target;
  let current;
  for (const link of $('section-nav').querySelectorAll('a')) {
    const active = link.dataset.target === id;
    if (active) { link.setAttribute('aria-current','location'); current = link; }
    else link.removeAttribute('aria-current');
  }
  $('nav-current').textContent = current ? current.title : '選擇閱讀章節';
  if (current && previous !== id && !compactNavigation.matches) {
    const link = current.getBoundingClientRect(), list = $('section-nav').getBoundingClientRect();
    if (link.top < list.top) $('section-nav').scrollTop += link.top-list.top-8;
    else if (link.bottom > list.bottom) $('section-nav').scrollTop += link.bottom-list.bottom+8;
  }
}

function trackNavigation() {
  navFrame = 0;
  if (!navTargets.length) { markNavigation(''); return; }
  // Near the footer several short targets can share the same clamped scroll position.
  if (navigationJump && Math.abs(window.scrollY-navigationJump.y)<=2 && navTargets.some(el=>el.id===navigationJump.id)) {
    markNavigation(navigationJump.id); return;
  }
  navigationJump = null;
  const threshold = compactNavigation.matches ? 120 : 100;
  let current = navTargets[0];
  for (const target of navTargets) {
    if (target.getBoundingClientRect().top <= threshold) current = target;
    else break;
  }
  markNavigation(current.id);
}

function jumpToSection(id, updateHistory = false) {
  const target = document.getElementById(id);
  if (!target) return false;
  if (compactNavigation.matches) $('reader-navigation').open = false;
  if (updateHistory && hashId() !== id) history.pushState(null,'','#'+encodeURIComponent(id));
  target.scrollIntoView({block:'start', behavior:'instant'});
  target.focus({preventScroll:true});
  navigationJump = {id, y:window.scrollY};
  markNavigation(id);
  return true;
}

function renderNavigation() {
  navTargets = [...$('content').querySelectorAll('.reader-jump-target')];
  const signature = JSON.stringify([view,...navTargets.map(el=>[el.id,el.dataset.navGroup,el.dataset.navLabel])]);
  if (signature !== navSignature) {
    navSignature = signature;
    const groups = new Map();
    for (const target of navTargets) {
      const group = target.dataset.navGroup;
      if (!groups.has(group)) groups.set(group,[]);
      groups.get(group).push(target);
    }
    const fragment = document.createDocumentFragment();
    for (const [name,targets] of groups) {
      const group = node('div',null,'reader-nav-group');
      if (name) group.append(node('p',name));
      for (const target of targets) {
        const link = node('a',target.dataset.navLabel);
        link.href = '#'+encodeURIComponent(target.id);
        link.dataset.target = target.id;
        link.title = [name,target.dataset.navLabel].filter(Boolean).join(' · ');
        group.append(link);
      }
      fragment.append(group);
    }
    if (!navTargets.length) fragment.append(node('p','目前篩選沒有可跳轉的章節。','reader-nav-empty muted'));
    const top = $('section-nav').scrollTop;
    $('section-nav').replaceChildren(fragment);
    $('section-nav').scrollTop = top;
    $('nav-context').textContent = `${viewNames[view]} · ${navTargets.length} 個區段`;
  }
  trackNavigation();
  if (pendingHash) {
    pendingHash = false;
    requestAnimationFrame(()=>jumpToSection(hashId()));
  }
}

function resultText(match) {
  if (!match.result) return match.status === 'completed' ? '結果待確認' : state(match);
  const result = match.result;
  const winner = [match.side_a, match.side_b].find(s => s.entry_id && s.entry_id === result.winner_entry_id);
  return winner ? `${result.outcome && result.outcome !== "played" ? (outcomes[result.outcome] || "") + " · " : ""}${winner.name} 勝出` : outcomes[result.outcome] || '結果待確認';
}

function standingsGroup(group) {
  const root = node('section', null, 'standings-group');
  root.dataset.poolId = group.pool_id || group.stage_id;
  const head = node('div', null, 'standings-heading');
  head.append(node('h3', group.name), node('span', `${group.confirmed_matches}／${group.total_matches} 場已確認`,'muted'));
  root.append(head, node('p', group.notice, group.status === 'manual_review' ? 'ranking-pending' : 'muted'), node('p', group.scoring_label, 'scoring-label muted'));
  if (group.advancement_count) root.append(node('p', `前 ${group.advancement_count} 名晉級 · 積分按完整小組計算`, 'muted'));
  const wrapper = node('div', null, 'standings-scroll');
  wrapper.tabIndex = 0;
  wrapper.setAttribute('aria-label', `${group.name}積分表，可左右捲動`);
  const table = node('table', null, 'standings-table'), thead = node('thead'), headers = node('tr');
  ['選手','名次','積分','勝','敗','棄／退'].forEach((label,i) => headers.append(node('th',label,i ? 'num' : '')));
  thead.append(headers); table.append(thead);
  const body = node('tbody');
  for (const row of group.rows) {
    const tr = node('tr'); tr.dataset.entryId = row.entry_id;
    const name = node('td', null, row.qualifies ? 'qualifier' : '');
    name.append(node('span',row.name));
    if (row.qualifies) name.append(node('span','晉級','qualifier-label'));
    if (row.status !== 'active') name.append(node('span',row.status === 'withdrawn' ? '已退賽' : '資格未啟用','muted entry-status'));
    tr.append(name);
    [row.rank ?? (group.status === 'manual_review' ? '待核定' : '—'), row.points,row.wins,row.losses,row.forfeits].forEach(value => tr.append(node('td',String(value),'num')));
    body.append(tr);
  }
  table.append(body); wrapper.append(table); root.append(node('p','左右滑動查看勝敗與棄退','standings-scroll-hint muted'),wrapper);
  root.append(node('p','敗含判敗；棄／退為另外列出的次數。尚未完成或待裁定時，不顯示確定名次。','standings-note muted'));
  return root;
}

function standingsView(matches) {
  const ids = new Set(matches.map(m => m.stage_id)), root = node('div');
  for (const stage of data.stages.filter(s => s.type === 'round_robin' && ids.has(s.id))) {
    const p = navigationTarget(panel(`${label('divisions',stage.division_id)} · ${stage.name}`,'即時積分與排名'), `stage-${stage.id}`, label('divisions',stage.division_id), stage.name);
    const groups = (data.standings || []).filter(g => g.stage_id === stage.id);
    groups.forEach(group => p.append(standingsGroup(group)));
    if (!groups.length) p.append(node('p','資料來源尚未提供積分表。','empty'));
    root.append(p);
  }
  if (!root.children.length) root.append(panel('沒有循環賽積分資料','可選擇包含循環賽的組別，或查看晉級圖表。'));
  return root;
}

function card(match) {
  const el = node('article', null, 'match' + (match.status === 'in_progress' ? ' is-playing' : ''));
  el.dataset.matchId = match.id;
  const head = node('div', null, 'match-head');
  head.append(node('strong', `第 ${match.code} 場`), node('span', state(match), match.status === 'in_progress' ? 'status-playing' : ''));
  el.append(head);
  for (const key of ['side_a', 'side_b']) {
    const side = match[key], result = match.result;
    const won = !!side.entry_id && result?.winner_entry_id === side.entry_id;
    const row = node('div', null, 'side' + (won ? ' winner' : '') + (side.resolution === 'pending' ? ' pending' : ''));
    row.dataset.side = key;
    const identity = node('div',null,'side-identity');
    identity.append(node('span', side.name, 'side-name'));
    if (side.entry_id && side.source_label) identity.append(node('span',side.source_label,'side-origin'));
    row.append(identity);
    const count = scoreMarks(match)?.[key === 'side_a' ? 0 : 1] ?? null;
    row.append(node('span', count === null ? '—' : String(count), 'game-count'));
    if (won) row.append(node('span', '勝方', 'sr-only'));
    el.append(row);
  }
  const footer = node('div', null, 'match-footer');
  if (match.status !== 'completed') footer.append(node('p', `${time(match.planned_start_at)} · ${label('courts', match.court_id)}`));
  if (match.result) footer.append(node('p', (match.result.games.length ? match.result.games.map(g => `${g.side_a}:${g.side_b}`).join('、') + ' · ' : '') + (outcomes[match.result.outcome] || '結果待確認'), 'scoreline'));
  if (match.started_at) footer.append(node('p', `實際開賽 ${time(match.started_at, false)}`));
  else if (match.called_at) footer.append(node('p', `叫號 ${time(match.called_at, false)}`));
  el.append(footer);
  return el;
}

function drawLines() {
  for (const canvas of document.querySelectorAll('.bracket-canvas')) {
    const svg = canvas.querySelector('.graph-lines');
    const cards = new Map([...canvas.querySelectorAll('.match')].map(el => [el.dataset.matchId, el]));
    const base = canvas.getBoundingClientRect();
    svg.setAttribute('width', canvas.scrollWidth);
    svg.setAttribute('height', canvas.scrollHeight);
    svg.replaceChildren();
    for (const match of data.matches) {
      const target = cards.get(match.id);
      if (!target) continue;
      for (const key of ['side_a', 'side_b']) {
        const side = match[key], source = cards.get(side.source_match_id);
        if (!source) continue;
        const a = source.getBoundingClientRect(), b = target.querySelector(`[data-side="${key}"]`).getBoundingClientRect();
        const x1 = a.right - base.left, y1 = a.top + a.height / 2 - base.top;
        const x2 = b.left - base.left - 12, y2 = b.top + b.height / 2 - base.top;
        // Cross-round references are the graph's actual edges, not inferred pairings.
        if (x2 <= x1) continue;
        const path = document.createElementNS(svgNS, 'path'), mid = x1 + (x2 - x1) / 2;
        path.setAttribute('d', `M${x1},${y1} H${mid} V${y2} H${x2}`);
        path.setAttribute('class', (side.entry_id ? 'advanced ' : '') + (side.source_type === 'match_loser' ? 'loser' : ''));
        svg.append(path);
      }
    }
  }
}

function roundRobinResults(rows, key, title = '查看對戰與成績') {
  const details = node('details',null,'stage-results'); details.dataset.stage = key;
  details.append(node('summary',`${title}（${rows.length} 場）`));
  const grid = node('div',null,'match-list');
  grid.dataset.columns = rows.length % 3 === 0 ? '3' : rows.length % 2 === 0 ? '2' : '1';
  rows.forEach(m=>grid.append(card(m))); details.append(grid);
  return details;
}

function chapterLink(title, target, className) {
  const link = node('a',title,className); link.href = '#'+encodeURIComponent(target);
  link.dataset.target = target; return link;
}

function roundRobinOverview(el, stage, groups, rows) {
  const overview = node('section',null,'rr-overview');
  overview.setAttribute('aria-label',`${label('divisions',stage.division_id)}循環賽對戰圖總覽`);
  const head = node('div',null,'rr-overview-heading');
  head.append(node('h3','對戰圖總覽'),node('p',`${groups.length} 個小組 · 先看全部對戰，再往下查看各組積分與成績。`,'muted'));
  overview.append(head,node('p','線端數字為該選手的勝局數；點選連線查看逐局比分。勝／棄為判定結果，退為中途退賽。','polygon-key muted'),node('p','可放大圖表查看較長姓名與比分，放大後可左右滑動。','scroll-hint'));
  const grid = node('div',null,'rr-overview-grid'),details = node('div',null,'rr-pool-details'),accounted = new Set();
  for (const group of groups) {
    const members = new Set(group.rows.map(r=>r.entry_id));
    const matches = rows.filter(m=>members.has(m.side_a.entry_id)&&members.has(m.side_b.entry_id));
    matches.forEach(m=>accounted.add(m.id));
    const detail = navigationTarget(node('section',null,'rr-pool-detail'),`pool-${group.pool_id || group.stage_id}`,label('divisions',stage.division_id),`${group.name} · 積分與成績`);
    detail.dataset.poolId = group.pool_id || group.stage_id;
    const graph = polygon(group,rows,card,{compact:true}) || node('section',null,'round-robin-graph is-compact');
    if (!graph.querySelector('svg')) graph.append(node('h3',group.name+' · 對戰圖'),node('p','此小組尚無可呈現的對戰圖，請查看下方明細。','muted'));
    const meta = node('div',null,'rr-graph-meta');
    meta.append(node('span',`${group.confirmed_matches}／${group.total_matches} 場已確認`,'muted'));
    if (graph.querySelector('svg')) {
      const key = `${stage.id}:${group.pool_id || 'all'}`,zoom = node('button',null,'rr-zoom');zoom.type = 'button';zoom.setAttribute('aria-label',`${group.name}放大圖表`);
      const updateZoom = ()=>{const enlarged = expandedOverviewPools.has(key);graph.classList.toggle('is-expanded',enlarged);zoom.textContent = enlarged?'縮回總覽':'放大圖表';zoom.setAttribute('aria-pressed',String(enlarged));};
      zoom.addEventListener('click',()=>{if (expandedOverviewPools.has(key)) expandedOverviewPools.delete(key);else expandedOverviewPools.add(key);updateZoom();});
      updateZoom();meta.append(zoom);
    }
    meta.append(chapterLink('查看小組明細',detail.id,'rr-detail-link'));
    graph.append(meta); grid.append(graph);
    detail.append(standingsGroup(group),roundRobinResults(matches,`${stage.id}:${group.pool_id || 'all'}`,`${group.name}對戰與成績`),chapterLink('回到對戰圖總覽',el.id,'rr-overview-link'));
    details.append(detail);
  }
  overview.append(grid);el.append(overview,details);
  const remaining = rows.filter(m=>!accounted.has(m.id));
  if (remaining.length) el.append(roundRobinResults(remaining,stage.id,'其他待確認分組的對戰與成績'));
}

function brackets(matches) {
  const root = node('div'), ids = new Set(matches.map(m => m.stage_id));
  for (const stage of data.stages.filter(s => ids.has(s.id)).sort((a, b) => a.order - b.order)) {
    // Keep the stage's context when searching, so the route into the next round remains legible.
    const rows = data.matches.filter(m => m.stage_id === stage.id);
    const el = navigationTarget(panel(`${label('divisions', stage.division_id)} · ${stage.name}`, `${rows.length} 場 · ${rows.filter(m => m.status === 'completed').length} 場完成`), `stage-${stage.id}`, label('divisions',stage.division_id), stage.name);
    if (stage.bye_names?.length) el.append(node('p',`首輪輪空：${stage.bye_names.join('、')}`,'muted'));
    if (stage.type === 'round_robin') {
      const groups = (data.standings || []).filter(g => g.stage_id === stage.id);
      if (groups.length > 1) {
        el.dataset.navLabel = `${stage.name} · 對戰圖總覽`;
        roundRobinOverview(el,stage,groups,rows);
      } else {
        groups.forEach(group => {
          const graph = polygon(group, rows, card);
          if (graph) el.append(graph);
          el.append(standingsGroup(group));
        });
        el.append(roundRobinResults(rows,stage.id));
      }
    } else {
      if (rows.some(m => [m.side_a, m.side_b].some(s => s.source_type === 'match_loser'))) {
        el.append(node('p', '實線：勝方晉級 · 虛線：敗方進入名次賽', 'graph-key muted'));
      }
      el.append(node('p', '可左右滑動查看輪次與晉級對戰', 'scroll-hint'));
      const scroll = node('div', null, 'bracket-scroll');
      scroll.dataset.stage = stage.id;
      scroll.tabIndex = 0;
      scroll.setAttribute('aria-label', `${stage.name}晉級圖，可左右捲動`);
      const canvas = node('div', null, 'bracket-canvas'), svg = document.createElementNS(svgNS, 'svg');
      svg.setAttribute('class', 'graph-lines'); svg.setAttribute('aria-hidden', 'true'); canvas.append(svg);
      const groups = new Map();
      for (const match of rows) {
        const key = `${String(match.round).padStart(3, '0')}-${match.kind === 'placement' ? 1 : 0}`;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(match);
      }
      for (const [, group] of [...groups].sort(([a], [b]) => a.localeCompare(b))) {
        const round = node('section', null, 'round'), stack = node('div', null, 'round-matches');
        round.append(node('h3', group[0].kind === 'placement' && stage.type !== 'placement' ? '名次賽' : `第 ${group[0].round} 輪`));
        group.sort((a, b) => a.position - b.position).forEach(m => stack.append(card(m)));
        round.append(stack); canvas.append(round);
      }
      scroll.append(canvas); el.append(scroll);
    }
    root.append(el);
  }
  return root;
}

function schedule(matches) {
  const root = panel('賽程與成績', `${matches.length} 場符合條件`);
  const ordered = [...matches].sort((a, b) => (a.planned_start_at || 'z').localeCompare(b.planned_start_at || 'z') || a.code.localeCompare(b.code));
  if (pendingHash) {
    const index = ordered.findIndex(m => `reader-schedule-day-${day(m.planned_start_at) || '未排程'}` === hashId());
    if (index >= limit) limit = index + 40;
  }
  let previousDay = null;
  for (const match of ordered.slice(0, limit)) {
    const date = day(match.planned_start_at) || '未排程';
    if (date !== previousDay) {
      root.append(navigationTarget(node('h3',date,'schedule-day-heading'),`day-${date}`,'賽程日期',date));
      previousDay = date;
    }
    const row = node('article', null, 'schedule-row'); row.dataset.matchId = match.id;
    const when = node('div'); when.append(node('p', time(match.planned_start_at)), node('p', label('courts', match.court_id), 'muted'));
    const who = node('div'); who.append(node('p', `${match.side_a.name} 對 ${match.side_b.name}`, 'players'), node('p', `${label('divisions', match.division_id)} · ${label('stages', match.stage_id)} · 第 ${match.code} 場`, 'muted'));
    const result = node('div', null, 'result'); result.append(node('p', resultText(match)), node('p', match.result?.games.map(g => `${g.side_a}:${g.side_b}`).join('、') || state(match), 'muted'));
    if (match.started_at) result.append(node('p', `實際開賽 ${time(match.started_at, false)}`, 'muted'));
    row.append(when, who, result); root.append(row);
  }
  if (ordered.length > limit) {
    const more = node('button', `顯示更多（目前 ${Math.min(limit, ordered.length)}／${ordered.length} 場）`, 'text-action');
    more.type = 'button'; more.addEventListener('click', () => { limit += 40; render(); }); root.append(more);
  }
  return root;
}

function render() {
  if (!data) return;
  const y = window.scrollY, scrolls = new Map([...document.querySelectorAll('.bracket-scroll, .polygon-scroll')].map(el => [el.dataset.stage, el.scrollLeft]));
  const expanded = new Set([...document.querySelectorAll('.stage-results[open]')].map(el => el.dataset.stage));
  const matches = visible();
  $('summary').textContent = `${matches.length} 場比賽 · ${matches.filter(m => m.status === 'completed').length} 場完成`;
  const query = $('search').value.trim().toLocaleLowerCase();
  const singles = data.publication_status === 'ready' ? data.divisions.filter(d => d.single_entry_name && (!$('division').value || d.id === $('division').value) && (!query || d.single_entry_name.toLocaleLowerCase().includes(query) || d.name.toLocaleLowerCase().includes(query))) : [];
  const content = node('div');
  if (matches.length) content.append(view === 'bracket' ? brackets(matches) : view === 'standings' ? standingsView(matches) : schedule(matches));
  if (singles.length) {
    const info = panel('單人報名組別','保留名單，未編排對戰。');
    singles.forEach(d => info.append(navigationTarget(node('p',`${d.name} · ${d.single_entry_name}`),`single-${d.id}`,'單人報名組別',d.name)));
    content.append(info);
  }
  if (!content.children.length) content.append(panel(data.publication_status === 'pending_review' ? '資料待確認' : '沒有符合條件的場次', data.publication_status === 'pending_review' ? data.notice : '可調整組別、搜尋、日期或場地。'));
  $('content').replaceChildren(content);
  for (const el of document.querySelectorAll('.bracket-scroll, .polygon-scroll')) {
    el.scrollLeft = scrolls.get(el.dataset.stage) ?? (el.classList.contains('polygon-scroll') ? (el.scrollWidth - el.clientWidth) / 2 : 0);
  }
  for (const el of document.querySelectorAll('.stage-results')) el.open = expanded.has(el.dataset.stage);
  document.querySelectorAll('[data-view]').forEach(el => el.setAttribute('aria-pressed', String(el.dataset.view === view)));
  window.scrollTo({top: y, behavior: 'instant'});
  renderNavigation();
  requestAnimationFrame(drawLines);
}

function receive(next) {
  const switched = data && data.workspace_id !== next.workspace_id;
  data = next;
  $('event-name').textContent = data.name;
  document.title = data.name + ' · 即時賽況';
  const registrations = data.divisions.every(d => Number.isInteger(d.entry_count)) ? `${data.divisions.reduce((sum,d) => sum+d.entry_count,0)} 筆有效報名 · ` : '';
  $('workspace').textContent = `${registrations}${switched ? '已切換至 ' : ''}工作區 ${data.workspace_id.slice(0, 8)} · ${data.timezone}`;
  $('notice').textContent = data.notice;
  $('revision').textContent = '版本 ' + data.revision_id.slice(0, 8);
  $('revision').title = data.revision_id;
  options('division', data.divisions.map(d => [d.id, d.name]), '全部組別');
  options('court', data.courts.map(c => [c.id, c.name]), '全部場地');
  options('date', [...new Set(data.matches.map(m => day(m.planned_start_at)).filter(Boolean))].sort().map(d => [d, d]), '全部日期');
  render();
}

function connection(status) {
  $('connection').textContent = status.state === 'live' ? '已同步' : status.hasData ? '連線中斷 · 資料可能過期' : '尚未連上賽況';
  $('connection').parentElement.classList.toggle('is-stale', status.state !== 'live');
  $('error').hidden = status.state === 'live';
  $('error').textContent = status.message || '';
  if (status.checkedAt && data) $('updated').textContent = `最後成功同步 ${time(status.checkedAt.toISOString())} · 賽況更新 ${time(data.updated_at)}`;
}

function filterChanged() { limit = 40; history.replaceState(null,'',location.pathname+location.search); render(); }
for (const id of ['division', 'date', 'court']) $(id).addEventListener('change', filterChanged);
$('search').addEventListener('input', filterChanged);
document.querySelectorAll('[data-view]').forEach(el => el.addEventListener('click', () => { view = el.dataset.view; history.replaceState(null,'',location.pathname+location.search); render(); }));
function followChapterLink(event) {
  const link = event.target.closest('a[data-target]');
  if (!link || event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault(); jumpToSection(link.dataset.target,true);
}
$('section-nav').addEventListener('click',followChapterLink);
$('content').addEventListener('click',followChapterLink);
document.querySelector('.reader-back-top').addEventListener('click',event=>{ event.preventDefault(); jumpToSection('reader-top',true); });
function navigationViewport() { $('reader-navigation').open = !compactNavigation.matches; }
navigationViewport(); compactNavigation.addEventListener('change',navigationViewport);
window.addEventListener('scroll',()=>{ if (!navFrame) navFrame = requestAnimationFrame(trackNavigation); },{passive:true});
window.addEventListener('hashchange',()=>{
  const nextView = hashView();
  if (nextView && (nextView !== view || !document.getElementById(hashId()))) { view = nextView; pendingHash = true; render(); }
  else jumpToSection(hashId());
});
$('refresh').addEventListener('click', () => { if (reader) void reader.refresh(); else void start(); });
window.addEventListener('resize', () => requestAnimationFrame(drawLines));
window.addEventListener('online', () => { if (reader) void reader.refresh(); });
document.addEventListener('visibilitychange', () => { if (!document.hidden && reader) void reader.refresh(); });
document.fonts.ready.then(() => { if (data) drawLines(); });

async function start() {
  try {
    const response = await fetch('/live-config.json', {cache: 'no-store', credentials: 'omit'});
    if (!response.ok) throw new Error('無法載入賽況來源設定。');
    const config = await response.json();
    const interval = Number(config.poll_interval_ms);
    if (!Number.isFinite(interval) || interval < 1000) throw new Error('更新間隔設定無效。');
    reader = new LiveReader({source: createSource(config.source, location.href), onSnapshot: receive, onStatus: connection, interval});
    reader.start();
  } catch (error) { connection({state: 'error', hasData: false, message: error.message}); }
}

void start();
