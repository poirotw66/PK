let selectedPlayer = '';
let knownPlayers = [];
let restoredQuery = false;

function writeQueryState() {
  $('share-feedback').hidden = true;
  $('share-link').value = '';
  const search = stateSearch({player:selectedPlayer, search:$('search').value.trim(), date:$('date').value, division:$('division').value, court:$('court').value, view});
  history.replaceState(null, '', location.pathname + search);
}

function restoreQueryState() {
  const state = queryState(location.search);
  selectedPlayer = state.player;
  $('search').value = state.player || state.search;
  let invalid = false;
  for (const id of ['date', 'division', 'court']) {
    const exists = [...$(id).options].some(option => option.value === state[id]);
    $(id).value = exists ? state[id] : '';
    invalid ||= !exists;
  }
  view = hashView() || state.view;
  if (invalid) showShareFeedback('連結中的部分日期、組別或場地已不存在，已移除失效條件。');
  restoredQuery = true;
}

function prepareReaderState() {
  knownPlayers = playerNames(data);
  if (!restoredQuery) restoreQueryState();
}

function choosePlayer(name) {
  $('search').blur();
  selectedPlayer = name;
  $('search').value = name;
  for (const id of ['division', 'court', 'date']) $(id).value = '';
  view = 'schedule';
  filterChanged();
  focusPersonalSummary();
}

function focusPersonalSummary() {
  requestAnimationFrame(() => $('personal-summary').scrollIntoView({block:'start', behavior:matchMedia('(prefers-reduced-motion:reduce)').matches ? 'instant' : 'smooth'}));
}

function searchChanged() {
  const query = $('search').value.trim();
  selectedPlayer = knownPlayers.includes(query) ? query : '';
  if (selectedPlayer) {
    for (const id of ['division', 'court', 'date']) $(id).value = '';
    view = 'schedule';
  }
  filterChanged();
  if (selectedPlayer && !matchMedia('(max-width:640px)').matches) focusPersonalSummary();
}

function syncPersonalSchedule() {
  const query = $('search').value.trim();
  const suggestions = $('player-suggestions');
  suggestions.replaceChildren();
  suggestions.hidden = !query || !!selectedPlayer;
  if (!suggestions.hidden) {
    const normalized = query.normalize('NFKC').replace(/\s/g, '').toLocaleLowerCase();
    const names = knownPlayers.filter(name => name.normalize('NFKC').replace(/\s/g, '').toLocaleLowerCase().includes(normalized));
    if (names.length) {
      suggestions.append(node('p', `選擇選手，查看跨組賽程${names.length > 8 ? `（顯示前 8 / ${names.length} 位，請繼續輸入縮小範圍）` : ''}`));
      for (const name of names.slice(0, 8)) {
        const button = node('button', name, 'player-choice');
        button.type = 'button';
        button.addEventListener('click', () => choosePlayer(name));
        suggestions.append(button);
      }
    } else suggestions.hidden = true;
  }
  const summary = $('personal-summary');
  summary.replaceChildren();
  summary.hidden = !selectedPlayer;
  $('schedule-heading').textContent = selectedPlayer ? '個人賽程' : '賽事日程';
  if (!selectedPlayer) return;
  const all = data.matches.filter(match => matchesPlayer(match, selectedPlayer));
  const groupIds = new Set(all.map(match => match.division_id));
  for (const division of data.divisions) if (division.single_entry_name === selectedPlayer) groupIds.add(division.id);
  summary.append(node('p', '個人賽程 · 跨組合併', 'eyebrow'), node('h2', selectedPlayer));
  summary.append(node('p', knownPlayers.includes(selectedPlayer) ? `${groupIds.size} 個組別 · ${all.length} 場已確定對戰` : '目前發布資料找不到這位選手。'));
  const groups = [...groupIds].map(id => label('divisions', id)).join('、');
  if (groups) summary.append(node('p', groups, 'personal-divisions'));
  summary.append(node('p', '僅列已確定對戰，不包含可能晉級場次。下方篩選可縮小顯示範圍。', 'personal-caveat'));
  const back = node('button', '查看全部賽程', 'leave-personal');
  back.type = 'button';
  back.addEventListener('click', resetFilters);
  summary.append(back);
}

