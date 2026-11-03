// Public page routing preserves existing shared query and chapter links.
export const pageInfo = {
  live: {path:'/live',view:'courts',title:'即時賽況',description:'看現在誰在比賽，比分自動更新。'},
  schedule: {path:'/schedule',view:'schedule',title:'查詢賽程',description:'輸入選手姓名，查看自己的比賽時間與場地。'},
  bracket: {path:'/brackets',view:'bracket',title:'晉級對戰',description:'選擇組別，查看對戰安排與晉級結果。'},
  standings: {path:'/standings',view:'standings',title:'積分排名',description:'選擇組別，查看循環賽成績與排名。'},
};
export function pageForView(view) {
  return Object.keys(pageInfo).find(page=>pageInfo[page].view===view)??'schedule';
}
export function readerPage(pathname,search='',hash='') {
  const path=pathname.replace(/\/$/,'')||'/';
  const explicit=Object.keys(pageInfo).find(page=>pageInfo[page].path===path);
  if(explicit)return explicit;
  const chapter=/^#reader-(bracket|standings|schedule|courts)-/.exec(hash)?.[1];
  if(chapter)return pageForView(chapter);
  const params=new URLSearchParams(search),view=params.get('view');
  if(Object.values(pageInfo).some(page=>page.view===view))return pageForView(view);
  return ['player','q','date','court','division'].some(key=>params.get(key))?'schedule':'live';
}
export function pageHref(page,search='') {
  const next=pageInfo[page]??pageInfo.live,params=new URLSearchParams(search);
  params.delete('view');
  for(const [key,value] of [...params])if(!value)params.delete(key);
  if(page==='live')return next.path;
  return next.path+(params.size?'?'+params.toString():'');
}
