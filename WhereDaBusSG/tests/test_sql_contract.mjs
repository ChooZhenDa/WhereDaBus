// PostgreSQL execution through PGlite (WASM PostgreSQL), without Docker.
// npm install --no-save @electric-sql/pglite
// node --test tests/test_sql_contract.mjs
// PGLITE_MODULE may be an absolute file: URL for a separately installed module.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test, before, after, beforeEach, afterEach } from 'node:test';

const { PGlite } = await import(process.env.PGLITE_MODULE || '@electric-sql/pglite');
const db = new PGlite();
const root = new URL('../', import.meta.url);
const query = async (sql, parameters=[]) => (await db.query(sql, parameters)).rows;
// Execute the actual read-only API coverage statement against PostgreSQL.
const apiSource = await readFile(new URL('backend/api.py', root), 'utf8');
const statusSQL = apiSource.match(/ROUTE_STATUS_SQL = '''([\s\S]*?)'''/)[1];
const samplingSource = await readFile(new URL('backend/sampling_options.py', root), 'utf8');
const chooseSQL = samplingSource.match(/CHOOSE_SAMPLE_STOPS_SQL = '''([\s\S]*?)'''/)[1].replaceAll('%s','$1');
const optionsSQL = samplingSource.match(/CONFIGURED_STOP_OPTIONS_SQL = '''([\s\S]*?)'''/)[1].replaceAll('%s','$1');
const examplesSource = await readFile(new URL('backend/route_examples.py', root), 'utf8');
const examplesSQL = examplesSource.match(/DIRECT_ROUTE_EXAMPLES_SQL = '''([\s\S]*?)'''/)[1];

before(async () => {
  for (const name of ['001_schema.sql', '002_queries.sql']) {
    await db.exec(await readFile(new URL(`sql/${name}`, root), 'utf8'));
  }
  const fixture = JSON.parse(await readFile(new URL('fixtures/static.json', root), 'utf8'));
  for (const stop of fixture.BusStops) {
    await query('INSERT INTO transport.bus_stops (stop_code,road_name,description,latitude,longitude) VALUES ($1,$2,$3,$4,$5)',
      [stop.BusStopCode,stop.RoadName,stop.Description,stop.Latitude,stop.Longitude]);
  }
  for (const service of fixture.BusServices) {
    await query('INSERT INTO transport.bus_services (service_no,direction,operator,category,origin_code,destination_code,loop_description) VALUES ($1,$2,$3,$4,$5,$6,$7)',
      [service.ServiceNo,service.Direction,service.Operator,service.Category,service.OriginCode,service.DestinationCode,service.LoopDesc]);
  }
  for (const route of fixture.BusRoutes) {
    await query('INSERT INTO transport.bus_routes VALUES ($1,$2,$3,$4,$5)',
      [route.ServiceNo,route.Direction,route.StopSequence,route.BusStopCode,route.Distance]);
  }
});
beforeEach(async () => { await db.exec('BEGIN'); });
afterEach(async () => { await db.exec('ROLLBACK'); });
after(async () => { await db.close(); });

async function rejected(sql, expectedCode) {
  await db.exec('SAVEPOINT invalid_input');
  await assert.rejects(query(sql), error => error.code === expectedCode);
  await db.exec('ROLLBACK TO SAVEPOINT invalid_input; RELEASE SAVEPOINT invalid_input');
}

test('schema preserves the exact five-column importer contract and loop occurrences', async () => {
  const columns = await query("SELECT column_name FROM information_schema.columns WHERE table_schema='transport' AND table_name='bus_routes' ORDER BY ordinal_position");
  assert.deepEqual(columns.map(x=>x.column_name), ['service_no','direction','stop_sequence','stop_code','distance_km']);
  const repeat = await query("SELECT stop_code FROM transport.bus_routes WHERE service_no='DEMO1' AND stop_code='01001'");
  assert.equal(repeat.length,2);
});

