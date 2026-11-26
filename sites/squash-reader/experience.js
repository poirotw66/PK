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
  return `${value.start ? day(value.start).slice(5).replace('-','/') + ' ' + time(value.start,false) : '時間待通知'}`;
}
function appendPersonalChange(target, change) {
  target.classList.add('has-personal-change');
  const note = node('div', null, 'personal-match-change');
  note.append(node('p', changeDescription(change), 'change-action'));
  if (change.after) {
    const destination = node('div', null, 'change-destination');
    const when = node('div');
    when.append(node('span', change.after.start ? `新時間 · ${day(change.after.start).slice(5).replace('-','/')}` : '新時間'), node('strong', change.after.start ? time(change.after.start,false) : '待通知'));
    destination.append(when);
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
  // The live court can differ from the scheduled assignment. It is not a schedule change.
  result.changes = result.changes.filter(change => !change.before || !change.after || change.before.start !== change.after.start)
    .map(change => ({ ...change, before: change.before && { ...change.before, court:null },
      after: change.after && { ...change.after, court:null } }));
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
  notice.append(node('h3', `${result.changes.length} 場${hasNewArrangement ? '比賽有新安排' : '個人賽程已調整'}`), node('p', hasNewArrangement ? '請確認下方場次，預定時間僅供參考。' : '以下場次已移出，請向賽務確認。'));
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
  show.addEventListener('click', () => { for (const id of ['division','date']) $(id).value = ''; view = 'schedule'; filterChanged(); });
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
  const search = stateSearch({player:selectedPlayer, search:$('search').value.trim(), date:$('date').value, division:$('division').value, view});
  page=pageForView(view);
  history.replaceState(null, '', pageHref(page,search));
  applyPageUi();
}

function restoreQueryState() {
  const state = queryState(location.search);
  selectedPlayer = state.player;
  $('search').value = state.player || state.search;
  let invalid = false;
  for (const id of ['date', 'division']) {
    const exists = [...$(id).options].some(option => option.value === state[id]);
    $(id).value = exists ? state[id] : '';
    invalid ||= !exists;
  }
  page=readerPage(location.pathname,location.search,location.hash);
  view=pageInfo[page].view;
  if (invalid) showShareFeedback('連結中的部分日期或組別已不存在，已移除失效條件。');
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
  $('division').value = '';
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
    $('division').value = '';
    view = 'schedule';
  }
  filterChanged();
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
  $('schedule-heading').textContent = page==='bracket'?'各組賽程':page==='results'?'完賽成績':selectedPlayer?'個人賽程':'預定時間';
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
  for (const id of ['search', 'division']) $(id).value = '';
  filterChanged();
}

function showShareFeedback(message, url = '') {
  $('share-feedback').hidden = false;
  $('share-message').textContent = message;
  $('share-link').hidden = !url;
  $('share-link').value = url;
}

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
  document.body.dataset.view = view;
  syncPersonalSchedule();
  const dates = [...new Set(data.matches.map(m => day(m.planned_start_at)).filter(Boolean))].sort();
  if(page==='bracket')$('date').value='';
  else if(!dates.includes($('date').value))$('date').value=preferredEventDate(dates,new Date(),data.timezone);
  const selected = $('date').value;
  const signature = JSON.stringify([dates, selected]);
  if ($('day-tabs').dataset.signature !== signature) {
    $('day-tabs').dataset.signature = signature;
    $('day-tabs').replaceChildren(...dates.map((date, index) => {
      const button = node('button', null, 'day-tab');
      button.type = 'button';
      button.setAttribute('aria-pressed', String(date === selected));
      button.append(node('span', `第 ${index + 1} 天`, 'day-caption'));
      button.append(node('strong', date.slice(5).replace('-', ' / ')));
      button.addEventListener('click', () => {
        $('date').value = date;
        filterChanged();
      });
      return button;
    }));
  }
  $('clear-search').hidden = !$('search').value;
  $('reset-filters').hidden = !['search', 'division'].some(id => $(id).value);
  const context=[];
  if(['bracket','standings'].includes(page)) {
    if($('search').value)context.push('選手：'+$('search').value);
    if($('date').value)context.push('日期：'+$('date').value);
  }
  $('query-context').hidden=!context.length;
  $('query-context').textContent=context.join(' · ');
  $('match-total').textContent = data.matches.length;
  if($('overview-matches'))$('overview-matches').textContent=data.matches.length;
  if($('overview-divisions'))$('overview-divisions').textContent=data.divisions.length;
  $('event-totals').textContent = `場比賽 · ${data.divisions.length} 個組別`;
}

$('clear-search').addEventListener('click', () => {
  selectedPlayer = '';
  $('search').value = '';
  filterChanged();
  $('search').focus();
});
$('reset-filters').addEventListener('click', resetFilters);

