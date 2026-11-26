import {orderedMatches} from './reader-state.mjs';
// Public page routing preserves existing shared query and chapter links.
export const pageInfo = {
  results: {path:'/results',view:'courts',title:'完賽成績',description:'查詢已確認的各局比分與勝方。'},
  schedule: {path:'/schedule',view:'schedule',title:'預定時間',description:'輸入選手姓名或場號，查看當日預定時間與完賽成績。'},
  bracket: {path:'/brackets',view:'bracket',title:'各組賽程',description:'選擇組別，查看對戰、成績與小組排名。'},
};
export function pageForView(view) {
  if(view==='standings')return 'bracket';
  return Object.keys(pageInfo).find(page=>pageInfo[page].view===view)??'schedule';
}
export function readerPage(pathname,search='',hash='') {
  const path=pathname.replace(/\/$/,'')||'/';
  if(path==='/live')return 'results';
  if(path==='/standings')return 'bracket';
  const explicit=Object.keys(pageInfo).find(page=>pageInfo[page].path===path);
  if(explicit)return explicit;
  const chapter=/^#reader-(bracket|standings|schedule|courts)-/.exec(hash)?.[1];
  if(chapter)return pageForView(chapter);
  const params=new URLSearchParams(search),view=params.get('view');
  if(view==='standings'||Object.values(pageInfo).some(page=>page.view===view))return pageForView(view);
  return 'schedule';
}
export function pageHref(page,search='') {
  const next=pageInfo[page==='standings'?'bracket':page]??pageInfo.schedule,params=new URLSearchParams(search);
  params.delete('view');
  for(const [key,value] of [...params])if(!value)params.delete(key);
  return next.path+(params.size?'?'+params.toString():'');
}

export function completedMatches(matches) { return matches.filter(match => match.status === 'completed' && !!match.result); }

export function localDate(value = new Date(), timezone = 'Asia/Taipei') {
  return new Intl.DateTimeFormat('sv-SE',{timeZone:timezone,year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(value));
}
export function preferredEventDate(dates, now = new Date(), timezone = 'Asia/Taipei') {
  const sorted=[...new Set(dates.filter(Boolean))].sort();
  if(!sorted.length)return '';
  const today=localDate(now,timezone);
  return today<=sorted[0]?sorted[0]:sorted.find(date=>date>=today)??sorted.at(-1);
}
export function matchesReaderQuery(match, query) {
  const term=query.normalize('NFKC').trim().toLocaleLowerCase();
  if(!term)return true;
  const number=/^(?:第\s*)?(\d+)(?:\s*場)?$/.exec(term)?.[1];
  if(number)return String(match.code).normalize('NFKC')===number;
  return [match.code,match.side_a.name,match.side_b.name].some(value=>String(value).normalize('NFKC').toLocaleLowerCase().includes(term));
}
// Pair consecutive matches, independently of their 15/20-minute start slots.
export function compactScheduleRows(matches, courtOrder = new Map(), timezone = 'Asia/Taipei') {
  const ordered=orderedMatches(matches,courtOrder), rows=[];
  const seenHours=new Set();
  for(let index=0;index<ordered.length;){
    const date=ordered[index].planned_start_at?localDate(ordered[index].planned_start_at,timezone):'未排程';
    const pair=[ordered[index++]];
    if(index<ordered.length&&(ordered[index].planned_start_at?localDate(ordered[index].planned_start_at,timezone):'未排程')===date)pair.push(ordered[index++]);
    const hours=[];
    for(const match of pair){
      const hour=match.planned_start_at?new Intl.DateTimeFormat('en-GB',{timeZone:timezone,hour:'2-digit',hourCycle:'h23'}).format(new Date(match.planned_start_at))+':00':'未定';
      const key=date+' '+hour;
      if(!seenHours.has(key)){seenHours.add(key);hours.push(hour);}
    }
    rows.push({date,hours,matches:pair});
  }
  return rows;
}
