let selectedPlayer = '';
let knownPlayers = [];
let restoredQuery = false;
let seenSchedules = null;
let personalChanges = new Map();
function seenScheduleStore() {
  if (!seenSchedules) {
    let storage = null;
    try { storage = window.localStorage; } catch {}
    seenSchedules = new SeenSchedules(storage);
  }
  return seenSchedules;
}
let acknowledgedPersonal = '';
function shortPersonalPosition(value) {
  if (!value) return '原本沒有此場比賽';
  const court = value.court ? data.courts.find(court => court.id === value.court)?.name || '原場地已移除' : '場地待安排';
  return `${value.start ? day(value.start).slice(5).replace('-','/') + ' ' + time(value.start,false) : '時間待通知'} · ${court}`;
}
function appendPersonalChange(target, change) {
  target.classList.add('has-personal-change');
  const note = node('div', null, 'personal-match-change');
  note.append(node('p', changeDescription(change), 'change-action'));
  if (change.after) {
    const destination = node('div', null, 'change-destination');
    const when = node('div');
    when.append(node('span', change.after.start ? `新時間 · ${day(change.after.start).slice(5).replace('-','/')}` : '新時間'), node('strong', change.after.start ? time(change.after.start,false) : '待通知'));
    const where = node('div');
    where.append(node('span', '新場地'), node('strong', change.after.court ? label('courts',change.after.court) : '待安排'));
    destination.append(when,where);
    note.append(destination);
  } else note.append(node('p', '這場已移出你的個人賽程，請向賽務確認。', 'change-removed'));
  if (change.before) note.append(node('p', `原訂：${shortPersonalPosition(change.before)}`, 'change-previous'));
  target.append(note);
}
function syncPersonalChanges(summary, all) {
  personalChanges = new Map();
  const store = seenScheduleStore();
  const player = selectedPlayer, workspace = data.workspace_id;
  const current = playerSchedule(data, player);
  const result = store.check(workspace, player, current);
  personalChanges = new Map(result.changes.map(change => [change.id, change]));
  if (!result.changes.length) {
    if (!result.persistent) summary.append(node('p', '提醒只保留在這次開啟的頁面；離開後會重新開始比較。', 'personal-memory-note'));
    if (acknowledgedPersonal === workspace+'\n'+player) summary.append(node('p', '已確認，下次有調整會再提醒。', 'personal-confirmed'));
    return;
  }
  acknowledgedPersonal = '';
  const notice = node('section', null, 'personal-change-notice');
  notice.setAttribute('aria-label', '你的賽程異動');
  const hasNewArrangement = result.changes.some(change => change.after);
  notice.append(node('h3', `${result.changes.length} 場${hasNewArrangement ? '比賽有新安排' : '個人賽程已調整'}`), node('p', hasNewArrangement ? '請依下方的新時間、新場地出賽。' : '以下場次已移出，請向賽務確認。'));
  const list = node('div', null, 'personal-change-list');
  for (const change of result.changes) {
    const match = all.find(match => match.id === change.id);
    const item = node('article');
    item.append(node('h4', match ? `第 ${match.code} 場 · ${label('divisions', match.division_id)}` : `第 ${change.before.code} 場（原個人賽程）`));
    if (match) item.append(node('p', `${match.side_a.name} 對 ${match.side_b.name}`, 'changed-match-players'));
    appendPersonalChange(item, change);
    list.append(item);
  }
  notice.append(list);
  const actions = node('div', null, 'personal-change-actions');
  const show = node('button', '查看我的完整賽程');
  show.type = 'button';
  show.addEventListener('click', () => { for (const id of ['division','court','date']) $(id).value = ''; view = 'schedule'; filterChanged(); });
  const confirm = node('button', '我知道了');
  confirm.type = 'button';
  confirm.addEventListener('click', () => { store.confirm(workspace, player, current); acknowledgedPersonal = workspace+'\n'+player; render(); });
  actions.append(confirm, show);
  notice.append(actions);
  const help = node('details', null, 'personal-reminder-help');
  help.append(node('summary', '提醒說明'), node('p', result.persistent ? '這部裝置會記住你確認過的安排。換裝置或清除瀏覽資料後，會重新開始比較。' : '這部裝置目前無法保存提醒；離開頁面後會重新開始比較。'));
  notice.append(help);
  summary.append(notice);
}