function chooseDivision(id) {
  selectedPlayer='';$('search').value='';$('division').value=id;view='schedule';filterChanged();
}
function scheduleTicket(match) {
  const ticket=node('article',null,'match-ticket');ticket.dataset.matchId=match.id;ticket.dataset.status=match.status;
  ticket.setAttribute('aria-label',`第 ${match.code} 場`);
  const head=node('div',null,'ticket-head');
  head.append(node('strong',`第 ${match.code} 場`));
  if(match.planned_start_at){const planned=node('time',`預定 ${time(match.planned_start_at,false)}`,'ticket-planned');planned.dateTime=match.planned_start_at;head.append(planned);}
  const competition=node('p',null,'ticket-competition'),division=node('button',label('divisions',match.division_id),'match-filter-link');
  division.type='button';division.setAttribute('aria-label',`篩選${label('divisions',match.division_id)}`);division.addEventListener('click',()=>chooseDivision(match.division_id));
  competition.append(division,node('span',label('stages',match.stage_id)));
  const players=node('div',null,'ticket-players');
  for(const [key,side]of[['a',match.side_a],['b',match.side_b]]){
    const player=node('div',null,'ticket-player'),name=node(side.entry_id&&side.resolution==='confirmed'?'button':'span',side.name,side.name===selectedPlayer?'player-name match-filter-link is-selected-player':'player-name match-filter-link');
    if(name.tagName==='BUTTON'){name.type='button';name.setAttribute('aria-label',`篩選選手${side.name}`);name.addEventListener('click',()=>choosePlayer(side.name));}
    if(match.result?.winner_entry_id===side.entry_id)player.className+=' is-winner';
    player.append(name);
    if(match.result?.games.length&&(match.result.outcome??'played')==='played'){const wins=match.result.games.filter(game=>key==='a'?game.side_a>game.side_b:game.side_b>game.side_a).length;player.append(node('strong',String(wins),'player-score'));}
    players.append(player);
  }
  const foot=node('div',null,'ticket-foot');foot.append(node('span',match.result?resultText(match):state(match),'ticket-status'));
  ticket.append(head,competition,players,foot);
  if(match.result?.games.length)ticket.append(node('p',match.result.games.map(game=>`${game.side_a}:${game.side_b}`).join(' · '),'ticket-scoreline'));
  if(personalChanges.has(match.id))appendPersonalChange(ticket,personalChanges.get(match.id));
  return ticket;
}
function schedule(matches) {
  const root=node('section',null,'schedule-board');
  const courtOrder=new Map(data.courts.map(court=>court.id).sort((a,b)=>label('courts',a).localeCompare(label('courts',b),undefined,{numeric:true})).map((id,index)=>[id,index]));
  let previousDay=null;
  for(const row of compactScheduleRows(matches,courtOrder,data.timezone)){
    if(row.date!==previousDay){root.append(navigationTarget(node('h2',row.date,'schedule-day-heading'),`day-${row.date}`,'賽程日期',row.date));previousDay=row.date;}
    const slot=node('section',null,'time-slot compact-time-row'),rail=node('div',null,'slot-clock');
    rail.setAttribute('aria-label',row.hours.length?row.hours.join('、'):'同一時段');
    for(const hour of row.hours)rail.append(node('span',hour));
    const grid=node('div',null,'ticket-grid');row.matches.forEach(match=>grid.append(scheduleTicket(match)));slot.append(rail,grid);root.append(slot);
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


$('search').addEventListener('keydown', event => {
  if (event.key !== 'Enter' || event.isComposing || !matchMedia('(max-width:640px)').matches) return;
  event.preventDefault();
  $('search').blur();
  if (selectedPlayer) focusPersonalSummary();
  else $('schedule-heading').scrollIntoView({block:'start', behavior:'instant'});
});


function applyPageUi() {
  const info=pageInfo[page],schedule=page==='schedule',browse=schedule||page==='results';
  document.body.dataset.page=page;
  $('hero-title').textContent=info.title;
  $('schedule-heading').textContent=page==='results'?'完賽成績':page==='bracket'?'各組賽程':'預定時間';
  $('page-description').textContent=info.description;
  document.title=(data?.name??'壁球賽事')+' · '+info.title;
  document.querySelector('.query-toolbar').hidden=!browse;
  document.querySelector('.browse-controls').hidden=false;
  document.querySelector('.reader-layout').hidden=false;
  if(!schedule) $('personal-summary').hidden=true;
  document.querySelector('.skip-link').href=browse?'#search':'#division';
  for(const link of document.querySelectorAll('[data-page-link]')) {
    const target=link.dataset.pageLink;
    if(target===page)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
    link.href=pageHref(target,$('division').value?'?division='+encodeURIComponent($('division').value):'');
  }
  $('filter-home').hidden=page!=='bracket';
}
applyPageUi();
