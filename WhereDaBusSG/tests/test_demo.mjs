// Run from the project root: node --test tests/test_demo.mjs
// No packages, network, database, or DOM are required.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';

import {demoRequest as request} from '../backend/web/demo.js';

test('demo never claims configured live infrastructure', async () => {
  const config = await request('/ui/config');
  assert.equal(config.mode, 'preview');
  assert.equal(config.ready, false);
  assert.deepEqual(config.configured, {sql: false, mongo: false, lta: false});
  assert.equal((await request('/health')).source, 'SYNTHETIC demo');
});

test('stop search handles names, roads, codes and leading zeroes', async () => {
  assert.equal((await request('/stops/search', {query: '  LIBRARY  '}))[0].stop_code, '01002');
  assert.equal((await request('/stops/search', {query: 'Demo Road'})).length, 2);
  assert.equal((await request('/stops/search', {query: '01001'}))[0].description, 'Demo Campus');
  assert.deepEqual(await request('/stops/search', {query: 'unknown stop'}), []);
});

test('URLSearchParams and plain object searches have the same result', async () => {
  assert.deepEqual(await request('/stops/search', new URLSearchParams({query: '01001'})),
    await request('/stops/search', {query: '01001'}));
});

test('nearby stops respect radius, coordinates and distance order', async () => {
  const rows = await request('/stops/nearby', {lat: 1.3, lon: 103.8, radius: 500});
  assert.equal(rows.length, 3);
  assert.equal(rows[0].stop_code, '01001');
  assert.equal(rows[0].distance_m, 0);
  assert(rows[1].distance_m > 150 && rows[1].distance_m < 165);
  assert(rows[2].distance_m > rows[1].distance_m);
  assert.equal((await request('/stops/nearby', {lat: 1.3, lon: 103.8, radius: 1})).length, 1);
  assert.deepEqual(await request('/stops/nearby', {lat: 1.5, lon: 103.8, radius: 500}), []);
});

test('direct journeys respect service direction and a loop return', async () => {
  const forward = await request('/routes/direct', {origin: '01001', destination: '01003'});
  assert.deepEqual(forward.map(row => row.service_no), ['DEMO1', 'DEMO2']);
  const reverse = await request('/routes/direct', {origin: '01003', destination: '01001'});
  assert.deepEqual(reverse.map(row => row.service_no), ['DEMO1']);
  assert(reverse[0].destination_sequence > reverse[0].origin_sequence);
  assert.equal(reverse[0].distance_km, 1.5);
  assert.deepEqual(await request('/routes/direct', {origin: '99999', destination: '01001'}), []);
});

test('road search does not duplicate a service visiting multiple matching stops', async () => {
  const rows = await request('/routes/road', {name: 'demo road'});
  assert.equal(rows.length, 2);
  assert.equal(new Set(rows.map(row => `${row.service_no}/${row.direction}`)).size, rows.length);
  assert.deepEqual(await request('/routes/road', {name: 'Missing Road'}), []);
  assert.deepEqual(await request('/routes/road', {name: ' '}), []);
});

test('longest routes are ordered by route length', async () => {
  const rows = await request('/routes/longest');
  assert(rows.length > 1);
  assert(rows.every((row, index) => !index || rows[index - 1].route_length_km >= row.route_length_km));
});

test('arrivals retain three bus slots, valid countdowns and synthetic provenance', async () => {
  const data = await request('/arrivals/01001');
  assert.equal(data.status, 'available');
  assert.equal(data.stopCode, '01001');
  assert.equal(data.source, 'SYNTHETIC demo');
  assert.equal(data.observations.length, 2);
  for (const observation of data.observations) {
    assert.equal(observation.source, 'SYNTHETIC demo');
    assert.equal(observation.meta.stopCode, data.stopCode);
    assert.deepEqual(observation.arrivals.map(bus => bus.rank), [1, 2, 3]);
    for (const bus of observation.arrivals) {
      assert.equal((Date.parse(bus.eta) - Date.parse(data.capturedAt)) / 1000, bus.etaSecondsNow);
      assert(bus.etaSecondsNow > 0);
    }
  }
  assert(Date.parse(data.freshUntil) > Date.parse(data.capturedAt));
});

