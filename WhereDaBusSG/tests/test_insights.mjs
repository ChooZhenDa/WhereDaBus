import test from 'node:test';
import assert from 'node:assert/strict';
import {sgDate,datePreset,validDateRange,chartSegments,trendChart,csvText,formatSG} from '../backend/web/insights-model.js';
import {demoRequest} from '../backend/web/demo.js';

test('Singapore date presets cross UTC midnight correctly',()=>{
  const now=Date.parse('2026-11-15T17:00:00Z');
  assert.equal(sgDate(now),'2026-11-16');
  assert.deepEqual(datePreset('today',now),{from:'2026-11-16',to:'2026-11-16'});
  assert.deepEqual(datePreset('week',now),{from:'2026-11-09',to:'2026-11-15'});
  assert.match(formatSG(now),/16 Nov/);
});
test('date range validation rejects impossible, reversed, future and overlong dates',()=>{
  const now=Date.parse('2026-11-16T05:00:00Z');
  for(const [a,b] of [['2026-02-30','2026-03-01'],['2026-11-16','2026-11-15'],['2026-11-17','2026-11-17'],['2026-10-15','2026-11-15']])assert(validDateRange(a,b,now));
  assert.equal(validDateRange('2026-10-16','2026-11-15',now),'');
});
test('chart never joins across missing bins or plots unknown crowding as zero',()=>{
  const row=(time,score)=>({_id:{bucket:`2026-11-16T${time}:00Z`,operator:'TEST'},meanCrowdingOrdinal:score,rollingMeanOrdinal:score});
  const rows=[row('00:00',0),row('00:30',1),row('01:00',null),row('01:30',2),row('03:00',1)];
  const parts=chartSegments(rows,'meanCrowdingOrdinal');
  assert.deepEqual(parts.map(p=>p.length),[2,1,1]);
  assert.equal(parts[0][0].value,0);
  const svg=trendChart(rows,'<script>alert(1)</script>');
  assert(!svg.includes('<script>'));assert(!svg.includes('NaN'));assert(!svg.includes('Unknown time'));
});
test('CSV quotes embedded commas/newlines and neutralizes formula strings',()=>{
  const csv=csvText([{name:'=HYPERLINK("bad")',description:'a,b\nc',score:0,missing:null}]);
  assert(csv.includes('"\'=HYPERLINK(""bad"")"'));
  assert(csv.includes('"a,b\nc"'));assert(csv.includes('"0",""'));
});
test('demo date filter returns only selected Singapore date and labels synthetic records',async()=>{
  const rows=await demoRequest('/analytics/trend',{service:'DEMO1',stop:'01001',date_from:'2026-11-10',date_to:'2026-11-10'});
  assert.equal(rows.length,8);
  for(const row of rows){assert.equal(sgDate(Date.parse(row._id.bucket)),'2026-11-10');assert.equal(row.daysWithValidSamples,1);assert.match(row.source,/SYNTHETIC/);}
  const weekend=await demoRequest('/analytics/peak',{service:'DEMO1',stop:'01001',date_from:'2026-11-14',date_to:'2026-11-15'});
  assert.deepEqual(weekend.rows,[]);
  const weekday=await demoRequest('/analytics/peak',{service:'DEMO1',stop:'01001',date_from:'2026-11-10',date_to:'2026-11-10'});
  assert.equal(weekday.rows[0].daysWithValidSamples,1);assert.equal(weekday.rows[0].observations,160);
});
