import test from 'node:test';
import assert from 'node:assert/strict';
import {LiveSession,liveScore} from '../backend/web/live-session.js';
const now=Date.parse('2026-11-12T09:00:00Z');
const bus={rank:1,monitored:1,eta:'2026-11-12T09:05:00Z',etaSecondsNow:300,load:'SEA'};
const snapshot=(time=now,code='01001',load='SEA')=>({stopCode:code,status:'available',capturedAt:new Date(time).toISOString(),freshUntil:new Date(time+20000).toISOString(),observations:[{meta:{serviceNo:'1',operator:'BUS'},arrivals:[{...bus,load}]}]});
test('live capacity rejects unknown, scheduled and elapsed readings without turning them into zero',()=>{
  assert.equal(liveScore(bus),0);assert.equal(liveScore({...bus,load:'LSD'}),2);
  for(const changes of [{load:''},{monitored:0},{monitored:null},{eta:null},{etaSecondsNow:-1},{etaSecondsNow:1801}])assert.equal(liveScore({...bus,...changes}),null);
});
test('session starts from first real reading and deduplicates cached timestamps',()=>{
  const store=new LiveSession();assert.equal(store.add(snapshot(),now),true);assert.equal(store.add(snapshot(),now+1000),false);
  assert.deepEqual(store.series('01001','1|BUS'),[{time:now,score:0}]);
  assert.equal(store.add(snapshot(now+20000,'01001','SDA'),now+20000),true);
  assert.deepEqual(store.series('01001','1|BUS').map(x=>x.score),[0,1]);
});
test('stale, expired, out-of-order and future timestamps never create trend points',()=>{
  const store=new LiveSession();store.add(snapshot(),now);
  for(const data of [{...snapshot(now+20000),status:'stale'},snapshot(now-20000),snapshot(now+200000),{...snapshot(now+20000),freshUntil:new Date(now).toISOString()}])assert.equal(store.add(data,now+20000),false);
  assert.equal(store.series('01001','1|BUS').length,1);
});
test('missing service creates an explicit unknown gap, not an empty-bus reading',()=>{
  const store=new LiveSession();store.add(snapshot(),now);store.add({...snapshot(now+20000),status:'no_data',observations:[]},now+20000);
  assert.deepEqual(store.series('01001','1|BUS').map(x=>x.score),[0,null]);
});
test('session memory is bounded and clear removes all prior-mode readings',()=>{
  const store=new LiveSession(2,3);
  for(let i=0;i<5;i++)store.add(snapshot(now+i*20000),now+i*20000);
  assert.equal(store.series('01001','1|BUS').length,3);
  store.add(snapshot(now,'01002'),now);store.add(snapshot(now,'01003'),now);
  assert.equal(store.series('01001','1|BUS').length,0);assert.equal(store.stops.size,2);
  store.clear();assert.equal(store.stops.size,0);
});