function resetFilters() {
  selectedPlayer = '';
  for (const id of ['search', 'division', 'date', 'court']) $(id).value = '';
  filterChanged();
}

function showShareFeedback(message, url = '') {
  $('share-feedback').hidden = false;
  $('share-message').textContent = message;
  $('share-link').hidden = !url;
  $('share-link').value = url;
}

$('share-query').addEventListener('click', async () => {
  writeQueryState();
  const url = location.href;
  try {
    await navigator.clipboard.writeText(url);
    showShareFeedback('已複製連結，可分享目前的選手、日期、組別與檢視。');
  } catch {
    showShareFeedback('請複製下方連結，即可分享目前查詢。', url);
    $('share-link').focus();
    $('share-link').select();
  }
});
window.addEventListener('popstate', () => {
  if (!data) return;
  restoreQueryState();
  limit = 40;
  pendingHash = !!location.hash;
  render();
});

function syncBrowseControls() {
  syncMobileControls();
  document.body.dataset.view = view;
  syncPersonalSchedule();
  const dates = [...new Set(data.matches.map(m => day(m.planned_start_at)).filter(Boolean))].sort();
  const selected = $('date').value;
  const signature = JSON.stringify([dates, selected]);
  if ($('day-tabs').dataset.signature !== signature) {
    $('day-tabs').dataset.signature = signature;
    $('day-tabs').replaceChildren(...['', ...dates].map((date, index) => {
      const button = node('button', null, 'day-tab');
      button.type = 'button';
      button.setAttribute('aria-pressed', String(date === selected));
      button.append(node('span', date ? `第 ${index} 天` : '完整賽事', 'day-caption'));
      button.append(node('strong', date ? date.slice(5).replace('-', ' / ') : '全部日期'));
      button.addEventListener('click', () => {
        $('date').value = date;
        filterChanged();
      });
      return button;
    }));
  }
  $('clear-search').hidden = !$('search').value;
  $('reset-filters').hidden = !['search', 'division', 'date', 'court'].some(id => $(id).value);
  $('match-total').textContent = data.matches.length;
  $('event-totals').textContent = `場比賽 · ${data.divisions.length} 個組別`;
}

$('clear-search').addEventListener('click', () => {
  selectedPlayer = '';
  $('search').value = '';
  filterChanged();
  $('search').focus();
});
$('reset-filters').addEventListener('click', resetFilters);

