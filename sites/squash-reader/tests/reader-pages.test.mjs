import test from 'node:test';
import assert from 'node:assert/strict';
import {readerPage,pageHref,pageInfo,pageForView,completedMatches,preferredEventDate,compactScheduleRows,matchesReaderQuery} from '../reader-pages.mjs';
test('each feature has a stable path and a dedicated view',()=>{
 assert.equal(readerPage('/'),'schedule');assert.equal(readerPage('/live/'),'results');
 for(const [page,info]of Object.entries(pageInfo)){assert.equal(readerPage(info.path),page);assert.equal(pageForView(info.view),page);assert.equal(readerPage(info.path,'?view=standings'),page);}
});
test('existing player, filter, view and chapter links still reach their feature',()=>{
 assert.equal(readerPage('/','?player=SyntheticPlayer001'),'schedule');assert.equal(readerPage('/','?court=a&date=2030-01-01'),'schedule');assert.equal(readerPage('/','?view=bracket&division=d'),'bracket');assert.equal(readerPage('/','?view=courts'),'results');assert.equal(readerPage('/','','#reader-standings-stage-d'),'bracket');
});
test('canonical page links retain search filters and omit obsolete view parameters',()=>{
 const url=pageHref('schedule','?player=SyntheticPlayer001&court=a&view=schedule&date=2030-01-01&empty=');assert(url.startsWith('/schedule?'));const p=new URL(url,'https://example.test').searchParams;assert.equal(p.get('player'),'SyntheticPlayer001');assert.equal(p.get('court'),'a');assert.equal(p.get('date'),'2030-01-01');assert.equal(p.has('view'),false);assert.equal(p.has('empty'),false);assert.equal(pageHref('results','?view=courts&division=d'),'/results?division=d');
});

test('results exclude unfinished and unconfirmed matches but retain walkovers',()=>{ const pending={id:'pending',status:'completed',result:null},playing={id:'playing',status:'in_progress',result:{games:[]}},done={id:'done',status:'completed',result:{games:[{side_a:11,side_b:5}]}},walkover={id:'walkover',status:'completed',result:{games:[],outcome:'walkover'}}; assert.deepEqual(completedMatches([pending,playing,done,walkover]),[done,walkover]); });

test('old ranking links open the combined group page',()=>{
 assert.equal(readerPage('/standings'),'bracket');assert.equal(readerPage('/','?view=standings'),'bracket');assert.equal(pageHref('standings'),'/brackets');
});
test('default event day follows the event dates at Taipei midnight',()=>{
 const dates=['2030-10-25','2030-10-24'];
 assert.equal(preferredEventDate(dates,'2030-10-23T16:00:00Z'),'2030-10-24');
 assert.equal(preferredEventDate(dates,'2030-10-24T15:59:59Z'),'2030-10-24');
 assert.equal(preferredEventDate(dates,'2030-10-24T16:00:00Z'),'2030-10-25');
 assert.equal(preferredEventDate(dates,'2030-11-01T00:00:00Z'),'2030-10-25');
 assert.equal(preferredEventDate([]),'');
});
const scheduled=(id,start)=>({id,code:String(id),planned_start_at:`2030-10-24T${start}:00+08:00`,court_id:'A',side_a:{name:'王小明'},side_b:{name:'陳大同'}});
test('mixed 15/20 minute starts fill consecutive pairs and retain the whole day',()=>{
 const matches=[scheduled(1,'08:00'),scheduled(2,'08:15'),scheduled(3,'08:35'),scheduled(4,'09:00'),...Array.from({length:45},(_,i)=>scheduled(i+5,'09:20'))];
 const rows=compactScheduleRows(matches);
 assert.deepEqual(rows.slice(0,2).map(row=>row.matches.map(match=>match.id)),[[1,2],[3,4]]);
 assert.deepEqual(rows.flatMap(row=>row.hours),['08:00','09:00']);
 assert.equal(rows.flatMap(row=>row.matches).length,49);
 assert(rows.slice(0,-1).every(row=>row.matches.length===2));
 assert.equal(matches[0].id,1);
});
test('search supports full-width input and exact match numbers',()=>{
 const match=scheduled(42,'08:00');
 assert(matchesReaderQuery(match,'王小'));assert(matchesReaderQuery(match,'第４２場'));assert(matchesReaderQuery(match,'42'));
 assert.equal(matchesReaderQuery(match,'4'),false);assert.equal(matchesReaderQuery(match,'林'),false);
});
