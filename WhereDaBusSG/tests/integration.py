"""Real-engine checks. Requires fresh *_test databases; never runs in production.
Run: python -m tests.integration (after creating empty wheredabus_test database).
"""
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import psycopg
from backend.storage import sql_connection,mongo_database,import_static,initialize_mongo
from backend.domain import normalize_arrivals
from backend.analytics import morning_peak,half_hour_trend,crowded_stops,previous_seven_days
from backend.live import MongoLiveCache

root = Path(__file__).resolve().parents[1]
assert os.environ['MONGO_DB'].endswith('_test'), 'Use an isolated *_test Mongo database'
with sql_connection() as conn:
    assert conn.execute('SELECT current_database()').fetchone()[0].endswith('_test')
    assert conn.execute("SELECT to_regnamespace('transport')").fetchone()[0] is None, 'SQL test DB must be empty'
    for script in ('001_schema.sql','002_queries.sql'):
        conn.execute((root/'sql'/script).read_text())

snapshot = json.loads((root/'fixtures/static.json').read_text())
import_static(snapshot=snapshot)
with sql_connection() as conn:
    assert conn.execute('SELECT route_length_km FROM transport.longest_routes() LIMIT 1').fetchone()[0] == 4
    assert len(conn.execute("SELECT * FROM transport.routes_on_road('demo road')").fetchall()) == 2
    assert len(conn.execute("SELECT * FROM transport.direct_routes('01001','01003')").fetchall()) == 2
    # DEMO2 cannot be ridden backwards; DEMO1 loops back to the first stop.
    reverse = conn.execute("SELECT service_no FROM transport.direct_routes('01003','01001')").fetchall()
    assert reverse == [('DEMO1',)]
    assert len(conn.execute('SELECT * FROM transport.nearby_stops(1.300,103.800,1000)').fetchall()) == 3
    try:
        with conn.transaction():
            conn.execute("INSERT INTO transport.bus_routes VALUES ('DEMO1',1,0,'01001',-1)")
        raise AssertionError('CHECK should reject invalid route')
    except psycopg.errors.CheckViolation:
        pass
    user_id = conn.execute("INSERT INTO transport.users(auth_subject,email,display_name) VALUES ('test|1','a@example.test','A') RETURNING user_id").fetchone()[0]
    try:
        with conn.transaction():
            conn.execute("INSERT INTO transport.users(auth_subject,email,display_name) VALUES ('test|2','A@example.test','B')")
        raise AssertionError('Case-insensitive unique index should reject email')
    except psycopg.errors.UniqueViolation:
        pass
    conn.execute("INSERT INTO transport.favourite_routes(user_id,service_no,direction) VALUES (%s,'DEMO1',1)",(user_id,))
    conn.execute('DELETE FROM transport.users WHERE user_id=%s',(user_id,))
    assert conn.execute('SELECT count(*) FROM transport.favourite_routes').fetchone()[0] == 0

client,db = mongo_database()
try:
    assert not db.list_collection_names(), 'Mongo test DB must be empty'
    initialize_mongo(db)
    start,end = previous_seven_days()
    payload = json.loads((root/'fixtures/arrival.json').read_text())
    docs = []
    for day in range(7):
        for index,load in enumerate(('SEA','SDA','LSD')):
            captured = start+timedelta(days=day,hours=7,minutes=30*index)
            payload['Services'][0]['NextBus'].update(Load=load,EstimatedArrival=(captured+timedelta(minutes=5)).isoformat())
            docs.extend(normalize_arrivals(payload,captured,f'{day}-{index}'))
    # 21 polls, of which 15 are weekdays. Add exact replay duplicates.
    db.arrival_observations.insert_many(docs)
    db.arrival_observations.insert_many([{k:v for k,v in d.items() if k!='_id'} for d in docs])
    result = list(db.arrival_observations.aggregate(morning_peak('DEMO1',start,end,'01001')))[0]
    assert result['observations'] == 15 and result['validSamples'] == 15
    assert result['meanCrowdingOrdinal'] == 1 and result['daysWithValidSamples'] == 5
    assert abs(result['limitedStandingPct']-100/3)<1e-8
    trend = list(db.arrival_observations.aggregate(half_hour_trend('DEMO1',start,end,'01001')))
    assert len(trend)==21 and abs(trend[2]['rollingMeanOrdinal']-1)<1e-8
    assert len(list(db.arrival_observations.aggregate(crowded_stops('DEMO1',start,end,min_samples=15))))==1
    assert not list(db.arrival_observations.aggregate(crowded_stops('DEMO1',start,end,min_samples=30)))
    live = MongoLiveCache(db)
    now = datetime.now(timezone.utc)
    assert live.acquire('83139','owner-1',now)
    assert not live.acquire('83139','owner-2',now)
    assert live.acquire('83139','owner-2',now+timedelta(seconds=31))
    snapshot = {'_id':'83139','capturedAt':now+timedelta(seconds=31),
                'freshUntil':now+timedelta(seconds=51),
                'expiresAt':now+timedelta(seconds=151),'observations':[]}
    assert not live.complete('83139','owner-1',snapshot,now+timedelta(seconds=31))
    assert live.complete('83139','owner-2',snapshot,now+timedelta(seconds=31))
    assert not live.acquire('83139','owner-3',now+timedelta(seconds=40))
    assert live.acquire('83139','owner-3',now+timedelta(seconds=52))
    live.fail('83139','owner-3',now+timedelta(seconds=52),90)
    assert not live.acquire('83139','owner-4',now+timedelta(seconds=60))
    print('PASS: real SQL constraints, CRUD, four queries, Mongo time-series and three aggregation pipelines')
    print('PASS: MongoDB live-cache leases, fencing, freshness and cooldown')
finally:
    client.close()