function writeQueryState() {
  $('share-feedback').hidden = true;
  $('share-link').value = '';
  const search = stateSearch({player:selectedPlayer, search:$('search').value.trim(), date:$('date').value, division:$('division').value, court:$('court').value, view});
  page=pageForView(view);
  history.replaceState(null, '', pageHref(page,search));
  applyPageUi();
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
  page=readerPage(location.pathname,location.search,location.hash);
  view=pageInfo[page].view;
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
  summary.hidden = !selectedPlayer || page!=='schedule';
  $('schedule-heading').textContent = page==='bracket'?'選擇組別查看晉級':page==='standings'?'選擇組別查看積分':selectedPlayer?'個人賽程':'賽程與成績';
  personalChanges = new Map();
  if (!selectedPlayer) return;
  const all = data.matches.filter(match => matchesPlayer(match, selectedPlayer));
  const groupIds = new Set(all.map(match => match.division_id));
  for (const division of data.divisions) if (division.single_entry_name === selectedPlayer) groupIds.add(division.id);
  summary.append(node('p', '個人賽程 · 跨組合併', 'eyebrow'), node('h2', selectedPlayer));
  summary.append(node('p', knownPlayers.includes(selectedPlayer) ? `${groupIds.size} 個組別 · ${all.length} 場已確定對戰` : '目前發布資料找不到這位選手。'));
  if (knownPlayers.includes(selectedPlayer) || all.length || seenScheduleStore().has(data.workspace_id,selectedPlayer)) syncPersonalChanges(summary, all);
  const groups = [...groupIds].map(id => label('divisions', id)).join('、');
  if (groups && !personalChanges.size) summary.append(node('p', groups, 'personal-divisions'));
  if (!personalChanges.size) summary.append(node('p', '僅列已確定對戰，不包含可能晉級場次。', 'personal-caveat'));
  const back = node('button', '查看全體選手賽程', 'leave-personal');
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
window.addEventListener('storage', event => {
  if (!event.key || event.key.startsWith('pk-schedule-seen-v1:')) {
    seenSchedules?.invalidate();
    if (data) render();
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
  applyPageUi();
  if(page==='live') renderLiveNow();
  paintLiveScores();
  void refreshLiveScores();
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
  const context=[];
  if(['bracket','standings'].includes(page)) {
    if($('search').value)context.push('選手：'+$('search').value);
    if($('date').value)context.push('日期：'+$('date').value);
    if($('court').value)context.push('場地：'+label('courts',$('court').value));
  }
  $('query-context').hidden=!context.length;
  $('query-context').textContent=context.join(' · ');
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
    if (match.status === 'in_progress') ticket.append(liveScorePanel(match));
    if (match.started_at) ticket.append(node('p', `實際開賽 ${time(match.started_at, false)}`, 'ticket-scoreline'));
    if (personalChanges.has(match.id)) appendPersonalChange(ticket, personalChanges.get(match.id));
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
    $('source-state').textContent = status.source_state === 'cloud' ? '雲端後台同步' : status.source_state === 'online' ? '本機連線中' : status.source_state === 'offline' ? '本機來源離線' : '等待首次同步';
    $('sync-detail').textContent = status.version !== data?.version ? '網站已收到較新版本，正在取得最新資料。' : status.source_state === 'cloud' ? '每 10 秒檢查更新；目前場上由管理員確認，預定時間不代表已開賽。' : status.source_state === 'online' ? '每 10 秒檢查網站版本；本機確認異動後自動同步。檢查更新不會修改賽事。' : status.source_state === 'offline' ? '超過 2 分鐘未收到本機連線，顯示最後同步資料；恢復連線後會自動更新。' : '目前顯示網站內附資料，尚未收到本機同步。';
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
  $(mobileLayout.matches && page==='schedule' ? 'mobile-filter-fields' : 'filter-home').append($('filter-fields'));

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
$('search').addEventListener('keydown', event => {
  if (event.key !== 'Enter' || event.isComposing || !mobileLayout.matches) return;
  event.preventDefault();
  $('search').blur();
  if (selectedPlayer) focusPersonalSummary();
  else $('schedule-heading').scrollIntoView({block:'start', behavior:'instant'});
});


function renderLiveNow() {
  if (!data) return;
  $('live-now-updated').textContent = `資料更新 ${fullTimestamp(data.updated_at)}`;
  const expandedQueues=new Set([...document.querySelectorAll('.court-next-matches[open]')].map(el=>el.dataset.court));
  const expandedOverflow=new Set([...document.querySelectorAll('.court-queue-overflow[open]')].map(el=>el.dataset.court));
  const cards = data.courts.map(court => {
    const {current,upcoming}=courtMatches(data.matches,court.id);
    const active=current[0];
    const card = node('article', null, 'live-now-card' + (active ? ' is-playing' : ''));
    const head = node('div', null, 'live-now-card-heading');
    head.append(node('h3', court.name), node('span', active ? '比賽中' : '目前未開賽', 'live-now-state'));
    card.append(head);
    if (active) {
      if(current.length>1)card.append(node('p','此場地有多場標記進行中，請向賽務確認。','notice'));
      for(const match of current){
        const division=data.divisions.find(row=>row.id===match.division_id);
        card.append(node('p',`${division?.name||''} · 第 ${match.code} 場`,'live-now-division'),liveScorePanel(match));
      }
    } else {
      const next = upcoming.find(match=>match.side_a.resolution==='confirmed'&&match.side_b.resolution==='confirmed');
      if (next) {const division = data.divisions.find(row => row.id === next.division_id);card.append(node('p', `待開賽・第 ${next.code} 場 · ${division?.name || ''}`, 'live-now-division'), node('p', `${next.side_a.name} 對 ${next.side_b.name}`, 'live-now-next'));}
      else card.append(node('p', '等待管理員確認下一場開賽。', 'live-now-idle'));
    }
    if(upcoming.length){
      const queue=node('details',null,'court-next-matches');queue.dataset.court=court.id;queue.open=expandedQueues.has(court.id);
      queue.append(node('summary',`待賽名單 · ${upcoming.length} 場`));
      const entry=match=>{const row=node('div',null,'court-queue-entry');row.append(node('p',`${match.called_at?'已叫號 · ':''}第 ${match.code} 場 · ${label('divisions',match.division_id)}`),node('strong',`${match.side_a.name} 對 ${match.side_b.name}`));return row;};
      for(const match of upcoming.slice(0,3))queue.append(entry(match));
      if(upcoming.length>3){const more=node('details',null,'court-queue-overflow');more.dataset.court=court.id;more.open=expandedOverflow.has(court.id);more.append(node('summary',`再看 ${upcoming.length-3} 場`));for(const match of upcoming.slice(3))more.append(entry(match));queue.append(more);}
      card.append(queue);
    }
    const scheduleLink=node('a','查看此場地賽程 →','court-schedule-link');
    scheduleLink.href=pageHref('schedule','?court='+encodeURIComponent(court.id));
    card.append(scheduleLink);
    return card;
  });
  $('live-now-courts').replaceChildren(...cards);
  const unassigned=data.matches.filter(m=>!m.court_id&&!m.result&&['planned','ready','in_progress'].includes(m.status)).length;
  if(unassigned){const note=node('p',`另有 ${unassigned} 場尚未安排場地，請至查詢賽程查看。`,'notice');$('live-now-courts').append(note);}
}

let liveScoreRecords = [], liveScoreOffline = false, liveScoreFetching = false;
function liveScorePanel(match) {
  const panel = node('section', null, 'live-score');
  panel.dataset.liveScoreMatch = match.id;
  panel.setAttribute('aria-label', `第 ${match.code} 場即時比分`);
  return panel;
}
function paintLiveScores() {
  if (!data) return;
  const matches = new Map(data.matches.map(match => [match.id, match]));
  for (const panel of document.querySelectorAll('[data-live-score-match]')) {
    const match = matches.get(panel.dataset.liveScoreMatch);
    if (!match) continue;
    const record = visibleLiveScore(match, liveScoreRecords);
    const signature = JSON.stringify([match.started_at,record?.version??null,liveScoreOffline]);
    if (panel.dataset.scoreSignature === signature) continue;
    panel.dataset.scoreSignature = signature;
    if (!record) { panel.replaceChildren(node('p',`${match.side_a.name} 對 ${match.side_b.name}`,'live-score-waiting'),node('p', liveScoreOffline ? '比分連線中斷，正在重新連線。' : '正在取得現場比分…', 'live-score-note')); continue; }
    const score = record.score, wins = {a:0,b:0};
    for (const game of score.games) wins[game.side_a > game.side_b ? 'a':'b']++;
    const current = score.winner ? score.games.at(-1) : score.current;
    const heading = node('div',null,'live-score-heading');
    heading.append(node('strong',score.winner ? '勝負待確認' : `第 ${score.games.length+1} 局`),node('span',`局數 ${wins.a}：${wins.b}`));
    const players = node('div',null,'live-score-players');
    for (const [side,name] of [['a',match.side_a.name],['b',match.side_b.name]]) {
      const player = node('div',null,'live-score-player');
      player.append(node('span',name),node('strong',String(current[side==='a'?'side_a':'side_b'])));
      players.append(player);
    }
    const games = node('p',score.games.map((game,i)=>`第 ${i+1} 局 ${game.side_a}：${game.side_b}`).join(' · '),'live-score-games');
    const note = node('p',liveScoreOffline ? '連線中斷・顯示最後收到的比分' : `比分更新 ${new Date(record.updated_at).toLocaleTimeString("zh-TW",{timeZone:"Asia/Taipei",hour12:false})} · 約 2 秒自動更新`,'live-score-note');
    panel.replaceChildren(heading,players,...(score.games.length?[games]:[]),note);
  }
}
async function refreshLiveScores() {
  if (liveScoreFetching || document.hidden) return;
  liveScoreFetching = true;
  try {
    const response = await fetch('/public/live-scores.json',{cache:'no-store',credentials:'omit',signal:AbortSignal.timeout(4000)});
    if (!response.ok) throw new Error('Live scores unavailable');
    const next = await response.json();
    if (typeof next.schedule_version !== 'string' || !/^[a-f0-9]{64}$/.test(next.schedule_version) || !Array.isArray(next.matches) || next.matches.length>1000 || next.matches.some(record=>!validLiveScore(record))) throw new Error('Invalid live scores');
    liveScoreRecords = next.matches;
    if (data && reader && next.schedule_version !== data.version) void reader.refresh();
    liveScoreOffline = false;
  } catch { liveScoreOffline = true; }
  finally { liveScoreFetching = false; paintLiveScores(); }
}
setInterval(()=>void refreshLiveScores(),2000);
window.addEventListener('online',()=>void refreshLiveScores());
document.addEventListener('visibilitychange',()=>{if(!document.hidden)void refreshLiveScores();});

let appliedPage=null;
function applyPageUi() {
  const info=pageInfo[page],live=page==='live',schedule=page==='schedule';
  document.body.dataset.page=page;
  $('hero-title').textContent=info.title;
  $('page-description').textContent=info.description;
  document.title=(data?.name??'壁球賽事')+' · '+info.title;
  document.querySelector('.live-now').hidden=!live;
  document.querySelector('.query-toolbar').hidden=!schedule;
  document.querySelector('.browse-controls').hidden=live;
  document.querySelector('.reader-layout').hidden=live;
  if(!schedule) $('personal-summary').hidden=true;
  document.querySelector('.skip-link').href=live?'#live-now-courts':schedule?'#search':'#division';
  for(const link of document.querySelectorAll('[data-page-link]')) {
    const target=link.dataset.pageLink;
    if(target===page)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
    link.href=pageHref(target,$('division').value?'?division='+encodeURIComponent($('division').value):'');
  }
  if(appliedPage!==page){appliedPage=page;arrangeMobileControls();}
}
applyPageUi();