test('unknown and malformed stop codes never receive fabricated arrivals', async () => {
  for (const stop of ['99999', '1001', 'invalid']) {
    const live = await request(`/arrivals/${stop}`);
    assert.equal(live.status, 'no_data');
    assert.deepEqual(live.observations, []);
    const sampled = await request(`/sampled-arrivals/${stop}`);
    assert.equal(sampled.status, 'unavailable');
    assert.deepEqual(sampled.observations, []);
  }
});

test('analytics are limited to the selected service and stop cohort', async () => {
  for (const params of [{service: 'MISSING', stop: '01001'}, {service: 'DEMO1', stop: '99999'}, {service: 'DEMO2', stop: '01002'}]) {
    assert.deepEqual((await request('/analytics/peak', params)).rows, []);
    assert.deepEqual(await request('/analytics/trend', params), []);
  }
  assert.equal((await request('/analytics/peak', {service: 'demo1', stop: '01001'})).rows.length, 1);
});

test('analytics use seven completed Singapore calendar days', async () => {
  const data = await request('/analytics/peak', {service: 'DEMO1', stop: '01001'});
  const start = Date.parse(data.start), end = Date.parse(data.endExclusive);
  assert.equal(end - start, 7 * 86400000);
  assert.equal((end + 8 * 3600000) % 86400000, 0);
  assert(end <= Date.now() && Date.now() - end < 86400000);
  assert.equal(data.timezone, 'Asia/Singapore');
});

test('crowding counts, percentages and observed-bin rolling means are consistent', async () => {
  const rows = await request('/analytics/trend', {service: 'DEMO1', stop: '01001'});
  assert(rows.length > 3);
  for (const [index, row] of rows.entries()) {
    assert(row.seatsAvailable >= 0 && row.standingAvailable >= 0 && row.limitedStanding >= 0);
    assert.equal(row.seatsAvailable + row.standingAvailable + row.limitedStanding, row.validSamples);
    assert(Math.abs(row.seatsAvailablePct + row.standingAvailablePct + row.limitedStandingPct - 100) < 0.00001);
    assert(row.meanCrowdingOrdinal >= 0 && row.meanCrowdingOrdinal <= 2);
    const window = rows.slice(Math.max(0, index - 2), index + 1);
    const weighted = window.reduce((sum, item) => sum + item.scoreTotal, 0) /
      window.reduce((sum, item) => sum + item.validSamples, 0);
    assert.equal(row.rollingMeanOrdinal, weighted);
    if (index) assert(Date.parse(row._id.bucket) - Date.parse(rows[index - 1]._id.bucket) >= 1800000);
  }
});

test('mutating one response cannot corrupt later requests', async () => {
  const stop = await request('/stops/search', {query: '01001'});
  stop[0].description = 'Changed';
  assert.equal((await request('/stops/search', {query: '01001'}))[0].description, 'Demo Campus');
  const route = await request('/routes/longest');
  route[0].service_no = 'Changed';
  assert.equal((await request('/routes/longest'))[0].service_no, 'DEMO1');
  const arrival = await request('/arrivals/01001');
  arrival.observations[0].arrivals[0].load = 'Changed';
  assert.equal((await request('/arrivals/01001')).observations[0].arrivals[0].load, 'SEA');
});

test('unsupported endpoints fail explicitly', async () => {
  await assert.rejects(request('/unknown'), /not available in the demo/);
});

test('trip comparison returns one dated synthetic snapshot with route and morning evidence',async()=>{
  const data=await request('/routes/compare',{origin:'01001',destination:'01003'});
  assert.match(data.source,/SYNTHETIC/);
  assert.equal(data.routes.length,2);
  assert.ok(data.routes[0].live.eta<data.routes[1].live.eta);
  assert.equal(data.routes[0].live.load,'SEA');
  assert.equal(data.routes[0].history.status,'available');
  assert.equal(data.routes[0].history.daysWithValidSamples,5);
  assert.ok(data.historyWindow.dateFrom<data.historyWindow.dateTo);
});