test('route examples are distinct forward journeys accepted by the actual direct query', async () => {
  const examples = await query(examplesSQL);
  assert.equal(examples.length, 1); // Both fixture services connect the same pair.
  assert.equal(examples[0].origin_code, '01001');
  assert.equal(examples[0].destination_code, '01003'); // A loop does not suggest boarding/alighting at the same stop.
  assert.equal(examples[0].origin_description, 'Demo Campus');
  assert.equal(examples[0].destination_description, 'Demo Station');
  for (const example of examples) {
    const matches = await query('SELECT * FROM transport.direct_routes($1,$2)', [example.origin_code, example.destination_code]);
    assert.ok(matches.some(row => row.service_no===example.service_no && row.direction===example.direction));
  }
});

test('route examples exclude retired-stop directions and absent routes', async () => {
  await query("UPDATE transport.bus_stops SET is_active=false WHERE stop_code='01002'");
  const examples = await query(examplesSQL);
  assert.deepEqual(examples.map(x=>x.service_no), ['DEMO2']);
  await query('DELETE FROM transport.bus_routes');
  assert.deepEqual(await query(examplesSQL), []);
});

test('route examples are bounded and stable without single-stop or inactive services', async () => {
  for (let i=0; i<8; i++) {
    const origin=String(30000+i*2), destination=String(30001+i*2), service='QA'+i;
    for (const code of [origin,destination]) await query('INSERT INTO transport.bus_stops(stop_code,road_name,description,latitude,longitude) VALUES($1,$2,$3,$4,$5)', [code,'QA Road','QA '+code,1.3,103.8]);
    await query('INSERT INTO transport.bus_services(service_no,direction,operator) VALUES($1,1,$2)',[service,'QA']);
    await query('INSERT INTO transport.bus_routes VALUES($1,1,1,$2,0)',[service,origin]);
    if (i!==7) await query('INSERT INTO transport.bus_routes VALUES($1,1,2,$2,$3)',[service,destination,i+5]);
  }
  await query("UPDATE transport.bus_services SET is_active=false WHERE service_no='QA6'");
  const examples=await query(examplesSQL);
  assert.equal(examples.length,5);
  assert.deepEqual(examples,await query(examplesSQL));
  assert.ok(examples.every(x=>x.service_no!=='QA6' && x.service_no!=='QA7'));
  assert.equal(new Set(examples.map(x=>x.origin_code+':'+x.destination_code)).size,5);
  for (const example of examples) {
    const matches=await query('SELECT * FROM transport.direct_routes($1,$2)',[example.origin_code,example.destination_code]);
    assert.ok(matches.some(row=>row.service_no===example.service_no && row.direction===example.direction));
  }
});

test('automatic sampling chooses actual active stops with deduplicated service lists', async () => {
  const rows=await query(chooseSQL,[[]]);
  assert.deepEqual(rows.map(x=>x.stopCode),['01001','01003','01002']);
  assert.deepEqual(rows.find(x=>x.stopCode==='01001').services,['DEMO1','DEMO2']);
  assert.equal(rows[0].description,'Demo Campus');
  assert.equal((await query(chooseSQL,[['DEMO2']])).length,2);
  assert.deepEqual(await query(chooseSQL,[['NO_MATCH']]),[]);
});

test('automatic sampling is bounded, stable and spreads known stops across cells', async () => {
  for(let i=0;i<15;i++)await query('INSERT INTO transport.bus_stops(stop_code,road_name,description,latitude,longitude) VALUES($1,$2,$3,$4,$5)',
    [String(20000+i),'Test road','Synthetic test stop',1.4+i*0.021,103.9]);
  const first=await query(chooseSQL,[[]]), second=await query(chooseSQL,[[]]);
  assert.equal(first.length,10);
  assert.deepEqual(first,second);
  assert.equal(new Set(first.map(x=>x.stopCode)).size,10);
  await query("UPDATE transport.bus_stops SET is_active=false WHERE stop_code='01001'");
  assert.ok((await query(chooseSQL,[[]])).every(x=>x.stopCode!=='01001'));
});

test('automatic sampling permits stop-only data and returns nothing for no active stops', async () => {
  await query('DELETE FROM transport.bus_routes');
  const rows=await query(chooseSQL,[[]]);
  assert.equal(rows.length,3);
  assert.ok(rows.every(x=>x.services.length===0));
  assert.deepEqual(await query(chooseSQL,[['DEMO1']]),[]);
  await query('UPDATE transport.bus_stops SET is_active=false');
  assert.deepEqual(await query(chooseSQL,[[]]),[]);
});

