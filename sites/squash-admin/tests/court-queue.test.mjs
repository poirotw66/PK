import test from 'node:test';
import assert from 'node:assert/strict';
import {taipeiDate,preferredQueueDate,queueForDate} from '../outputs/court-queue.mjs';
test('queue dates use Taiwan time, including the UTC midnight boundary',()=>{
 assert.equal(taipeiDate('2030-01-01T16:00:00.000Z'),'2030-01-02');assert.equal(taipeiDate('2030-01-01T15:59:59.000Z'),'2030-01-01');assert.equal(taipeiDate('invalid'),'');
});
test('default date is today, otherwise the next event day or latest past event day',()=>{
 const dates=['2030-01-03','2030-01-01'];assert.equal(preferredQueueDate(dates,'2030-01-01'),'2030-01-01');assert.equal(preferredQueueDate(dates,'2030-01-02'),'2030-01-03');assert.equal(preferredQueueDate(dates,'2030-02-01'),'2030-01-03');assert.equal(preferredQueueDate([],'2030-01-01'),'2030-01-01');assert.deepEqual(dates,['2030-01-03','2030-01-01']);
});
test('date changes exclude the other day and preserve stable chronological order without mutation',()=>{
 const rows=[{id:'late',planned_start:'2030-01-01T02:00:00Z',eligible:true},{id:'next-day',planned_start:'2030-01-01T16:00:00Z',eligible:true},{id:'blocked',planned_start:'2030-01-01T00:00:00Z',eligible:false},{id:'early',planned_start:'2030-01-01T01:00:00Z',eligible:true},{id:'unassigned',planned_start:null,eligible:true}];const before=structuredClone(rows),queue=queueForDate(rows,'2030-01-01');assert.deepEqual(queue.map(row=>row.id),['blocked','early','late']);assert.equal(queue.find(row=>row.eligible).id,'early');assert.deepEqual(queueForDate(rows,'2030-01-02').map(row=>row.id),['next-day']);assert.deepEqual(queueForDate(rows,'2030-01-03'),[]);assert.deepEqual(rows,before);
});