function schedule(matches) {
  const root = node('section', null, 'schedule-board');
  const courtOrder = new Map(data.courts.map(court => court.id).sort((a,b) => label('courts',a).localeCompare(label('courts',b),undefined,{numeric:true})).map((id,index) => [id,index]));
  const ordered = orderedMatches(matches, courtOrder);
  const slotKeyFor = match => (day(match.planned_start_at) || '未排程') + 'T' + time(match.planned_start_at, false);
  const batches = new Map();
  for (const match of ordered) {
    const key = slotKeyFor(match);
    if (!batches.has(key)) batches.set(key, new Map());
    const counts = batches.get(key);
    counts.set(match.division_id, (counts.get(match.division_id) || 0) + 1);
  }
  const showBatch = !selectedPlayer && !['search','division','court'].some(id => $(id).value);
  if (pendingHash) {
    const index = ordered.findIndex(m => `reader-schedule-day-${day(m.planned_start_at) || '未排程'}` === hashId());
    if (index >= limit) limit = index + 40;
  }
  let previousDay = null, previousTime = null, grid;
  for (const match of ordered.slice(0, limit)) {
    const date = day(match.planned_start_at) || '未排程';
    if (date !== previousDay) {
      const heading = navigationTarget(node('h2', date, 'schedule-day-heading'), `day-${date}`, '賽程日期', date);
      root.append(heading);
      previousDay = date;
      previousTime = null;
    }
    const slotKey = slotKeyFor(match);
    if (slotKey !== previousTime) {
      const slot = node('section', null, 'time-slot');
      slot.setAttribute('aria-label', `${date} ${time(match.planned_start_at, false)}`);
      const rail = node('div', null, 'slot-clock');
      const clock = node('time', time(match.planned_start_at, false));
      if (match.planned_start_at) clock.dateTime = match.planned_start_at;
      rail.append(clock);
      grid = node('div', null, 'ticket-grid');
      if (showBatch) {
        const groups = [...batches.get(slotKey)].sort((a,b) => b[1] - a[1]);
        const caption = node('div', null, 'batch-caption');
        caption.append(node('strong', `主排 ${label('divisions',groups[0][0])}`));
        caption.append(node('span', groups.length > 1 ? `空場補位：${groups.slice(1).map(([id]) => label('divisions',id)).join('、')}` : '同組別時段'));
        grid.append(caption);
      }
      slot.append(rail, grid);
      root.append(slot);
      previousTime = slotKey;
    }
    const ticket = node('article', null, 'match-ticket');
    ticket.setAttribute('aria-label', `${time(match.planned_start_at)} ${label('courts',match.court_id)} 第 ${match.code} 場`);
    ticket.dataset.matchId = match.id;
    const head = node('div', null, 'ticket-head');
    const clock = node('time', time(match.planned_start_at, false), 'ticket-time sr-only');
    if (match.planned_start_at) clock.dateTime = match.planned_start_at;
    head.append(clock, node('span', label('courts', match.court_id), 'court-tag'));
    const competition = node('p', `${label('divisions', match.division_id)} · ${label('stages', match.stage_id)}`, 'ticket-competition');
    const players = node('div', null, 'ticket-players');
    for (const [key, side] of [['a', match.side_a], ['b', match.side_b]]) {
      const player = node('div', null, 'ticket-player');
      player.append(node('span', key === 'a' ? 'A' : 'B', 'player-number'), node('span', side.name, side.name === selectedPlayer ? 'player-name is-selected-player' : 'player-name'));
      if (match.result?.games.length) {
        const wins = match.result.games.filter(game => key === 'a' ? game.side_a > game.side_b : game.side_b > game.side_a).length;
        player.append(node('strong', String(wins), 'player-score'));
      }
      players.append(player);
    }
    const foot = node('div', null, 'ticket-foot');
    foot.append(node('span', `第 ${match.code} 場`), node('span', match.result ? resultText(match) : state(match), 'ticket-status'));
    ticket.append(head, competition, players, foot);
    if (match.result?.games.length) ticket.append(node('p', match.result.games.map(g => `${g.side_a}:${g.side_b}`).join(' · '), 'ticket-scoreline'));
    if (match.started_at) ticket.append(node('p', `實際開賽 ${time(match.started_at, false)}`, 'ticket-scoreline'));
    grid.append(ticket);
  }
  if (ordered.length > limit) {
    const more = node('button', `載入更多比賽 · 已顯示 ${Math.min(limit, ordered.length)} / ${ordered.length}`, 'load-matches');
    more.type = 'button';
    more.addEventListener('click', () => { limit += 40; render(); });
    root.append(more);
  }
  return root;
}