test('configured choices preserve leading zeroes and exclude unknown or inactive stops', async () => {
  const rows=await query(optionsSQL,[['01001','99999']]);
  assert.deepEqual(rows.map(x=>x.stopCode),['01001']);
  assert.deepEqual(rows[0].services,['DEMO1','DEMO2']);
  await query("UPDATE transport.bus_services SET is_active=false WHERE service_no='DEMO1'");
  assert.deepEqual((await query(optionsSQL,[['01001']]))[0].services,['DEMO2']);
});

test('coverage reports a missing route direction without hiding available routes', async () => {
  await query("DELETE FROM transport.bus_routes WHERE service_no='DEMO2'");
  const [coverage] = await query(statusSQL);
  assert.equal(Number(coverage.serviceDirections),2);
  assert.equal(Number(coverage.availableDirections),1);
  assert.equal(Number(coverage.unavailableDirections),1);
  assert.deepEqual(coverage.unavailableExamples,[{serviceNo:'DEMO2',direction:1}]);
  assert.equal(coverage.lastImportAt,null);
});

test('coverage keeps retired-stop directions partial and handles empty network', async () => {
  await query("UPDATE transport.bus_stops SET is_active=false WHERE stop_code='01002'");
  const [partial] = await query(statusSQL);
  assert.equal(Number(partial.availableDirections),2);
  assert.equal(Number(partial.gapFreeDirections),1);
  assert.equal(Number(partial.partialDirections),1);
  assert.equal(Number(partial.unavailableDirections),0);
  assert.deepEqual(partial.unavailableExamples,[]);
  assert.deepEqual(partial.partialExamples,[{serviceNo:'DEMO1',direction:1}]);
  await query('DELETE FROM transport.bus_routes');
  await query('DELETE FROM transport.bus_services');
  const [empty] = await query(statusSQL);
  assert.equal(Number(empty.availableDirections),0);
  assert.equal(Number(empty.serviceDirections),0);
  assert.deepEqual(empty.unavailableExamples,[]);
});

test('stop, route, foreign-key and uniqueness constraints reject invalid data', async () => {
  await rejected("INSERT INTO transport.bus_stops (stop_code,road_name,description,latitude,longitude) VALUES ('1001','Test','Test',1,103)", '23514');
  await rejected("UPDATE transport.bus_stops SET latitude='NaN' WHERE stop_code='01001'", '23514');
  await rejected("INSERT INTO transport.bus_routes VALUES ('DEMO1',1,0,'01001',0)", '23514');
  await rejected("INSERT INTO transport.bus_routes VALUES ('DEMO1',1,5,'01001',-1)", '23514');
  await rejected("INSERT INTO transport.bus_routes VALUES ('DEMO1',1,5,'99999',5)", '23503');
  await rejected("INSERT INTO transport.bus_routes VALUES ('DEMO1',1,1,'01001',0)", '23505');
});

test('longest routes retain route endpoints, length and loop stop count', async () => {
  const rows=await query('SELECT * FROM transport.longest_routes()');
  assert.deepEqual(rows.map(x=>x.service_no),['DEMO1','DEMO2']);
  assert.equal(Number(rows[0].route_length_km),4);
  assert.equal(Number(rows[0].stop_count),4);
  assert.equal(rows[0].origin_code,'01001');
  assert.equal(rows[0].destination_code,'01001');
});

test('road search is case-insensitive, literal and deduplicated', async () => {
  assert.equal((await query("SELECT * FROM transport.routes_on_road('  DEMO road  ')")).length,2);
  assert.equal((await query("SELECT * FROM transport.routes_on_road('%')")).length,0);
  assert.equal((await query("SELECT * FROM transport.routes_on_road(' ')")).length,0);
});

