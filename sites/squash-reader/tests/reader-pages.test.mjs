import test from 'node:test';
import assert from 'node:assert/strict';
import {readerPage,pageHref,pageInfo,pageForView} from '../reader-pages.mjs';
test('each feature has a stable path and a dedicated view',()=>{
 assert.equal(readerPage('/'),'live');assert.equal(readerPage('/live/'),'live');
 for(const [page,info]of Object.entries(pageInfo)){assert.equal(readerPage(info.path),page);assert.equal(pageForView(info.view),page);assert.equal(readerPage(info.path,'?view=standings'),page);}
});
test('existing player, filter, view and chapter links still reach their feature',()=>{
 assert.equal(readerPage('/','?player=SyntheticPlayer001'),'schedule');assert.equal(readerPage('/','?court=a&date=2030-01-01'),'schedule');assert.equal(readerPage('/','?view=bracket&division=d'),'bracket');assert.equal(readerPage('/','?view=courts'),'live');assert.equal(readerPage('/','','#reader-standings-stage-d'),'standings');
});
test('canonical page links retain search filters and omit obsolete view parameters',()=>{
 const url=pageHref('schedule','?player=SyntheticPlayer001&court=a&view=schedule&date=2030-01-01&empty=');assert(url.startsWith('/schedule?'));const p=new URL(url,'https://example.test').searchParams;assert.equal(p.get('player'),'SyntheticPlayer001');assert.equal(p.get('court'),'a');assert.equal(p.get('date'),'2030-01-01');assert.equal(p.has('view'),false);assert.equal(p.has('empty'),false);assert.equal(pageHref('live','?player=SyntheticPlayer001&court=a'),'/live');
});