let syncStatusBusy = false;
function fullTimestamp(value) {
  if (!value) return '尚未同步';
  return new Intl.DateTimeFormat('zh-TW', {timeZone:'Asia/Taipei',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(new Date(value));
}
async function refreshSyncStatus(connectionStatus) {
  if (connectionStatus.state !== 'live') {
    $('sync-detail').textContent = '無法連線網站，保留最後成功讀取的資料。';
    $('source-state').textContent = '目前無法確認';
    $('source-state').dataset.state = 'unknown';
    return;
  }
  if (syncStatusBusy) return;
  syncStatusBusy = true;
  try {
    const response = await fetch('/public/status.json', {cache:'no-store',credentials:'omit',signal:AbortSignal.timeout(8000)});
    if (!response.ok) throw new Error('Status unavailable');
    const status = await response.json();
    $('data-updated').textContent = fullTimestamp(data?.updated_at || status.data_updated_at);
    $('cloud-synced').textContent = fullTimestamp(status.synced_at);
    $('source-state').dataset.state = status.source_state;
    $('source-state').textContent = status.source_state === 'online' ? '本機連線中' : status.source_state === 'offline' ? '本機來源離線' : '等待首次同步';
    $('sync-detail').textContent = status.version !== data?.version ? '網站已收到較新版本，正在取得最新資料。' : status.source_state === 'online' ? '每 10 秒檢查網站版本；本機確認異動後自動同步。檢查更新不會修改賽事。' : status.source_state === 'offline' ? '超過 2 分鐘未收到本機連線，顯示最後同步資料；恢復連線後會自動更新。' : '目前顯示網站內附資料，尚未收到本機同步。';
  } catch {
    $('data-updated').textContent = fullTimestamp(data?.updated_at);
    $('source-state').textContent = '同步狀態暫時無法確認';
    $('source-state').dataset.state = 'unknown';
    $('sync-detail').textContent = '賽程仍可查看；同步狀態查詢失敗，稍後自動重試。';
  } finally { syncStatusBusy = false; }
}


// Reuse the same native selects in the mobile sheet and desktop filter row.
const mobileLayout = matchMedia('(max-width:640px)');
const filterDialog = $('filter-dialog');
function arrangeMobileControls() {
  if (!mobileLayout.matches && filterDialog.open) filterDialog.close();
  $(mobileLayout.matches ? 'mobile-filter-fields' : 'filter-home').append($('filter-fields'));
  $('event-policy').open = !mobileLayout.matches;
}
function syncMobileControls() {
  const count = ['division', 'court', 'date'].filter(id => $(id).value).length;
  $('mobile-filter-label').textContent = count ? `篩選賽程 · ${count}` : '篩選賽程';
  $('mobile-filters').classList.toggle('has-filters', count > 0);
  $('apply-mobile-filters').textContent = `查看 ${visible().length} 場比賽`;
}
arrangeMobileControls();
mobileLayout.addEventListener('change', arrangeMobileControls);
$('mobile-filters').addEventListener('click', () => filterDialog.showModal());
$('close-filters').addEventListener('click', () => filterDialog.close());
$('apply-mobile-filters').addEventListener('click', () => {
  filterDialog.close();
  $('schedule-heading').scrollIntoView({block:'start', behavior:'instant'});
});
$('clear-mobile-filters').addEventListener('click', resetFilters);
filterDialog.addEventListener('click', event => {
  if (event.target !== filterDialog) return;
  const rect = filterDialog.getBoundingClientRect();
  if (event.clientY < rect.top || event.clientY > rect.bottom || event.clientX < rect.left || event.clientX > rect.right) filterDialog.close();
});
filterDialog.addEventListener('close', () => {
  if (mobileLayout.matches) $('mobile-filters').focus({preventScroll:true});
});
$('mobile-search').addEventListener('click', () => {
  $('search').scrollIntoView({block:'center', behavior:'instant'});
  $('search').focus({preventScroll:true});
});

$('search').addEventListener('keydown', event => {
  if (event.key !== 'Enter' || event.isComposing || !mobileLayout.matches) return;
  event.preventDefault();
  $('search').blur();
  if (selectedPlayer) focusPersonalSummary();
  else $('schedule-heading').scrollIntoView({block:'start', behavior:'instant'});
});
