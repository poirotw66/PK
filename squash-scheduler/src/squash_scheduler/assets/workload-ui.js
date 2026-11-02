'use strict';
let workloadMode='requested',workloadDay='',workloadOnlyProblems=true,workloadBasis='all',workloadPerson=null,workloadLimit=40;
let workloadPending=null,workloadError=null;
let workloadDetailOpen=false,workloadFilterOpen=false;
const workloadCompactMedia=matchMedia('(max-width:700px)');
workloadCompactMedia.addEventListener('change',()=>{if(view==='workload')render();});
const workloadReports=new Map();
function workloadKey(){return [data.workspace_id,data.revision_id,data.content_hash,workloadMode].join(':');}
async function loadWorkload(force=false){
  const key=workloadKey();if(workloadPending===key)return;
  if(force)workloadReports.delete(key);workloadError=null;workloadPending=key;
  const query=new URLSearchParams({mode:workloadMode,revision_id:data.revision_id,workspace_id:data.workspace_id});
  try{
    const report=await api('/api/workload?'+query);
    if(report.workspace_id!==data.workspace_id||report.source_revision_id!==data.revision_id||report.snapshot_content_hash!==data.content_hash)return;
    workloadReports.set(key,report);while(workloadReports.size>4)workloadReports.delete(workloadReports.keys().next().value);
  }catch(error){if(key===workloadKey())workloadError={key,message:error.message};}
  finally{if(workloadPending===key)workloadPending=null;if(view==='workload'&&key===workloadKey())render();}
}
function workloadConflicts(report,person){return (person?person.conflicts:report.conflicts||[]).filter(c=>(!workloadDay||c.date===workloadDay)&&(workloadBasis==='all'||c.basis===workloadBasis));}
function workloadMatches(ids){return [...new Set(ids)].map(id=>data.matches.find(m=>m.id===id)).filter(Boolean);}
function openWorkloadSchedule(ids){
  const report=workloadReports.get(workloadKey());
  if(!report||report.source_revision_id!==data.revision_id){message('請重新整理負荷分析，再選取問題場次。',true);return;}
  const all=workloadMatches(ids),editable=all.filter(m=>m.schedule_editable);
  if(!editable.length){message('相關場次已開賽、結束或鎖定，無法直接移動；請先查看場次狀態。',true);return;}
  scheduleSelection=new Set(editable.map(m=>m.id));
  const first=editable[0],point=scheduleEffective(first);
  scheduleDay=point?point.local_start.slice(0,10):'unscheduled';scheduleCourt='';scheduleMode='list';scheduleScroll=0;
  $('search').value='';$('division').value='';
  go('schedule');if(view==='schedule')render();
  message(`已選取 ${editable.length} 場，可一起調整。${all.length>editable.length?`另有 ${all.length-editable.length} 場不可移動。`:''}跨日選取仍保留，調整後請檢查整份草案。`);
  requestAnimationFrame(()=>document.querySelector(`.schedule-slot[data-match-id="${CSS.escape(first.id)}"]`)?.scrollIntoView({block:'center'}));
}
function workloadAdjustButton(ids,label='選取相關場次調整'){
  const matches=workloadMatches(ids),editable=matches.filter(m=>m.schedule_editable),b=button(label,()=>openWorkloadSchedule(ids));
  b.className='workload-adjust';b.disabled=!editable.length;
  if(!editable.length)b.title='相關場次已開賽、結束、鎖定或尚未產生。';
  return b;
}
function workloadMatchLine(m){
  const row=node('div',null,'workload-match');row.dataset.matchId=m.match_id;
  const identity=node('div');identity.append(node('strong',`第 ${m.code||m.match_id.slice(0,8)} 場`),node('span',m.division,'muted'));
  const court=m.court||data.courts.find(c=>c.id===m.court_id)?.label||'場地待定';
  row.append(identity,node('p',`${eventClock(m.start)}–${eventClock(m.play_end)} · ${court}`));
  const match=data.matches.find(x=>x.id===m.match_id);
  if(match)row.append(node('p',`${match.side_a} 對 ${match.side_b}`,'muted'));
  row.append(workloadAdjustButton([m.match_id],'前往調整'));return row;
}
function workloadPersonDetail(report,person){
  const detail=panel(person.name,person.divisions.map(d=>d.name).join('、'));detail.classList.add('workload-detail');detail.dataset.participantId=person.participant_id;
  detail.prepend(button('返回選手清單',()=>{workloadDetailOpen=false;render();requestAnimationFrame(()=>document.querySelector('.workload-people')?.scrollIntoView({block:'start'}));},'workload-back'));
  const conflicts=workloadConflicts(report,person);
  const summary=node('div',null,'workload-person-summary');summary.append(node('p',`${conflicts.filter(c=>c.basis==='known').length} 項已確定問題 · ${conflicts.filter(c=>c.basis==='possible').length} 項可能晉級問題`));detail.append(summary);
  const problems=node('section',null,'workload-problems');problems.append(node('h3','需要處理的場次'));
  if(!conflicts.length)problems.append(node('p','目前篩選下，已排場次未發現選手負荷問題。','muted'));
  for(const c of conflicts){
    const item=node('article',null,'workload-conflict');item.dataset.conflictId=c.id;
    item.append(node('strong',`${c.basis==='known'?'已確定':'可能晉級'} · ${c.label}`),node('p',c.detail),workloadAdjustButton(c.match_ids));problems.append(item);
  }detail.append(problems);
  for(const day of person.daily.filter(d=>!workloadDay||d.date===workloadDay)){
    const section=node('section',null,'workload-daily');section.dataset.date=day.date;
    section.append(node('h3',day.date),node('p',`已確定 ${day.known_count} 場 · 相容晉級路徑最多 ${day.maximum_path_count??'待分析'} 場`,day.cap_exceeded?'workload-warning':''));
    const gaps=(person.known_gaps||[]).filter(g=>g.date===day.date),shortest=gaps.length?Math.min(...gaps.map(g=>g.gap_minutes)):null;
    section.append(node('p',shortest===null?'尚無兩場已確定出賽可計算休息。':`已確定出賽的最短預定休息 ${Number(shortest.toFixed(1))} 分鐘`,'muted'));
    if(day.known_matches.length)day.known_matches.forEach(m=>section.append(workloadMatchLine(m)));else section.append(node('p','此日沒有已確定的出賽場次。','muted'));
    const knownIds=new Set(day.known_matches.map(m=>m.match_id)),potential=day.candidate_matches.filter(m=>!knownIds.has(m.match_id));
    if(potential.length){const possibilities=node('details');possibilities.append(node('summary',`查看 ${potential.length} 場可能對戰`),node('p','以下包含不同晉級情境，不代表每場都會參加；每日上界已排除互斥分支。','muted'));potential.forEach(m=>possibilities.append(workloadMatchLine(m)));section.append(possibilities);}
    detail.append(section);
  }
  return detail;
}
function workloadView(){
  const root=node('div',null,'workload-view'),key=workloadKey(),report=workloadReports.get(key),compact=matchMedia('(max-width:700px)').matches;
  const listScroll=document.querySelector('.workload-people')?.scrollTop||0;
  const controls=node('div',null,'workload-controls'),mode=select([['requested','4 場／60 分鐘試算'],['configured','目前賽事規則']],workloadMode);mode.id='workload-mode';
  mode.addEventListener('change',()=>{workloadMode=mode.value;workloadPerson=null;workloadLimit=40;workloadError=null;render();});
  controls.append(field('檢查基準',mode),button('重新取得分析',()=>{loadWorkload(true);render();}));root.append(controls);
  if(!report){
    const error=workloadError?.key===key?workloadError.message:null;
    root.append(panel(error?'無法完成負荷分析':'正在計算選手負荷',error||'依選手身分跨組合併，檢查已排場次與相容晉級路徑。'));
    if(error)root.append(button('重新整理賽事資料',()=>load()));else if(workloadPending!==key)void loadWorkload();return root;
  }
  const policy=report.policy||{},configured=report.configured_policy||{};
  const notice=node('details',null,'notice workload-policy');notice.append(node('summary',workloadMode==='requested'?'試算：每天 4 場／休息 60 分鐘，不修改賽事規則':`目前規則：每日上限 ${policy.max_matches_per_day??'未設定'}，最低休息 ${policy.minimum_rest_minutes??0} 分鐘`),node('p',workloadMode==='requested'?`未修改目前規則（每日上限 ${configured.max_matches_per_day??'未設定'}、全賽事休息下限 ${configured.minimum_rest_minutes??'未設定'} 分鐘）。`:'各場次與選手的較高休息要求仍適用。'));root.append(notice);
  if(report.status==='invalid'){
    const invalid=panel('分析尚未完成','目前無法可靠計算相容路徑，不能判定負荷通過。');for(const i of report.issues||[])invalid.append(node('p',i.message||i.detail||'請核對賽事設定。','workload-issue'));root.append(invalid);return root;
  }
  root.append(node('p',`僅評估已排定 ${report.counts.scheduled_matches} 場；另有 ${report.counts.unscheduled_matches} 場未排程。${report.schedule_complete?'':'未排場次不代表已通過檢查。'}`,'muted workload-scope'));
  const day=select([['','全部日期'],...(report.event_dates||[]).map(d=>[d,d])],workloadDay);day.id='workload-day';day.addEventListener('change',()=>{workloadDay=day.value;workloadPerson=null;workloadLimit=40;render();});
  const basis=select([['all','全部問題'],['known','只看已確定問題'],['possible','只看可能晉級問題']],workloadBasis);basis.id='workload-basis';basis.addEventListener('change',()=>{workloadBasis=basis.value;workloadPerson=null;render();});
  const only=input('checkbox');only.id='workload-only-problems';only.checked=workloadOnlyProblems;only.addEventListener('change',()=>{workloadOnlyProblems=only.checked;workloadPerson=null;render();});
  const filters=node('div',null,'workload-filters'),check=field('只看有問題的選手',only);check.className='workload-check';filters.append(field('日期',day),field('問題類型',basis),check);
  const disclosure=node('details',null,'workload-filter-disclosure');disclosure.open=!compact||workloadFilterOpen;disclosure.append(node('summary',`日期與問題篩選 · ${workloadOnlyProblems?'有問題選手':'全部選手'}`),filters);disclosure.addEventListener('toggle',()=>{if(disclosure.isConnected&&compact)workloadFilterOpen=disclosure.open;});root.append(disclosure);
  const query=$('search').value.trim().toLocaleLowerCase(),divisionId=$('division').value;
  const inScope=report.participants.filter(p=>(!divisionId||p.divisions.some(d=>d.id===divisionId))&&(!query||[p.name,...p.divisions.map(d=>d.name),...p.daily.flatMap(d=>d.candidate_matches.map(m=>m.code||''))].some(v=>v.toLocaleLowerCase().includes(query))));
  const conflicts=inScope.flatMap(p=>workloadConflicts(report,p)),unique=new Map(conflicts.map(c=>[c.id,c]));
  const stats=node('div',null,'stats workload-stats');for(const [label,value] of [['位選手',inScope.length],['項已確定問題',[...unique.values()].filter(c=>c.basis==='known').length],['項可能晉級問題',[...unique.values()].filter(c=>c.basis==='possible').length]]){const cell=node('div');cell.append(node('strong',value),node('span',label));stats.append(cell);}root.append(stats);
  root.append(node('p',`${inScope.length} 位選手 · ${[...unique.values()].filter(c=>c.basis==='known').length} 項已確定問題 · ${[...unique.values()].filter(c=>c.basis==='possible').length} 項可能晉級問題`,'workload-compact-summary muted'));
  const people=inScope.filter(p=>!workloadOnlyProblems||workloadConflicts(report,p).length).sort((a,b)=>workloadConflicts(report,b).length-workloadConflicts(report,a).length||a.name.localeCompare(b.name,'zh-Hant'));
  const selected=people.find(p=>p.participant_id===workloadPerson)||people[0];workloadPerson=selected?.participant_id||null;
  const columns=node('div',null,'workload-columns'),list=panel('選手每日負荷',`${people.length} 位符合條件 · 已定＝已確定出賽；最多＝相容晉級路徑上界`);list.classList.add('workload-people');
  for(const p of people.slice(0,workloadLimit)){
    const row=button('',()=>{workloadPerson=p.participant_id;if(compact)workloadDetailOpen=true;render();if(compact)requestAnimationFrame(()=>document.querySelector('.workload-detail')?.scrollIntoView({block:'start'}));},'workload-person'+(p.participant_id===workloadPerson?' is-selected':''));row.dataset.participantId=p.participant_id;row.setAttribute('aria-pressed',String(p.participant_id===workloadPerson));row.setAttribute('aria-label',`查看 ${p.name} 的負荷明細`);
    const identity=node('div',null,'workload-person-name');identity.append(node('strong',p.name),node('span',p.divisions.map(d=>d.name).join('、'),'muted'));row.append(identity);
    const days=node('div',null,'workload-day-counts');for(const d of p.daily.filter(d=>!workloadDay||d.date===workloadDay)){const item=node('span',null,d.cap_exceeded?'workload-warning':'');item.append(node('small',d.date.slice(5).replace('-','/')),node('span',`已定 ${d.known_count}／最多 ${d.maximum_path_count??'待分析'}`));days.append(item);}row.append(days,node('span',`${workloadConflicts(report,p).length} 項問題`,'workload-problem-count'));list.append(row);
  }
  if(!people.length)list.append(node('p',inScope.length?'目前篩選沒有問題選手，可取消「只看有問題的選手」查看所有人。':'沒有符合姓名、場號或組別的選手。','empty'));
  if(people.length>workloadLimit)list.append(button('顯示更多選手',()=>{workloadLimit+=40;render();}));columns.append(list);
  if(selected&&(!compact||workloadDetailOpen))columns.append(workloadPersonDetail(report,selected));root.append(columns);root.classList.toggle('is-detail',compact&&!!selected&&workloadDetailOpen);
  requestAnimationFrame(()=>{if(list.isConnected)list.scrollTop=listScroll;});
  if(report.global_issues?.length){const other=node('details',null,'workload-global-issues');other.append(node('summary',`另有 ${report.global_issues.length} 項排程／資料檢查待處理`));for(const issue of report.global_issues)other.append(node('p',issue.message||issue.detail||issue.code,'workload-issue'));root.append(other);}
  const notes=node('details',null,'workload-notes');notes.append(node('summary','計算依據與範圍'));for(const note of report.notes||[])notes.append(node('p',note,'muted'));notes.append(node('p',`來源版本 ${report.source_revision_id}`,'revision-label'));root.append(notes);return root;
}