test('direct journeys preserve direction and valid loop returns', async () => {
  const forward=await query("SELECT * FROM transport.direct_routes('01001','01003')");
  assert.deepEqual(forward.map(x=>x.service_no),['DEMO1','DEMO2']);
  assert.equal(Number(forward[0].distance_km),2.5);
  assert.equal(forward[0].origin_sequence,1);
  assert.equal(forward[0].destination_sequence,3);
  const reverse=await query("SELECT * FROM transport.direct_routes('01003','01001')");
  assert.deepEqual(reverse.map(x=>x.service_no),['DEMO1']);
  assert.equal(Number(reverse[0].distance_km),1.5);
  const loop=await query("SELECT * FROM transport.direct_routes('01001','01001')");
  assert.deepEqual(loop.map(x=>x.service_no),['DEMO1']);
  assert.equal(Number(loop[0].distance_km),4);
});

test('multiple valid loop occurrences choose one shortest forward journey', async () => {
  await query("INSERT INTO transport.bus_routes VALUES ('DEMO1',1,5,'01003',5)");
  const result=await query("SELECT * FROM transport.direct_routes('01001','01003') WHERE service_no='DEMO1'");
  assert.equal(result.length,1);
  assert.equal(result[0].origin_sequence,4);
  assert.equal(result[0].destination_sequence,5);
  assert.equal(Number(result[0].distance_km),1);
});

test('inactive stops cannot form a direct journey through a broken segment', async () => {
  await query("UPDATE transport.bus_stops SET is_active=false WHERE stop_code='01002'");
  const result=await query("SELECT * FROM transport.direct_routes('01001','01003')");
  assert.deepEqual(result.map(x=>x.service_no),['DEMO2']);
  assert.deepEqual((await query('SELECT * FROM transport.longest_routes()')).map(x=>x.service_no),['DEMO2']);
});

test('inactive services are excluded from every route-facing query', async () => {
  await query("UPDATE transport.bus_services SET is_active=false WHERE service_no='DEMO1'");
  assert.deepEqual((await query('SELECT * FROM transport.longest_routes()')).map(x=>x.service_no),['DEMO2']);
  assert.deepEqual((await query("SELECT * FROM transport.routes_on_road('demo')")).map(x=>x.service_no),['DEMO2']);
  assert.deepEqual((await query("SELECT * FROM transport.direct_routes('01001','01003')")).map(x=>x.service_no),['DEMO2']);
  const nearby=await query('SELECT * FROM transport.nearby_stops(1.300,103.800,500)');
  assert.deepEqual(nearby.find(x=>x.stop_code==='01001').services,['DEMO2']);
  assert.equal(nearby.find(x=>x.stop_code==='01002').services,null);
});

test('nearby distances are metre-based, sorted, radius-filtered and keep leading zeroes', async () => {
  const rows=await query('SELECT * FROM transport.nearby_stops(1.300,103.800,500)');
  assert.deepEqual(rows.map(x=>x.stop_code),['01001','01002','01003']);
  assert.equal(rows[0].distance_m,0);
  assert.ok(rows[1].distance_m>150&&rows[1].distance_m<160);
  assert.ok(rows[2].distance_m>310&&rows[2].distance_m<320);
  assert.deepEqual(rows[0].services,['DEMO1','DEMO2']);
  assert.equal((await query('SELECT * FROM transport.nearby_stops(1.300,103.800,100)')).length,1);
  assert.equal((await query('SELECT * FROM transport.nearby_stops(0,0,500)')).length,0);
});

test('stop-only data stays discoverable with explicitly unknown route coverage', async () => {
  await query('DELETE FROM transport.bus_routes');
  const rows=await query('SELECT * FROM transport.nearby_stops(1.300,103.800,500)');
  assert.equal(rows.length,3);
  assert.ok(rows.every(row=>row.services===null));
  assert.equal((await query('SELECT * FROM transport.longest_routes()')).length,0);
});

test('nearby rejects invalid coordinates, nonfinite values and invalid radius', async () => {
  await rejected('SELECT * FROM transport.nearby_stops(91,103,500)','22023');
  await rejected("SELECT * FROM transport.nearby_stops('NaN',103,500)",'22023');
  await rejected('SELECT * FROM transport.nearby_stops(1,181,500)','22023');
  await rejected('SELECT * FROM transport.nearby_stops(1,103,5001)','22023');
  await rejected('SELECT * FROM transport.nearby_stops(1,103,0)','22023');
});

