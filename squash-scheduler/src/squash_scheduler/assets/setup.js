'use strict';
const $ = id => document.getElementById(id);
const token = JSON.parse($('boot').textContent).token;
let state, operatorState, rows = [], uploads = [], plan = null, busy = false;
const classifications = new Map();
const specialPolicies={walkover_rule:{title:'棄權',outcome:'walkover'},retirement_rule:{title:'傷退',outcome:'retired'},double_forfeit_rule:{title:'雙方棄權',outcome:'double_forfeit'}};
const ruleLabels = {late_arrival_rule:'遲到處理',walkover_rule:'棄權處理',retirement_rule:'傷退處理',double_forfeit_rule:'雙方棄權',replacement_rule:'替補規定',ranking_denominator_rule:'排名計算分母',multiway_tie_rule:'多人同分處理'};
function element(tag, text) { const e = document.createElement(tag); if(text !== undefined) e.textContent = text; return e; }
function message(text, error = false) { $('status').textContent = text; $('status').classList.toggle('error',error); }
async function api(path, body) { const r = await fetch(path,{method:body?'POST':'GET',headers:{'X-Squash-Token':token,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined}); const value = await r.json(); if(!r.ok) throw new Error(value.error || '無法完成操作。'); return value; }
async function run(task) { if(busy) return; busy=true; document.querySelectorAll('button').forEach(b=>b.disabled=true); try { await task(); } catch(e) { message(e.message,true); } finally { busy=false; document.querySelectorAll('button').forEach(b=>b.disabled=false); } }
function invalidate() { plan=null; }
function select(options,value,onchange) { const s=element('select'); for(const [v,text] of options) {const o=element('option',text);o.value=v;s.append(o);} s.value=value;s.addEventListener('change',()=>{onchange(s.value);invalidate();});return s; }
function identityChoices(row,index) {return rows.filter(other=>other.key===row.key||other.key===row.identity||(other.identity===other.key&&other.name===row.name)).map(other=>[other.key,other.key===row.key?`新增選手（第 ${index+1} 筆）`:`同第 ${rows.indexOf(other)+1} 筆 ${other.name}`]);}
function refreshIdentityChoices() {rows.forEach((row,index)=>{const control=$('rows').children[index]?.children[2]?.querySelector('select');if(!control)return;control.replaceChildren();for(const [value,label] of identityChoices(row,index)){const option=element('option',label);option.value=value;control.append(option);}control.value=row.identity;});}
function renderRows() {
    renderIdentityWarnings();
    $('rows').replaceChildren();
    rows.forEach((row,index)=>{
        const tr=element('tr');
        for(const key of ['name','division']) {const td=element('td'),input=element('input');input.value=row[key];input.maxLength=100;input.setAttribute('aria-label',`${index+1} ${key==='name'?'姓名':'組別'}`);input.addEventListener('input',()=>{row[key]=input.value;if(key==='division')renderClasses();else {refreshIdentityChoices();renderIdentityWarnings();}invalidate();});input.addEventListener('change',()=>{renderRows();renderClasses();});td.append(input);tr.append(td);}
        const identity=element('td');
        const identitySelect=select(identityChoices(row,index),row.identity,value=>{if(row.identity===row.key){for(const other of rows)if(other.identity===row.key)other.identity=value;}else row.identity=value;renderRows();});identitySelect.setAttribute('aria-label',`${index+1} 選手身分`);identity.append(identitySelect);tr.append(identity);
        const source=element('td',row.source_id?`${uploads.find(s=>s.id===row.source_id)?.filename||'來源'} · ${row.source_ref.sheet?row.source_ref.sheet+' · ':''}${row.source_ref.row?'第 '+row.source_ref.row+' 列':row.source_ref.page?'第 '+row.source_ref.page+' 頁':row.source_ref.line||index+1}${row.source_ref.column?' · 第 '+row.source_ref.column+' 欄':''}`:'人工新增');source.className='source';tr.append(source);
        const td=element('td'),remove=element('button','刪除');remove.type='button';remove.className='secondary';remove.addEventListener('click',()=>{if(rows.some(other=>other!==row&&other.identity===row.key)){message('其他報名仍使用此筆選手身分，請先改選身分再刪除。',true);return;}rows.splice(index,1);invalidate();renderRows();renderClasses();});td.append(remove);tr.append(td);$('rows').append(tr);
    });
}
function renderIdentityWarnings() {
    const names=new Map();for(const [index,row]of rows.entries()){const name=row.name.trim();if(!name)continue;if(!names.has(name))names.set(name,[]);names.get(name).push(index);}
    const warnings=[];for(const [name,indexes]of names){if(indexes.length<2)continue;const people=new Set(indexes.map(index=>rows[index].identity)),groups=new Set(indexes.map(index=>rows[index].division.trim()));warnings.push(`${name} 出現於第 ${indexes.map(index=>index+1).join('、')} 筆，現在視為 ${people.size} 位選手${groups.size<indexes.length?'；含同組同名，請核對是否重複報名':''}。`);}
    $('identity-warnings').textContent=warnings.join(' ');
}
function renderSources() {
    $('sources').replaceChildren();$('column-mapping').replaceChildren();
    for(const source of uploads){$('sources').append(element('p',`${source.filename} · ${rows.filter(row=>row.source_id===source.id).length} 筆候選報名`));for(const warning of source.warnings){const p=element('p',warning);p.className='warning';$('sources').append(p);}for(const table of source.tables||[])renderColumnMapping(source,table);}
    $('source-text').textContent=uploads.map(source=>`${source.filename}\n${source.text}`).join('\n\n');
}
function renderColumnMapping(source,table) {
    const panel=element('details');panel.className='mapping-panel';panel.open=!table.name_column;panel.append(element('summary',`${source.filename} · ${table.sheet} 欄位對應`));panel.append(element('p','來源欄名不同時可指定姓名與組別欄。套用會取代本工作表的候選報名，其他工作表及檔案保留；建立前仍須核對。'));
    const header=element('input');header.type='number';header.min=0;header.max=Math.max(...table.rows.map(row=>row.source_ref.row));header.value=table.header_row;const headerLabel=element('label','標題所在列（0 表示沒有標題）');headerLabel.append(header);
    const columns=Math.max(...table.rows.map(row=>row.values.length));const name=element('select'),division=element('select');
    function choices(){const values=table.rows.find(row=>row.source_ref.row===Number(header.value))?.values||[];for(const [control,empty]of [[name,'請選擇姓名欄'],[division,'使用固定組別或工作表名稱']]){const previous=control.value;control.replaceChildren();const blank=element('option',empty);blank.value='';control.append(blank);for(let index=0;index<columns;index++){const option=element('option',`第 ${index+1} 欄${values[index]?' · '+values[index]:''}`);option.value=String(index+1);control.append(option);}control.value=previous;}}
    choices();name.value=table.name_column||'';division.value=table.division_column||'';header.addEventListener('change',choices);const nameLabel=element('label','姓名來源欄'),divisionLabel=element('label','組別來源欄');nameLabel.append(name);divisionLabel.append(division);
    const fallback=element('input');fallback.maxLength=100;fallback.value=table.fallback_division;const fallbackLabel=element('label','固定組別（未指定組別欄時使用，可留白後逐筆填寫）');fallbackLabel.append(fallback);
    const sample=element('pre',table.rows.slice(0,6).map(row=>`第 ${row.source_ref.row} 列：${row.values.join(' | ')}`).join('\n'));sample.className='mapping-sample';
    const apply=element('button','套用本工作表欄位');apply.type='button';apply.addEventListener('click',()=>run(async()=>{const n=Number(name.value),d=Number(division.value),h=Number(header.value);if(!Number.isInteger(h)||h<0||h>Number(header.max)||!n||d===n)throw new Error('請指定有效標題列與姓名欄，組別欄不得與姓名欄相同。');const old=new Set(rows.filter(row=>row.source_id===source.id&&row.source_ref.sheet===table.sheet).map(row=>row.key));if(rows.some(row=>!old.has(row.key)&&old.has(row.identity)))throw new Error('其他報名仍使用本工作表的選手身分，請先改選身分再重新擷取。');const mapped=[];for(const record of table.rows){if(record.source_ref.row<=h)continue;const value=record.values[n-1]?.trim()||'';if(!value)continue;const group=d?record.values[d-1]?.trim()||'':fallback.value.trim();if(value.length>100||group.length>100||value.includes('\n'))throw new Error('指定欄位含過長文字，請改選正確姓名與組別欄。');const key=crypto.randomUUID();mapped.push({name:value,division:group,key,identity:key,source_id:source.id,source_ref:{...record.source_ref,column:n}});}rows=rows.filter(row=>!old.has(row.key));rows.push(...mapped);invalidate();renderRows();renderClasses();renderSources();message(`已重新擷取 ${mapped.length} 筆候選報名，尚未建立賽事。請核對姓名、組別與身分。`);}));
    panel.append(headerLabel,nameLabel,divisionLabel,fallbackLabel,sample,apply);$('column-mapping').append(panel);
}
function renderCloudReport(root,report) {
    root.replaceChildren();if(!report)return;root.classList.toggle('incompatible',!report.compatible);root.append(element('strong',report.compatible?'雲端設定檢查：必要條件相容':'雲端設定檢查：目前僅適合地端使用'));
    const list=element('ul');for(const check of report.checks||[]){const item=element('li',`${{pass:'符合',block:'需調整',warning:'注意'}[check.status]} · ${check.message}`);item.className=check.status;list.append(item);}root.append(list,element('p',report.notice));
}
function previewCloudSettings() {
    const form=new FormData($('settings')),value={rest:Number(form.get('rest')),daily_limit:Number(form.get('daily_limit')),participant_scope:form.get('participant_scope'),start:String(form.get('start')),end:String(form.get('end'))},checks=[['rest',60,'最低休息為 60 分鐘'],['daily_limit',5,'選手每日最多 5 場'],['participant_scope','confirmed_only','僅檢查已確定選手']].map(([key,wanted,message])=>({key,message,status:value[key]===wanted?'pass':'block'}));checks.push({key:'start_window',status:'08:00'<=value.start&&value.start<value.end&&value.end<='17:30'?'pass':'warning',message:'雲端只接受 08:00–17:30 的實際排定開賽時間，較寬的地端時段須另核對。'});renderCloudReport($('cloud-settings-report'),{compatible:checks.every(check=>check.status!=='block'),checks,notice:'設定相容不代表已核准賽制、籤表或排程；仍可保留其他設定只在地端使用。'});
}
function renderClasses() {
    const root=$('classifications');root.replaceChildren();
    for(const name of [...new Set(rows.map(r=>r.division.trim()).filter(Boolean))]) {
        if(!classifications.has(name)) classifications.set(name,{category:'competitive',gender_category:'mixed'});
        const value=classifications.get(name),count=rows.filter(row=>row.division.trim()===name).length,line=element('div');line.className='classification';const category=select([['competitive','競技組'],['school','學校組']],value.category,v=>value.category=v),gender=select([['mixed','混合／不限性別'],['male','男子'],['female','女子']],value.gender_category,v=>value.gender_category=v);category.setAttribute('aria-label',name+' 分類');gender.setAttribute('aria-label',name+' 性別');line.append(element('strong',`${name} · ${count} 人`),category,gender);if(count<3){if(value.small_count_entries!==count)value.small_count_action='';value.small_count_entries=count;const action=select([['','請選擇人數不足處置'],[count===1?'exhibition_only':'direct_match',count===1?'保留名單，不編排競賽':'兩人直接對賽']],value.small_count_action||'',v=>value.small_count_action=v);action.setAttribute('aria-label',name+' 人數不足處置');line.append(action);}root.append(line);
    }
}
function settings() {
    const form = new FormData($('settings')), result=Object.fromEntries(form.entries());
    for(const key of ['grid','play','buffer','rest','daily_limit','transfer','ranking_delay','best_of','target']) result[key]=Number(result[key]);
    for(const key of ['dates','courts']) result[key]=result[key].split(/\r?\n/).map(v=>v.trim()).filter(Boolean);
    result.division_classifications=Object.fromEntries([...classifications].filter(([name])=>rows.some(row=>row.division.trim()===name)).map(([name,value])=>[name,{category:value.category,gender_category:value.gender_category,...(rows.filter(row=>row.division.trim()===name).length<3?{small_count_action:value.small_count_action||''}:{})}]));
    Object.assign(result,readSpecialPolicies('setup'));
    return result;
}
function renderSpecialPolicies(root,prefix,current={}) {
    root.replaceChildren();root.append(element('h3','特殊結果的排名計算'));root.append(element('p','文字條文不會自動換算積分。需要登錄棄權、傷退或雙方棄權時，請明確設定是否計入排名、實際出賽、雙方積分及排名分母。啟用後以此計算設定取代同項文字條文；未確認時可保持不啟用。'));
    for(const [key,info]of Object.entries(specialPolicies)){
        const value=current[key]&&typeof current[key]==='object'?current[key]:null,details=element('details');details.className='special-policy';details.append(element('summary',info.title+'計算規則'+(value?'（已確認）':'（尚未設定）')));
        const enabled=element('input');enabled.type='checkbox';enabled.id=prefix+'-'+key+'-enabled';enabled.checked=!!value;const toggle=element('label','已確認'+info.title+'的計算規則');toggle.className='check';toggle.prepend(enabled);details.append(toggle);
        const fields=element('div');fields.className='grid';const inputs=[];
        const field=(name,title,options,initial)=>{const label=element('label',title),control=options?select([['','請明確選擇'],...options],initial===undefined?'':String(initial),()=>{}):element('input');control.id=prefix+'-'+key+'-'+name;if(!options){control.type='number';control.min='0';control.step='any';control.value=initial===undefined?'':initial;}control.disabled=!enabled.checked;control.required=enabled.checked;inputs.push(control);label.append(control);fields.append(label);};
        field('include_in_rankings','是否計入排名',[['true','計入'],['false','不計入']],value?.include_in_rankings);
        field('counts_as_played','是否算實際出賽',key==='retirement_rule'?[['true','計入實際出賽'],['false','不算實際出賽']]:[['false','未出賽，不算實際出賽']],value?.counts_as_played);
        if(key==='double_forfeit_rule'){field('side_a_points','A 方積分',null,value?.side_a_points);field('side_b_points','B 方積分',null,value?.side_b_points);}else{field('winner_points','勝方積分',null,value?.winner_points);field('loser_points','敗方積分',null,value?.loser_points);}
        field('denominator','是否計入排名分母',[['true','計入'],['false','不計入']],current.ranking_denominator_rule?.[info.outcome]);
        enabled.addEventListener('change',()=>{for(const control of inputs){control.disabled=!enabled.checked;control.required=enabled.checked;}if(prefix==='edit'){const text=$('edit-'+key);text.readOnly=enabled.checked;const denominator=$('edit-ranking_denominator_rule');denominator.readOnly=Object.keys(specialPolicies).some(policy=>$(prefix+'-'+policy+'-enabled').checked);}invalidate();});
        details.append(fields);root.append(details);
    }
}
function readSpecialPolicies(prefix,current={}) {
    const rules={},denominator=current.ranking_denominator_rule&&typeof current.ranking_denominator_rule==='object'?{...current.ranking_denominator_rule}:{};let active=false;
    for(const [key,info]of Object.entries(specialPolicies)){if(!$(prefix+'-'+key+'-enabled').checked)continue;active=true;const policy={};for(const name of ['include_in_rankings','counts_as_played',...(key==='double_forfeit_rule'?['side_a_points','side_b_points']:['winner_points','loser_points']),'denominator']){const value=$(prefix+'-'+key+'-'+name).value;if(value==='')throw new Error('請完整確認'+info.title+'的排名、實際出賽、積分與分母設定。');if(name==='denominator')denominator[info.outcome]=value==='true';else if(['include_in_rankings','counts_as_played'].includes(name))policy[name]=value==='true';else{const points=Number(value);if(!Number.isFinite(points)||points<0)throw new Error('特殊結果積分須為非負數字。');policy[name]=points;}}rules[key]=policy;}
    if(active)rules.ranking_denominator_rule=denominator;return rules;
}
function sameRule(a,b){const stable=value=>JSON.stringify(value,value&&typeof value==='object'?Object.keys(value).sort():null);return stable(a)===stable(b);}
function metadata() {const actor=$('actor').value.trim(),reason=$('reason').value.trim();if(!actor||!reason) throw new Error('請填寫操作者與本次操作原因。');return {actor,reason};}
function showReview(value,kind) {
    plan={...value,kind};$('review-title').textContent=value.summary?.title||'核對雲端回傳資料';const root=$('review-body');root.replaceChildren();
    const summary=value.summary||{};
    const line=(label,value)=>{const row=element('div');row.className='summary-row';row.append(element('strong',label+' '),document.createTextNode(String(value)));root.append(row);};
    if(kind==='setup') {
        line('賽事',`${summary.event.name} · ${summary.event.venue_name}`);line('日期',summary.event.event_dates.join('、'));line('報名',`${summary.entries} 筆 · ${summary.participants} 位選手`);line('組別',summary.divisions.join('、'));
        const settings=summary.settings;
        line('場地',settings.courts.join('、'));line('每日開放',`${settings.start}–${settings.end}`);line('時間設定',`每場 ${settings.play} 分鐘、緩衝 ${settings.buffer} 分鐘、格點 ${settings.grid} 分鐘`);line('選手負荷',`至少休息 ${settings.rest} 分鐘、每日最多 ${settings.daily_limit} 場、跨場地移動 ${settings.transfer} 分鐘`);line('未定晉級選手',settings.participant_scope==='all_possible'?'預留所有可能選手':'僅檢查已確定選手');line('計分',`${settings.best_of} 局制、每局 ${settings.target} 分、贏兩分、不封頂`);
        for(const [name,classification] of Object.entries(settings.division_classifications))line(name,`${classification.category==='school'?'學校組':'競技組'} · ${{male:'男子',female:'女子',mixed:'混合／不限性別'}[classification.gender_category]}`);
        if(summary.cloud_compatibility){const report=element('div');renderCloudReport(report,summary.cloud_compatibility);root.append(report);}
        for(const item of summary.identity_review||[])line('同名核對',`${item.name} · 第 ${item.rows.join('、')} 筆 · ${item.identities} 位選手 · ${item.divisions.join('、')}`);
        for(const group of summary.small_groups||[])line(`${group.division} · ${group.entries} 人`,group.label);
        for(const [key,label] of Object.entries(ruleLabels))line(label,ruleDisplay(settings[key]));
    } else if(kind==='rules') {
        line('來源版本',summary.source_revision);
        for(const change of summary.changes||[])line(change.label,`${ruleDisplay(change.before)} → ${ruleDisplay(change.after)}`);
    } else {
        line('雲端來源',summary.origin);line('雲端版本',summary.cloud_revision);line('地端來源版本',summary.source_revision);
        const matches=operatorState?.matches||[],courts=operatorState?.courts||[];
        const match=id=>matches.find(m=>m.id===id),name=id=>{const m=match(id);return m?`第 ${m.code} 場 ${m.side_a} 對 ${m.side_b}`:id;};
        const position=p=>p?`${new Date(p.start*60000).toLocaleString('zh-TW',{timeZone:'Asia/Taipei',hour12:false})} · ${courts.find(c=>c.id===p.court)?.label||p.court}`:'未排程';
        line('排程異動',`${summary.schedule_changes?.length||0} 場`);
        for(const change of summary.schedule_changes||[])line(name(change.match_id),`${position(change.before)} → ${position(change.after)}`);
        line('新增成績',`${summary.result_count||0} 場`);
        for(const result of summary.results||[]){const winner=operatorState?.entries.find(e=>e.id===result.winner_id)?.name||'待確認';line(name(result.match_id),`勝方 ${winner} · ${result.games.map(g=>`${g.side_a}:${g.side_b}`).join('、')}`);}
        const statuses={planned:'待比賽',ready:'待比賽',completed:'已完賽'};
        for(const change of summary.status_changes||[])line(name(change.match_id),`${statuses[change.before]||change.before} → ${statuses[change.after]||change.after}`);
        for(const [id,instant] of Object.entries(summary.actual_starts?.starts||{}))line(name(id)+' 實際開賽',`${new Date(instant).toLocaleString('zh-TW',{timeZone:'Asia/Taipei',hour12:false})} · ${courts.find(c=>c.id===summary.actual_starts.actual_courts[id])?.label||'未知場地'}`);
    }
    if(summary.notice)line('注意事項',summary.notice);
    $('confirm').disabled=value.summary?.can_apply===false;$('review').showModal();
}
async function refresh() {
    state=await api('/api/setup-state');$('workspace').textContent=state.workspace;$('new-event').hidden=state.initialized;$('existing').hidden=!state.initialized;$('operator-link').hidden=!state.initialized;$('setup-preview').hidden=state.initialized;$('next-heading').textContent=state.initialized?'編排、文件與雲端交換':'4. 建立賽事';
    renderCloudReport($('cloud-existing-report'),state.cloud_compatibility);
    if(!state.initialized&&!uploads.length&&state.uploads?.length){uploads=state.uploads;for(const source of uploads)for(const candidate of source.rows){const key=crypto.randomUUID();rows.push({...candidate,key,identity:key,source_id:source.id});}renderSources();renderRows();renderClasses();message('已重新讀取尚在服務記憶體中的來源候選；未儲存的姓名修正與身分選擇需重新核對。');}
    if(state.initialized){const data=await api('/api/state');operatorState=data;if(!$('actor').value)$('actor').value=data.actor||'';renderRuleEditor();}
}
function ruleDisplay(value){if(value===null||value===undefined||value==='')return '待補';if(typeof value!=='object')return String(value);const labels={include_in_rankings:'計入排名',counts_as_played:'實際出賽',winner_points:'勝方積分',loser_points:'敗方積分',side_a_points:'A 方積分',side_b_points:'B 方積分',played:'正常完賽計入分母',walkover:'棄權計入分母',retired:'傷退計入分母',double_forfeit:'雙方棄權計入分母',cancelled:'取消計入分母'};return Object.entries(value).map(([key,item])=>`${labels[key]||key}：${typeof item==='boolean'?(item?'是':'否'):item}`).join('；');}
function ruleText(value){return value===null||value===undefined?'':typeof value==='string'?value:JSON.stringify(value,null,2);}
function renderRuleEditor(){const root=$('rule-edit');root.replaceChildren();for(const [key,title]of Object.entries(ruleLabels)){const label=element('label',title),text=element('textarea');text.id='edit-'+key;text.maxLength=4000;text.value=state.rules[key]&&typeof state.rules[key]==='object'&&(key in specialPolicies||key==='ranking_denominator_rule')?'':ruleText(state.rules[key]);text.readOnly=!!(state.rules[key]&&typeof state.rules[key]==='object'&&(key in specialPolicies||key==='ranking_denominator_rule'));text.placeholder=text.readOnly?'已使用下方特殊結果計算規則':'尚未確認可留空';label.append(text);root.append(label);}renderSpecialPolicies($('special-existing'),'edit',state.rules);}
$('rules-preview').addEventListener('click',()=>run(async()=>{const rules={};for(const key of Object.keys(ruleLabels)){const field=$('edit-'+key);if(field.readOnly)continue;const text=field.value.trim();if(text!==ruleText(state.rules[key]).trim())rules[key]=text||null;}for(const [key,value]of Object.entries(readSpecialPolicies('edit',state.rules)))if(!sameRule(value,state.rules[key]))rules[key]=value;if(!Object.keys(rules).length)throw new Error('簡章條文沒有變更。');const value=await api('/api/preview',{...metadata(),revision_id:state.revision_id,action:'event_rules',rules});showReview(value,'rules');message('請核對簡章條文前後差異。');}));
$('upload').addEventListener('click',()=>run(async()=>{
    const chosen=[...$('files').files];if(!chosen.length)throw new Error('請先選擇 Excel、Word 或 PDF。');
    for(const file of chosen){if(file.size>25*1048576)throw new Error('單一來源檔案不可超過 25 MB。');message(`正在讀取 ${file.name}…`);const r=await fetch('/api/intake?filename='+encodeURIComponent(file.name),{method:'POST',headers:{'X-Squash-Token':token,'Content-Type':'application/octet-stream'},body:file});const value=await r.json();if(!r.ok)throw new Error(value.error||'無法讀取檔案。');if(uploads.some(s=>s.id===value.id))continue;uploads.push(value);for(const raw of value.rows){const key=crypto.randomUUID();rows.push({...raw,key,identity:key,source_id:value.id});}}
    renderSources();invalidate();renderRows();renderClasses();message('檔案已讀取，尚未建立賽事。請核對名單、身分與規則。');
}));
$('add-row').addEventListener('click',()=>{const key=crypto.randomUUID();rows.push({name:'',division:'',key,identity:key,source_ref:{}});invalidate();renderRows();});
for(const [key,title] of Object.entries(ruleLabels)){const label=element('label',title),text=element('textarea');text.name=key;text.maxLength=4000;text.placeholder='尚未確認可留空';label.append(text);$('rules').append(label);}
$('settings').addEventListener('input',()=>{invalidate();previewCloudSettings();});
renderSpecialPolicies($('special-setup'),'setup');
previewCloudSettings();
$('setup-preview').addEventListener('click',()=>run(async()=>{if(!$('settings').reportValidity())return;const value=await api('/api/setup-preview',{...metadata(),uploads:uploads.map(s=>s.id),rows:rows.map(({key,...row})=>row),settings:settings()});showReview(value,'setup');message('請核對建立內容，確認後才會保存。');}));
$('confirm').addEventListener('click',()=>run(async()=>{if(!plan||plan.summary?.can_apply===false)return;await api(plan.kind==='setup'?'/api/setup-confirm':'/api/confirm',{id:plan.id,hash:plan.hash});plan=null;$('review').close();await refresh();message('已儲存新版本。請依操作步驟繼續。');}));
$('cancel').addEventListener('click',()=>{plan=null;$('review').close();});$('review').addEventListener('cancel',()=>{plan=null;});
$('cloud-export').addEventListener('click',()=>run(async()=>{const value=await api('/api/cloud-export');const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));const a=element('a');a.href=url;a.download=value.format==='pk-local-update-v1'?'pk-local-update.json':'pk-schedule.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);message('已下載雲端賽程資料。請到雲端後台匯入、核對並發布。');}));
$('advancement-export').addEventListener('click',()=>run(async()=>{const value=await api('/api/advancement-sources');const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));const a=element('a');a.href=url;a.download='pk-advancement-sources.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);message('已下載原版晉級來源，請到雲端後台核對後確認綁定。');}));
$('return-preview').addEventListener('click',()=>run(async()=>{const file=$('cloud-return').files[0];if(!file)throw new Error('請選擇雲端回傳 JSON。');if(file.size>1048576)throw new Error('回傳檔案不可超過 1 MB。');const envelope=JSON.parse(await file.text());const value=await api('/api/cloud-return-preview',{...metadata(),revision_id:state.revision_id,envelope});showReview(value,'cloud');message('請核對雲端資料與地端差異。');}));
$('font-upload').addEventListener('click',()=>run(async()=>{const file=$('font').files[0];if(!file)throw new Error('請選擇可嵌入的字型。');if(file.size>32*1048576)throw new Error('字型不可超過 32 MB。');const actor=$('actor').value.trim();if(!actor)throw new Error('請填寫操作者。');const bytes=new TextEncoder().encode(JSON.stringify({actor,revision_id:state.revision_id}));let raw='';for(const byte of bytes)raw+=String.fromCharCode(byte);const r=await fetch('/api/font',{method:'POST',headers:{'X-Squash-Token':token,'Content-Type':'application/octet-stream','X-Upload-Metadata':btoa(raw)},body:file});const value=await r.json();if(!r.ok)throw new Error(value.error||'字型匯入失敗。');await refresh();message('字型已匯入並建立新版本，可回操作台產生文件。');}));
window.addEventListener('beforeunload',event=>{if(!state?.initialized&&(rows.length||uploads.length)){event.preventDefault();event.returnValue='';}});
run(refresh);
