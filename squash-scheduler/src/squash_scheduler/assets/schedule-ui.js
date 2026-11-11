'use strict';
let scheduleDraft=new Map(),scheduleSelected=null,scheduleDraftBase=null,scheduleDraftWorkspace=null;
let scheduleSelection=new Set(),scheduleUndo=[],scheduleDay='',scheduleCourt='',scheduleMode='',scheduleScroll=0;
let scheduleStorageState=null,scheduleDraftReason='人工調整賽程';
const scheduleStorageWriter=globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random().toString(36).slice(2)}`;
const scheduleCompactMedia=matchMedia('(max-width:700px)');
scheduleCompactMedia.addEventListener('change',()=>{if(view==='schedule')render();});
function scheduleWorkspace(){return data.workspace_id||data.workspaces?.find(w=>w.active)?.id||data.workspace_label;}
function resetScheduleDraft({discard=true}={}){
  const state=scheduleStorageState;
  if(state?.active){if(discard)scheduleStorageClear(state);else scheduleStoragePersist();}
  scheduleDraft.clear();scheduleSelected=null;scheduleDraftBase=null;scheduleDraftWorkspace=null;scheduleUndo=[];scheduleSelection.clear();scheduleDraftReason='人工調整賽程';
  if(!discard)scheduleStorageState=null;
}
function scheduleDraftStale(){return scheduleDraft.size&&(scheduleDraftBase!==data.revision_id||scheduleDraftWorkspace!==scheduleWorkspace());}
function scheduleStorageError(state,error){
  state.error=error.message||'無法保存本機草案，請保留目前網頁。';state.lastRequested=null;
  if(error.code==='DRAFT_STORAGE_CONFLICT')state.conflict=true;
  scheduleStorageStatus();
}
function scheduleStorageSync(){
  if(!data)return;
  const workspace=data.workspace_id;
  if(!workspace||typeof ScheduleDraftStore==='undefined'){
    if(!scheduleStorageState)scheduleStorageState={workspace:null,error:'目前服務未提供安全的工作區識別，草案僅保留在此頁。'};
    return;
  }
  if(scheduleStorageState?.workspace!==workspace){
    if(scheduleDraft.size){scheduleStoragePersist();scheduleDraft.clear();scheduleDraftBase=null;scheduleDraftWorkspace=null;scheduleUndo=[];scheduleSelection.clear();scheduleDraftReason='人工調整賽程';}
    const state={workspace,store:null,pendingRecord:null,error:null,invalid:false,conflict:false,active:false,lastRequested:null,savedAt:null,busy:0};
    scheduleStorageState=state;
    try{state.store=new ScheduleDraftStore({workspaceId:workspace,storage:localStorage,locks:navigator.locks,writerId:scheduleStorageWriter});state.pendingRecord=state.store.read();state.savedAt=state.pendingRecord?.saved_at||null;}
    catch(error){state.invalid=error.code==='DRAFT_STORAGE_INVALID';scheduleStorageError(state,error.code?error:new Error('瀏覽器無法使用本機儲存；草案僅保留在目前網頁。'));}
    return;
  }
  const state=scheduleStorageState;
  if(!state.store)return;
  try{
    if(state.store.peek()!==state.store.raw){
      if(scheduleDraft.size){state.conflict=true;state.error='其他分頁已更新本機草案，已停止自動覆寫；本頁編輯仍保留。';}
      else{state.pendingRecord=state.store.read();state.savedAt=state.pendingRecord?.saved_at||null;state.invalid=false;state.error=null;state.conflict=false;state.active=false;state.lastRequested=null;}
    }
  }catch(error){state.invalid=error.code==='DRAFT_STORAGE_INVALID';scheduleStorageError(state,error);}
}
function scheduleStorageText(){
  const s=scheduleStorageState;if(!s)return '草案尚未保存。';
  if(s.error)return s.error;
  if(s.busy)return '正在保存本機草案，請稍候再關閉網頁。';
  if(s.pendingRecord)return `找到本機保存的 ${s.pendingRecord.updates.length} 場草案，尚未恢復到本頁。`;
  if(scheduleDraft.size&&s.savedAt)return `已自動保存於本機 ${localTime(s.savedAt)}；尚未正式儲存賽程。`;
  return '草案會自動保存在此瀏覽器；確認套用後才會更新正式賽程。';
}
function scheduleStorageStatus(){
  const el=document.getElementById('schedule-storage-status');if(el)el.textContent=scheduleStorageText();
}
function scheduleStorageRefresh(state){if(state===scheduleStorageState&&typeof view!=='undefined'&&view==='schedule')render();}
function scheduleStoragePersist(){
  const s=scheduleStorageState;
  if(!s?.store||!s.active||s.pendingRecord||s.invalid)return;
  if(!scheduleDraft.size){if(!s.conflict){if(s.store.raw!==null)scheduleStorageClear(s);else{s.active=false;s.error=null;s.lastRequested=null;}}return;}
  if(s.conflict||s.error)return;
  const updates=[...scheduleDraft.values()].map(u=>({...u})),signature=JSON.stringify([scheduleDraftBase,updates,scheduleDraftReason]);
  if(signature===s.lastRequested)return;s.lastRequested=signature;s.busy++;scheduleStorageStatus();
  let task;
  try{task=s.store.save({baseRevisionId:scheduleDraftBase,updates,reason:scheduleDraftReason});}
  catch(error){s.busy--;scheduleStorageError(s,error);scheduleStorageRefresh(s);return;}
  task.then(saved=>{if(saved&&s.store.record)s.savedAt=s.store.record.saved_at;}).catch(error=>{scheduleStorageError(s,error);scheduleStorageRefresh(s);}).finally(()=>{s.busy--;scheduleStorageStatus();});
}
function scheduleStorageClear(state=scheduleStorageState){
  if(!state?.store)return Promise.resolve();
  state.busy++;state.lastRequested=null;scheduleStorageStatus();
  return state.store.clear().then(()=>{state.pendingRecord=null;state.savedAt=null;state.error=null;state.invalid=false;state.conflict=false;state.active=state===scheduleStorageState&&scheduleDraft.size>0;})
    .catch(error=>scheduleStorageError(state,error)).finally(()=>{state.busy--;scheduleStorageStatus();scheduleStorageRefresh(state);});
}
function scheduleStorageCanRestore(record){
  if(!record||record.workspace_id!==data.workspace_id||record.base_revision_id!==data.revision_id)return false;
  return record.updates.every(u=>{
    const m=data.matches.find(m=>m.id===u.match_id),p=m&&scheduleOriginal(m),locks=m?.assignment?.locks||{};
    return m?.schedule_editable&&(data.event_dates||[]).includes(u.local_start.slice(0,10))&&data.courts.some(c=>c.id===u.court_id)&&
      (!(locks.time||locks.start_at)||p?.local_start===u.local_start)&&(!(locks.court||locks.court_id)||p?.court_id===u.court_id);
  });
}
function scheduleStorageRestore(){
  const s=scheduleStorageState,r=s?.pendingRecord;if(!scheduleStorageCanRestore(r)||scheduleDraft.size)return;
  try{if(s.store.peek()!==s.store.raw){scheduleStorageSync();render();return;}}catch(error){scheduleStorageError(s,error);render();return;}
  scheduleDraft=new Map(r.updates.map(u=>[u.match_id,{...u}]));scheduleDraftBase=r.base_revision_id;scheduleDraftWorkspace=r.workspace_id;
  scheduleDraftReason=r.reason;$('reason').value=r.reason;scheduleUndo=[];scheduleSelection.clear();s.pendingRecord=null;s.active=true;s.error=null;s.conflict=false;s.lastRequested=null;
  message(`已恢復 ${scheduleDraft.size} 場草案，請檢查變更後再確認。`);scheduleRepaint();
}
function scheduleStorageInspect(){
  const s=scheduleStorageState;let record=s?.pendingRecord||s?.store?.record;
  if(s?.conflict){try{record=ScheduleDraftStore.decode(s.store.peek(),s.workspace);}catch{record=null;}}
  if(!record){message('目前沒有可閱讀的已保存草案；格式損壞時請丟棄後重建。',true);return;}
  const dialog=scheduleDialog('已保存的賽程草案');
  dialog.append(node('p',`保存時間：${localTime(record.saved_at)}`,'muted'),node('p','來源版本：'+record.base_revision_id,'revision-label'),node('p',record.base_revision_id===data.revision_id?'此草案尚未套用，恢復後仍須檢查並確認。':'來源版本已變更，這份草案只能檢視或丟棄；不可直接套用。','notice'),node('p','異動原因：'+record.reason));
  for(const u of record.updates){const m=data.matches.find(m=>m.id===u.match_id),row=node('article',null,'schedule-change');row.append(node('h3',m?`第 ${m.code} 場 · ${m.division}`:'來源場次已不在目前版本'),node('p','草案安排：'+scheduleDescribe(u)));dialog.append(row);}dialog.showModal();
}
function scheduleStorageTakeOver(){
  const s=scheduleStorageState;if(!s?.store)return;
  try{s.store.read();s.pendingRecord=null;s.error=null;s.invalid=false;s.conflict=false;s.active=true;s.lastRequested=null;scheduleStoragePersist();render();}
  catch(error){scheduleStorageError(s,error);render();}
}
function scheduleStoragePanel(root){
  const s=scheduleStorageState,area=node('section',null,'schedule-storage'),status=node('p',scheduleStorageText());status.id='schedule-storage-status';status.setAttribute('role','status');area.append(status);
  const actions=node('div',null,'schedule-storage-actions');
  if(s?.pendingRecord&&!scheduleDraft.size){
    const stale=s.pendingRecord.base_revision_id!==data.revision_id,valid=scheduleStorageCanRestore(s.pendingRecord);
    area.append(node('p',stale?'來源版本已更新，已保存草案不可直接恢復。':valid?`保存時間：${localTime(s.pendingRecord.saved_at)}`:'已保存草案與目前場次或鎖定狀態不相容，無法恢復。','muted'));
    const restore=button('恢復已保存草案',scheduleStorageRestore);restore.id='schedule-draft-restore';restore.disabled=!valid;actions.append(restore,button('查看已保存草案',scheduleStorageInspect),button('丟棄已保存草案',()=>scheduleStorageClear()));
  }else if(s?.invalid&&!scheduleDraft.size){actions.append(button('丟棄損壞的草案',()=>scheduleStorageClear()));}
  else if(s?.conflict&&scheduleDraft.size){actions.append(button('查看其他分頁保存的草案',scheduleStorageInspect),button('以本頁草案取代保存',scheduleStorageTakeOver));}
  else if(s?.error&&s?.store&&scheduleDraft.size){actions.append(button('重試保存草案',()=>{s.error=null;s.lastRequested=null;scheduleStoragePersist();render();}));}
  if(actions.children.length)area.append(actions);root.append(area);
}
window.addEventListener('beforeunload',event=>{if(scheduleDraft.size){event.preventDefault();event.returnValue='';}});
window.addEventListener('storage',event=>{const s=scheduleStorageState;if(s?.store&&event.key===s.store.key){scheduleStorageSync();scheduleStorageRefresh(s);}});
function eventClock(value){return value?new Intl.DateTimeFormat('en-GB',{timeZone:data.timezone,hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(value)):'';}
function scheduleOriginal(m){return m?.assignment?{match_id:m.id,local_start:dayOf(m.time)+'T'+eventClock(m.time),court_id:m.assignment.court_id}:null;}
function scheduleEffective(m){return scheduleDraft.get(m.id)||scheduleOriginal(m);}
function scheduleDescribe(p){return p?`${p.local_start.replace('T',' ')} · ${data.courts.find(c=>c.id===p.court_id)?.label||p.court_id}`:'未排程';}
function scheduleRepaint(){scheduleStoragePersist();scheduleScroll=document.querySelector('.schedule-canvas')?.scrollTop||0;render();}
function scheduleTransaction(updates){
  scheduleStorageSync();
  if(scheduleStorageState?.pendingRecord||scheduleStorageState?.invalid){message('請先恢復或丟棄本機保存的草案，再開始修改。',true);return false;}
  if(scheduleDraftStale()){message('來源版本已更新，請清空草案後依新版本調整。',true);return false;}
  const next=new Map(scheduleDraft);
  for(const u of updates){
    const m=data.matches.find(m=>m.id===u.match_id),original=m&&scheduleOriginal(m),locks=m?.assignment?.locks||{};
    if(!m?.schedule_editable){message('選取包含已開賽、已結束或不可調整的場次，這次修改未加入草案。',true);return false;}
    if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(u.local_start)||!(data.event_dates||[]).includes(u.local_start.slice(0,10))||!data.courts.some(c=>c.id===u.court_id)){
      message('請使用既定賽期、有效的時間與場地；不能增加比賽天數。',true);return false;
    }
    if(((locks.time||locks.start_at)&&u.local_start!==original?.local_start)||((locks.court||locks.court_id)&&u.court_id!==original?.court_id)){
      message(`第 ${m.code} 場的時間或場地已鎖定，整批修改未加入草案。`,true);return false;
    }
    if(original&&u.local_start===original.local_start&&u.court_id===original.court_id)next.delete(m.id);else next.set(m.id,{...u});
  }
  if(JSON.stringify([...next])===JSON.stringify([...scheduleDraft]))return true;
  scheduleUndo.push({draft:[...scheduleDraft],base:scheduleDraftBase,workspace:scheduleDraftWorkspace});
  if(scheduleUndo.length>30)scheduleUndo.shift();
  if(!scheduleDraft.size&&next.size){scheduleDraftBase=data.revision_id;scheduleDraftWorkspace=scheduleWorkspace();}
  if(scheduleStorageState?.store)scheduleStorageState.active=true;
  scheduleDraft=next;if(!next.size){scheduleDraftBase=null;scheduleDraftWorkspace=null;}
  message('已更新待套用變更；檢查並確認後才會儲存。');scheduleRepaint();return true;
}
function scheduleUndoLast(){const prev=scheduleUndo.pop();if(!prev)return;scheduleDraft=new Map(prev.draft);scheduleDraftBase=prev.base;scheduleDraftWorkspace=prev.workspace;const s=scheduleStorageState;if(s?.store&&scheduleDraft.size){s.active=true;if(s.pendingRecord){s.pendingRecord=null;s.conflict=true;s.error='其他分頁已更新本機草案，已停止自動覆寫；本頁編輯仍保留。';}}scheduleRepaint();message('已復原上一步草案修改。');}
function scheduleShift(minutes){
  if(!Number.isInteger(minutes)||!minutes||Math.abs(minutes)>1440){message('請輸入 -1440 至 1440 之間的非零整數分鐘，例如 15 或 -15。',true);return;}
  const updates=[];
  for(const id of scheduleSelection){const m=data.matches.find(m=>m.id===id),p=m&&scheduleEffective(m);if(!p){message('未排程場次請先指定日期與時間，再平移。',true);return;}
    const d=new Date(p.local_start+':00Z');d.setUTCMinutes(d.getUTCMinutes()+minutes);updates.push({...p,local_start:d.toISOString().slice(0,16)});}
  scheduleTransaction(updates);
}
function scheduleSwap(){
  const matches=[...scheduleSelection].map(id=>data.matches.find(m=>m.id===id));
  if(matches.length!==2)return;if(matches.some(m=>!m)){message('來源場次已變更，請重新選取要互換的場次。',true);return;}const [a,b]=matches.map(scheduleEffective);
  if(!a||!b){message('兩場都需要已有日期、時間與場地才能互換。',true);return;}
  scheduleTransaction([{...b,match_id:matches[0].id},{...a,match_id:matches[1].id}]);
}
function scheduleDialog(title,cls=''){
  const dialog=node('dialog',null,'schedule-dialog '+cls),heading=node('div',null,'dialog-heading');dialog.setAttribute('aria-label',title);
  heading.append(node('h2',title),button('完成',()=>dialog.close()));dialog.append(heading);document.body.append(dialog);
  dialog.addEventListener('close',()=>{dialog.remove();scheduleSelected=null;});return dialog;
}
function scheduleEdit(m){
  scheduleSelected=m.id;const p=scheduleEffective(m),locks=m.assignment?.locks||{},dialog=scheduleDialog(`第 ${m.code} 場 · 調整`,'schedule-editor');
  dialog.append(node('p',`${m.side_a} 對 ${m.side_b}`,'schedule-editor-players'),node('p',m.division,'muted'),node('p','原安排：'+scheduleDescribe(scheduleOriginal(m)),'muted'));
  const date=select((data.event_dates||[]).map(d=>[d,d]),p?.local_start.slice(0,10)||scheduleDay||data.event_dates?.[0]);date.id='manual-date';date.disabled=!!(locks.time||locks.start_at);
  const time=input('time',p?.local_start.slice(11)||(data.timing_defaults?.daily_grid_anchors||[]).find(a=>a.date===date.value)?.local_time?.slice(0,5)||'09:00');time.id='manual-time';time.step=60;time.disabled=date.disabled;
  const court=select(data.courts.map(c=>[c.id,c.label]),p?.court_id);court.id='manual-court';court.disabled=!!(locks.court||locks.court_id);
  dialog.append(field('比賽日期',date),field(`開賽時間（${data.timezone}）`,time),field('場地',court));
  const status=node('p','修改欄位後會自動加入待套用變更。','schedule-editor-status');status.setAttribute('role','status');dialog.append(status);
  const update=()=>{if(date.value&&time.value&&court.value){const ok=scheduleTransaction([{match_id:m.id,local_start:date.value+'T'+time.value,court_id:court.value}]);status.textContent=ok?'已加入草案；完成編輯後請檢查變更。':'未加入草案，請查看畫面上方的錯誤訊息。';}};
  for(const control of [date,time,court])control.addEventListener('change',update);
  if(locks.time||locks.start_at||locks.court||locks.court_id)dialog.append(node('p','已鎖定欄位不可更動。','muted'));
  dialog.append(button('還原此場原安排',()=>{const original=scheduleOriginal(m);if(original&&scheduleTransaction([original]))dialog.close();}));dialog.showModal();
}
function scheduleBatchDialog(){
  const ids=[...scheduleSelection],dialog=scheduleDialog(`批次調整 ${ids.length} 場`);
  dialog.append(node('p','只更動勾選的欄位，其他安排保留。相同場地與時間可能撞場，套用前會完整檢查。','muted'));
  const date=select([['','保留各場日期'],...(data.event_dates||[]).map(d=>[d,d])]);date.id='batch-date';
  const court=select([['','保留各場場地'],...data.courts.map(c=>[c.id,c.label])]);court.id='batch-court';
  dialog.append(field('比賽日期',date),field('場地',court));
  const status=node('p','','schedule-editor-status');status.setAttribute('role','status');dialog.append(status);
  const apply=button('加入待套用變更',()=>{if(!date.value&&!court.value){status.textContent='請先選擇要更動的日期或場地。';return;}const updates=[];
    for(const id of ids){const m=data.matches.find(m=>m.id===id),p=m&&scheduleEffective(m);if(!p){message('選取中有未排程場次，請先逐場指定開賽時間。',true);return;}updates.push({...p,local_start:(date.value||p.local_start.slice(0,10))+p.local_start.slice(10),court_id:court.value||p.court_id});}
    if(scheduleTransaction(updates))dialog.close();else status.textContent=$('message').textContent;},'primary');apply.id='batch-apply';dialog.append(apply);dialog.showModal();
}
function scheduleDraftDialog(){
  const dialog=scheduleDialog('待套用變更');
  const reason=node('textarea');reason.id='manual-reason';reason.rows=2;reason.maxLength=2000;reason.value=scheduleDraftReason;$('reason').value=reason.value;reason.addEventListener('input',()=>{scheduleDraftReason=reason.value;$('reason').value=reason.value;scheduleStoragePersist();});dialog.append(field('異動原因',reason));
  for(const u of scheduleDraft.values()){const m=data.matches.find(m=>m.id===u.match_id),row=node('article',null,'schedule-change');row.append(node('h3',m?`第 ${m.code} 場 · ${m.division}`:'來源場次已不在目前版本'),node('p',m?`${m.side_a} 對 ${m.side_b}`:'請依目前版本重新建立安排。'),node('p','原：'+scheduleDescribe(scheduleOriginal(m)),'muted'),node('p','新：'+scheduleDescribe(u)));const remove=button('移除此場變更',()=>{const original=scheduleOriginal(m);if(original)scheduleTransaction([original]);else{scheduleUndo.push({draft:[...scheduleDraft],base:scheduleDraftBase,workspace:scheduleDraftWorkspace});scheduleDraft.delete(u.match_id);if(!scheduleDraft.size){scheduleDraftBase=null;scheduleDraftWorkspace=null;}scheduleRepaint();}dialog.close();});row.append(remove);dialog.append(row);}
  dialog.append(button('清空所有草案變更',()=>{scheduleUndo.push({draft:[...scheduleDraft],base:scheduleDraftBase,workspace:scheduleDraftWorkspace});scheduleDraft.clear();scheduleDraftBase=null;scheduleDraftWorkspace=null;scheduleRepaint();dialog.close();}));dialog.showModal();
}
function scheduleCard(m){
  const p=scheduleEffective(m),card=node('article',null,'schedule-slot'+(scheduleSelection.has(m.id)?' is-selected':'')+(scheduleDraft.has(m.id)?' is-changed':''));card.dataset.matchId=m.id;
  const check=input('checkbox');check.checked=scheduleSelection.has(m.id);check.disabled=!m.schedule_editable;check.setAttribute('aria-label',`選取第 ${m.code} 場`);check.dataset.matchId=m.id;check.className='schedule-check';
  check.addEventListener('change',()=>{if(check.checked)scheduleSelection.add(m.id);else scheduleSelection.delete(m.id);scheduleRepaint();document.querySelector(`.schedule-check[data-match-id="${CSS.escape(m.id)}"]`)?.focus({preventScroll:true});});
  const edit=button('',()=>scheduleEdit(m),'schedule-edit');edit.dataset.matchId=m.id;edit.disabled=!m.schedule_editable;
  edit.append(node('span',`${p?.local_start.slice(11)||'未排程'} · ${m.code}`,'slot-time'),node('span',m.side_a+' 對 '+m.side_b,'slot-players'),node('span',m.division,'slot-division'));
  const pick=node('label',null,'schedule-pick');pick.append(check);card.append(pick,edit);if(scheduleDraft.has(m.id))card.append(node('span','待套用','slot-change'));else if(!m.schedule_editable)card.append(node('span',statuses[m.status]||'已鎖定','slot-change'));
  card.title=(scheduleDraft.has(m.id)?'原：'+scheduleDescribe(scheduleOriginal(m))+'\n新：':'')+scheduleDescribe(p);return card;
}
function scheduleView(){
  scheduleStorageSync();
  const root=node('div',null,'schedule-workspace'),policy=data.player_workload||{};
  if(!scheduleMode)scheduleMode=matchMedia('(max-width:700px)').matches?'list':'board';
  if(scheduleDay!=='unscheduled'&&!(data.event_dates||[]).includes(scheduleDay))scheduleDay=data.event_dates?.[0]||'';
  $('reason').value=scheduleDraftReason;
  scheduleStoragePanel(root);
  const tools=node('div',null,'schedule-tools'),dates=node('div',null,'schedule-tabs');dates.setAttribute('aria-label','比賽日期');
  for(const day of data.event_dates||[]){const b=button(day.slice(5).replace('-','/'),()=>{scheduleDay=day;scheduleScroll=0;render();});b.setAttribute('aria-pressed',String(day===scheduleDay));dates.append(b);}
  const unscheduled=button('未排程',()=>{scheduleDay='unscheduled';scheduleScroll=0;render();});unscheduled.setAttribute('aria-pressed',String(scheduleDay==='unscheduled'));dates.append(unscheduled);
  // Unscheduled is a view, never an extra event date.
  const court=select([['','全部場地'],...data.courts.map(c=>[c.id,c.label])],scheduleCourt);court.setAttribute('aria-label','篩選場地');court.addEventListener('change',()=>{scheduleCourt=court.value;scheduleScroll=0;render();});
  const mode=button(scheduleMode==='board'?'改用清單':'場地時間表',()=>{scheduleMode=scheduleMode==='board'?'list':'board';scheduleScroll=0;render();});mode.id='schedule-mode';tools.append(dates,court,mode);root.append(tools);
  if(scheduleDraftStale())root.append(node('p','來源版本已更新；目前草案不能套用。請先查看變更，再清空並依新版本調整。','notice'));
  const matches=visibleMatches().filter(m=>{const p=scheduleEffective(m);return (scheduleDay==='unscheduled'?!p:p?.local_start.startsWith(scheduleDay))&&(!scheduleCourt||p?.court_id===scheduleCourt);}).sort((a,b)=>(scheduleEffective(a)?.local_start||'').localeCompare(scheduleEffective(b)?.local_start||'')||String(a.code).localeCompare(String(b.code),undefined,{numeric:true}));
  const selection=node('div',null,'schedule-selection'),all=input('checkbox'),editable=matches.filter(m=>m.schedule_editable);all.id='schedule-select-all';all.checked=!!editable.length&&editable.every(m=>scheduleSelection.has(m.id));all.indeterminate=editable.some(m=>scheduleSelection.has(m.id))&&!all.checked;all.disabled=!editable.length;
  all.addEventListener('change',()=>{for(const m of editable){if(all.checked)scheduleSelection.add(m.id);else scheduleSelection.delete(m.id);}scheduleRepaint();});
  const label=field(`選取此篩選 ${editable.length} 場`,all);label.className='schedule-select-label';selection.append(label,node('span',`顯示 ${matches.length} 場`,'muted'));
  if(scheduleSelection.size){selection.append(node('strong',`已選 ${scheduleSelection.size} 場`));const clear=button('取消選取',()=>{scheduleSelection.clear();scheduleRepaint();});selection.append(clear);}
  root.append(selection);
  if(scheduleSelection.size){const batch=node('div',null,'schedule-batch'),offset=input('number','15');offset.id='batch-minutes';offset.step=1;offset.setAttribute('aria-label','平移分鐘（負數提前）');batch.append(field('平移分鐘（負數提前）',offset));const shift=button('平移選取場次',()=>scheduleShift(Number(offset.value)));shift.id='batch-shift';batch.append(shift,button('改日期／場地',scheduleBatchDialog));const swap=button('互換兩場安排',scheduleSwap);swap.id='batch-swap';swap.disabled=scheduleSelection.size!==2;batch.append(swap);root.append(batch);}
  const canvas=node('div',null,'schedule-canvas');canvas.setAttribute('aria-label',scheduleMode==='board'?'場地時間表':'賽程清單');canvas.tabIndex=0;
  if(!matches.length)canvas.append(node('p','此篩選沒有場次。切換日期、場地或搜尋條件。','empty'));
  else if(scheduleMode==='list'||scheduleDay==='unscheduled'||matchMedia('(max-width:700px)').matches){
    canvas.classList.add('schedule-list');for(const m of matches){const card=scheduleCard(m);card.append(node('span',data.courts.find(c=>c.id===scheduleEffective(m)?.court_id)?.label||'未排程','slot-court'));canvas.append(card);}
  }else{
    const courts=data.courts.filter(c=>!scheduleCourt||c.id===scheduleCourt),minutes=p=>Number(p.local_start.slice(11,13))*60+Number(p.local_start.slice(14,16));
    const first=Math.floor(Math.min(...matches.map(m=>minutes(scheduleEffective(m))))/60)*60,last=Math.ceil(Math.max(...matches.map(m=>{const p=scheduleEffective(m),a=m.assignment;return minutes(p)+(a?Math.max(15,(new Date(a.court_release_at||a.play_end_at)-new Date(a.start_at))/60000):30);}))/60)*60;
    const scale=6,grid=node('div',null,'schedule-timeline');grid.style.gridTemplateColumns=`52px repeat(${courts.length},minmax(160px,1fr))`;
    grid.append(node('div','時間','timeline-head'));for(const c of courts)grid.append(node('div',c.label,'timeline-head'));
    const axis=node('div',null,'timeline-axis');axis.style.height=((last-first)*scale+24)+'px';for(let t=first;t<=last;t+=60){const tick=node('span',String(Math.floor(t/60)).padStart(2,'0')+':00');tick.style.top=((t-first)*scale)+'px';axis.append(tick);}grid.append(axis);
    for(const c of courts){const lane=node('div',null,'timeline-lane');lane.style.height=axis.style.height;for(let t=first;t<=last;t+=60){const line=node('div',null,'timeline-hour');line.style.top=((t-first)*scale)+'px';lane.append(line);}const items=matches.filter(m=>scheduleEffective(m).court_id===c.id),ends=[];
      for(const m of items){const p=scheduleEffective(m),start=minutes(p),a=m.assignment,duration=a?Math.max(15,(new Date(a.court_release_at||a.play_end_at)-new Date(a.start_at))/60000):30;let col=ends.findIndex(end=>end<=start);if(col<0)col=ends.length;ends[col]=start+duration;const card=scheduleCard(m);card.style.top=((start-first)*scale)+'px';card.style.height=(duration*scale-4)+'px';card.dataset.lane=String(col);lane.append(card);}
      const lanes=Math.max(1,ends.length);for(const card of lane.querySelectorAll('.schedule-slot')){card.style.width=`calc(${100/lanes}% - 4px)`;card.style.left=`calc(${Number(card.dataset.lane)*100/lanes}% + 2px)`;}if(lanes>1)lane.setAttribute('aria-label',c.label+' 有重疊安排，請檢查變更');grid.append(lane);
    }canvas.append(grid);
  }
  root.append(canvas);requestAnimationFrame(()=>{if(canvas.isConnected)canvas.scrollTop=scheduleScroll;});
  const floor=Math.max(policy.minimum_rest_minutes||0,data.timing_defaults?.minimum_player_rest_minutes||0);root.append(node('p',`${data.timezone} · 最少休息 ${floor} 分鐘 · 每人每日 ${policy.max_matches_per_day?`最多 ${policy.max_matches_per_day} 場`:'上限未設定'} · 修改後請檢查整份草案`,'schedule-policy'));
  const footer=node('div',null,'schedule-footer');footer.append(node('strong',`${scheduleDraft.size} 場待套用`));
  const undo=button('復原上一步',scheduleUndoLast);undo.id='schedule-undo';undo.disabled=!scheduleUndo.length;const inspect=button('查看變更',scheduleDraftDialog);inspect.id='schedule-draft-details';inspect.disabled=!scheduleDraft.size;
  const reset=button('清空草案',()=>{scheduleUndo.push({draft:[...scheduleDraft],base:scheduleDraftBase,workspace:scheduleDraftWorkspace});scheduleDraft.clear();scheduleDraftBase=null;scheduleDraftWorkspace=null;scheduleRepaint();});reset.disabled=!scheduleDraft.size;
  const review=button(`檢查 ${scheduleDraft.size} 場變更`,()=>preview({action:'manual_schedule',updates:[...scheduleDraft.values()]}),'primary');review.id='manual-preview';review.disabled=!scheduleDraft.size||!!scheduleDraftStale();footer.append(undo,inspect,reset,review);root.append(footer);
  const recent=node('details',null,'schedule-recent');recent.append(node('summary',`已套用的異動紀錄（${data.schedule_reviews?.length||0}）`));
  for(const record of data.schedule_reviews||[]){const details=node('details',null,'schedule-history');details.append(node('summary',`${localTime(record.created_at)} · ${record.schedule_review.changed_count} 場異動`));details.addEventListener('toggle',()=>{if(details.open&&!details.dataset.rendered){details.dataset.rendered='true';renderScheduleReview(record.schedule_review,details,{applied:true,revision:record.result_revision_id});}});recent.append(details);}root.append(recent);
  const automatic=button('預覽自動重排',()=>$('schedule').click());automatic.disabled=!!scheduleDraft.size;root.append(automatic);return root;
}

function schedulePoint(value){const root=node('div');if(!value){root.append(node('p','未排程'));return root;}root.append(node('p',value.date),node('p',`${value.time} · ${value.court}`));if(value.play_end_at)root.append(node('p',`預定打完 ${eventClock(value.play_end_at)}`,'muted'));return root;}
function scheduleAnnouncement(review,options={}){
  const lines=[`${review.event_name}｜賽程異動${options.applied?'公告':'草案'}`,`時區：${review.timezone}`,`來源版本：${review.base_revision_id}`];
  if(options.revision)lines.push(`套用版本：${options.revision}`);
  if(!options.applied)lines.push(review.can_apply?'尚未確認套用，請勿作為正式公告。':'草案仍有衝突，不能作為正式公告。');
  const describe=value=>value?`${value.date} ${value.time} ${value.court}`:'未排程';
  for(const row of review.changes)lines.push('',`第 ${row.code} 場｜${row.division}｜${row.players}`,`原：${describe(row.before)}`,`新：${describe(row.after)}`);
  return lines.join('\n');
}
function renderScheduleReview(review,root,options={}){
  const section=node('section',null,'schedule-review'),c=review.conflicts;
  section.append(node('p',`${review.changed_count} 場變更 · 衝突 ${c.before_count} → ${c.after_count}`,'schedule-review-summary'));
  section.append(node('p',`已排除 ${c.resolved.length} 項／新增 ${c.introduced.length} 項／仍存在 ${c.remaining.length} 項`,'muted'));
  if(!review.can_apply)section.append(node('p','草案尚有衝突，無法確認套用。可取消預覽並繼續調整。','notice'));
  const windows=review.preferred_windows;
  if(windows){
    section.append(node('p',`優先時段內 ${windows.assigned_count-windows.outside.length} 場，穿插安排 ${windows.outside.length} 場。`,'muted'));
    if(windows.outside.length){
      const details=node('details',null,'schedule-conflicts');
      details.append(node('summary','查看穿插場次'),node('p','優先時段可調整，場地、選手休息、每日場數及晉級順序仍須通過檢查。','muted'));
      details.append(table(['場次','組別','優先時段','排定時間'],windows.outside.map(row=>[
        row.code,row.division,row.preferred.join('、'),`${row.assigned.date} ${row.assigned.time} · ${row.assigned.court}`,
      ])));
      section.append(details);
    }
  }
  const labels={date:'日期',time:'時間',court:'場地',duration:'預定結束'};
  for(const row of review.changes){
    const card=node('article',null,'schedule-change');card.dataset.matchId=row.match_id;
    card.append(node('h3',`第 ${row.code} 場 · ${row.division}`),node('p',row.players,'muted'),node('p',`變更：${row.changed_fields.map(k=>labels[k]||k).join('、')}`,'schedule-fields'));
    const pair=node('div',null,'schedule-compare');for(const [label,value] of [['原排程',row.before],['新排程',row.after]]){const box=node('div');box.append(node('strong',label),schedulePoint(value));pair.append(box);}card.append(pair);section.append(card);
  }
  if(!review.changes.length)section.append(node('p','場次日期、時間與場地沒有變更。','empty'));
  for(const [key,title] of [['introduced','新增衝突'],['remaining','仍存在的衝突'],['resolved','已排除的衝突']]){
    if(!c[key].length)continue;const details=node('details',null,`schedule-conflicts ${key}`);details.open=key!=='resolved';details.append(node('summary',`${title}（${c[key].length}）`));
    for(const conflict of c[key]){const item=node('div',null,'schedule-conflict');item.append(node('strong',conflict.label));if(conflict.before_detail&&conflict.before_detail!==conflict.detail)item.append(node('p','原：'+conflict.before_detail,'muted'),node('p','新：'+conflict.detail,'muted'));else item.append(node('p',conflict.detail,'muted'));details.append(item);}section.append(details);
  }
  section.append(button(options.applied?'下載當次異動公告':'下載異動草案',()=>{const blob=new Blob([scheduleAnnouncement(review,options)],{type:'text/plain;charset=utf-8'}),url=URL.createObjectURL(blob),link=node('a');link.href=url;link.download=`schedule-${options.applied?'notice':'draft'}-${(options.revision||review.base_revision_id).slice(0,8)}.txt`;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}));root.append(section);
}