test('user email uniqueness is case-insensitive and favourites cascade on user deletion', async () => {
  const rows=await query("INSERT INTO transport.users (auth_subject,email,display_name) VALUES ('test|1','a@example.test','A') RETURNING user_id");
  await rejected("INSERT INTO transport.users (auth_subject,email,display_name) VALUES ('test|2','A@example.test','B')",'23505');
  await query("INSERT INTO transport.favourite_routes (user_id,service_no,direction) VALUES ($1,'DEMO1',1)",[rows[0].user_id]);
  await query('DELETE FROM transport.users WHERE user_id=$1',[rows[0].user_id]);
  assert.equal((await query('SELECT * FROM transport.favourite_routes')).length,0);
});

test('a missing intermediate stop keeps known endpoints but withholds exact journey metrics', async () => {
  await query("DELETE FROM transport.bus_routes WHERE service_no='DEMO1' AND stop_sequence=2");
  const journeys=await query("SELECT * FROM transport.direct_routes('01001','01003')");
  assert.deepEqual(journeys.map(x=>x.service_no),['DEMO2','DEMO1']);
  const partial=journeys[1];
  assert.equal(partial.segment_coverage,'partial');
  assert.equal(partial.route_coverage,'partial');
  assert.equal(partial.stops_between,null);
  assert.equal(partial.distance_km,null);
  assert.equal(partial.observed_stops_between,1);
  const [coverage]=await query(statusSQL);
  assert.equal(Number(coverage.availableDirections),2);
  assert.equal(Number(coverage.gapFreeDirections),1);
  assert.equal(Number(coverage.partialDirections),1);
  assert.equal(Number(coverage.unavailableDirections),0);
  assert.deepEqual(coverage.partialExamples,[{serviceNo:'DEMO1',direction:1}]);
  const road=(await query("SELECT * FROM transport.routes_on_road('Demo')")).find(x=>x.service_no==='DEMO1');
  assert.equal(road.stop_count,null);
  assert.equal(road.route_length_km,null);
  assert.equal(Number(road.observed_stop_count),3);
  assert.equal(Number(road.known_distance_span_km),4);
  assert.deepEqual((await query('SELECT * FROM transport.longest_routes()')).map(x=>x.service_no),['DEMO2']);
  assert.deepEqual((await query(examplesSQL)).map(x=>x.service_no),['DEMO2']);
});

test('a gap before the selected interval no longer hides a usable segment', async () => {
  await query("DELETE FROM transport.bus_routes WHERE service_no='DEMO1' AND stop_sequence=1");
  const [journey]=await query("SELECT * FROM transport.direct_routes('01002','01003')");
  assert.equal(journey.service_no,'DEMO1');
  assert.equal(journey.route_coverage,'partial');
  assert.equal(journey.segment_coverage,'no_detected_gaps');
  assert.equal(journey.stops_between,1);
  assert.equal(journey.observed_stops_between,1);
  assert.equal(Number(journey.distance_km),1.5);
});

test('a gap elsewhere in a loop does not invalidate its continuous return segment', async () => {
  await query("DELETE FROM transport.bus_routes WHERE service_no='DEMO1' AND stop_sequence=2");
  const [journey]=await query("SELECT * FROM transport.direct_routes('01003','01001')");
  assert.equal(journey.origin_sequence,3);
  assert.equal(journey.destination_sequence,4);
  assert.equal(journey.segment_coverage,'no_detected_gaps');
  assert.equal(journey.route_coverage,'partial');
  assert.equal(journey.stops_between,1);
  assert.equal(Number(journey.distance_km),1.5);
});

test('known terminal mismatch marks a truncated direction partial without hiding earlier segments', async () => {
  await query("DELETE FROM transport.bus_routes WHERE service_no='DEMO1' AND stop_sequence=4");
  const [summary]=await query("SELECT * FROM transport.active_route_summaries WHERE service_no='DEMO1'");
  assert.equal(summary.route_coverage,'partial');
  assert.equal(summary.route_length_km,null);
  const [journey]=await query("SELECT * FROM transport.direct_routes('01001','01002')");
  assert.equal(journey.segment_coverage,'no_detected_gaps');
  assert.equal(journey.route_coverage,'partial');
  assert.equal(journey.stops_between,1);
});

