import {parseLocal} from '../outputs/rules.mjs';
const start=parseLocal('2030-01-01T08:00');
export function fixture(){return {
 format:'pk-schedule-v1',source_revision:'synthetic-revision',name:'Synthetic tournament',timezone:'Asia/Taipei',dates:['2030-01-01','2030-01-02'],grid:15,
 anchors:{'2030-01-01':start,'2030-01-02':start+1440},first_start:'08:00',last_start:'17:30',minimum_rest:60,daily_limit:5,participant_scope:'confirmed_only',keep_groups_same_day:true,
 courts:[{id:'a',name:'Synthetic court A',windows:[{start,end:start+600},{start:start+1440,end:start+2040}]},{id:'b',name:'Synthetic court B',windows:[{start,end:start+600},{start:start+1440,end:start+2040}]}],
 players:[{id:'p1',name:'SyntheticPlayer001',rest:0},{id:'p2',name:'SyntheticPlayer002',rest:0}],
 matches:[{id:'m1',code:'1',division:'Synthetic division',stage:'Round robin',group:'g1',side_a:'SyntheticPlayer001',side_b:'SyntheticPlayer002',players:['p1','p2'],status:'planned',play:10,buffer:5,rest:60,transfer:0,dependencies:[],ready_after:null,lock_time:false,lock_court:false,assignment:{start,court:'a'}},
 {id:'m2',code:'2',division:'Synthetic division',stage:'Round robin',group:'g1',side_a:'SyntheticPlayer001',side_b:'SyntheticPlayer002',players:['p1','p2'],status:'planned',play:10,buffer:5,rest:60,transfer:0,dependencies:[],ready_after:null,lock_time:false,lock_court:false,assignment:{start:start+75,court:'b'}}],
};}
