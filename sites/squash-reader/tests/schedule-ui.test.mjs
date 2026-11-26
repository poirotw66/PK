import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {compactScheduleRows} from '../reader-pages.mjs';
const source=await readFile(new URL('../dist/live.mjs',import.meta.url),'utf8');
function node(tag,text=null,className='') {return {tag,tagName:tag.toUpperCase(),text,className,dataset:{},children:[],events:{},append(...children){this.children.push(...children);},setAttribute(){},addEventListener(event,fn){this.events[event]=fn;}};}
const find=(root,fn)=>[...(fn(root)?[root]:[]),...root.children.flatMap(child=>find(child,fn))];
const match=(id)=>({id:String(id),code:String(id),planned_start_at:'2030-10-24T08:00:00+08:00',division_id:'d',stage_id:'s',status:'planned',side_a:{entry_id:'a',resolution:'confirmed',name:'王小明'},side_b:{entry_id:'b',resolution:'confirmed',name:'陳大同'},result:null});
function renderer(){
 const selected=[],ctx={node,data:{courts:[],timezone:'Asia/Taipei'},selectedPlayer:'',personalChanges:new Map(),compactScheduleRows,label:(_,id)=>id,time:()=> '08:00',state:()=> '待比賽',resultText:()=> '成績已確認',navigationTarget:node=>node,choosePlayer:name=>selected.push(['player',name]),chooseDivision:id=>selected.push(['division',id])};
 const part=source.slice(source.indexOf('function scheduleTicket('),source.indexOf('let syncStatusBusy'));
 return {...vm.runInNewContext(part+';({schedule,scheduleTicket})',ctx),selected};
}
test('schedule renders all matches and player/group buttons apply a filter',()=>{
 const ui=renderer(),root=ui.schedule(Array.from({length:49},(_,i)=>match(i+1)));
 assert.equal(find(root,n=>n.className==='match-ticket').length,49);
 const buttons=find(root,n=>n.tag==='button');
 buttons.find(n=>n.text==='王小明').events.click();buttons.find(n=>n.text==='d').events.click();
 assert.deepEqual(ui.selected,[['player','王小明'],['division','d']]);
});
test('bracket card keeps reference dates only on unfinished matches',()=>{
 const body=source.slice(source.indexOf('function card('),source.indexOf('function drawLines('));
 const {card}=vm.runInNewContext(body+';({card})',{node,state:()=> '待比賽',scoreMarks:()=> null,time:()=> '10/24 08:00',label:()=> 'A',outcomes:{played:'已完賽'}});
 const pending=card(match(1)),done=card({...match(2),status:'completed',result:{outcome:'played',games:[{side_a:11,side_b:5}]}});
 assert(find(pending,n=>String(n.text).includes('10/24 08:00')).length);
 assert.equal(find(done,n=>String(n.text).includes('10/24 08:00')).length,0);
});