test('continuous segment wins over a shorter partial loop alternative', async () => {
  await query("INSERT INTO transport.bus_routes VALUES ('DEMO1',1,6,'01003',4.5)");
  const [journey]=await query("SELECT * FROM transport.direct_routes('01001','01003') WHERE service_no='DEMO1'");
  assert.equal(journey.origin_sequence,1);
  assert.equal(journey.destination_sequence,3);
  assert.equal(journey.segment_coverage,'no_detected_gaps');
  assert.equal(Number(journey.distance_km),2.5);
});

test('no active route occurrences means unavailable rather than partial', async () => {
  await query('UPDATE transport.bus_stops SET is_active=false');
  const [coverage]=await query(statusSQL);
  assert.equal(Number(coverage.availableDirections),0);
  assert.equal(Number(coverage.partialDirections),0);
  assert.equal(Number(coverage.gapFreeDirections),0);
  assert.equal(Number(coverage.unavailableDirections),2);
  assert.deepEqual(await query("SELECT * FROM transport.routes_on_road('Demo')"),[]);
  assert.deepEqual(await query("SELECT * FROM transport.direct_routes('01001','01003')"),[]);
});

test('very large sequence gaps remain bounded and never become an invented stop count', async () => {
  await query("UPDATE transport.bus_routes SET stop_sequence=2147483647 WHERE service_no='DEMO2' AND stop_sequence=2");
  const [journey]=await query("SELECT * FROM transport.direct_routes('01001','01003') WHERE service_no='DEMO2'");
  assert.equal(journey.segment_coverage,'partial');
  assert.equal(journey.stops_between,null);
  assert.equal(journey.observed_stops_between,1);
  assert.equal(journey.distance_km,null);
});

test('v1 query migration preserves rows and rolls back query changes on failure', async () => {
  const prepare=await readFile(new URL('sql/003_prepare_coverage_migration.sql',root),'utf8');
  const legacy=await readFile(new URL('tests/fixtures/schema_v1_queries.sql',root),'utf8');
  const current=await readFile(new URL('sql/002_queries.sql',root),'utf8');
  await db.exec(prepare);
  await db.exec(legacy);
  await db.exec('CREATE TABLE transport.schema_version(version integer PRIMARY KEY,applied_at timestamptz NOT NULL DEFAULT now()); INSERT INTO transport.schema_version(version) VALUES(1)');
  const beforeRows=await query('SELECT * FROM transport.bus_routes ORDER BY service_no,direction,stop_sequence');
  assert.equal(Object.hasOwn((await query("SELECT * FROM transport.direct_routes('01001','01003')"))[0],'segment_coverage'),false);
  // Simulate a failed migration inside the same transaction boundary used by bootstrap.
  await db.exec('SAVEPOINT failed_upgrade');
  await db.exec(prepare);
  await assert.rejects(query('SELECT * FROM transport.not_a_table'),e=>e.code==='42P01');
  await db.exec('ROLLBACK TO SAVEPOINT failed_upgrade; RELEASE SAVEPOINT failed_upgrade');
  assert.equal(Object.hasOwn((await query("SELECT * FROM transport.direct_routes('01001','01003')"))[0],'segment_coverage'),false);
  assert.deepEqual(await query('SELECT version FROM transport.schema_version'),[{version:1}]);
  await db.exec(prepare);
  await db.exec(current);
  await query('UPDATE transport.schema_version SET version=2,applied_at=now() WHERE version=1');
  assert.deepEqual(await query('SELECT * FROM transport.bus_routes ORDER BY service_no,direction,stop_sequence'),beforeRows);
  assert.deepEqual(await query('SELECT version FROM transport.schema_version'),[{version:2}]);
  assert.equal((await query("SELECT * FROM transport.direct_routes('01001','01003')"))[0].segment_coverage,'no_detected_gaps');
  // Version-aware bootstrap skips scripts on v2; exercise read-only repeat checks here.
  assert.equal((await query('SELECT version FROM transport.schema_version ORDER BY version'))[0].version,2);
  assert.deepEqual(await query('SELECT * FROM transport.bus_routes ORDER BY service_no,direction,stop_sequence'),beforeRows);
});
