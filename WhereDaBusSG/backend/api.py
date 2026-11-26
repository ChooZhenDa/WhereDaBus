"""Commuter API and same-origin GUI. Preserve deployment authentication.

Saved journeys are private to the browser that created them. Database
configuration remains server-side, and unavailable services return safe errors.
"""
from contextlib import asynccontextmanager
from datetime import date,datetime,timezone
import os
import re
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, Query, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg import Error as SQLError
from psycopg.rows import dict_row
from pymongo.errors import PyMongoError
from .analytics import morning_peak,half_hour_trend
from .analytics_window import analytics_window
from .storage import sql_connection,mongo_database
from .live import LiveArrivals,LiveUnavailable,MongoLiveCache
from .lta import LTAClient
from .sampling_options import CONFIGURED_STOP_OPTIONS_SQL, sampling_selection
from .route_examples import DIRECT_ROUTE_EXAMPLES_SQL
from .journeys import router as journeys_router
from .feedback import add_feedback_keys, router as feedback_router
from .route_comparison import compare_routes

@asynccontextmanager
async def lifespan(app):
    # The GUI and setup guidance must remain available before databases are set
    # up. Mongo connects lazily; request handlers report connection failures.
    client = None
    app.state.mongo = None
    app.state.live = None
    try:
        if os.getenv('MONGO_URI', '').strip():
            try:
                client,db = mongo_database()
                app.state.mongo = db
                key = os.getenv('LTA_ACCOUNT_KEY','').strip()
                app.state.live = LiveArrivals(MongoLiveCache(db),
                    LTAClient(key,timeout=8,attempts=1) if key else None)
            except (PyMongoError, ValueError, KeyError):
                # Never return connection strings, driver errors, or API keys.
                app.state.mongo = None
                app.state.live = None
        yield
    finally:
        if client is not None:
            client.close()

app = FastAPI(title='WhereDaBus SG',version='1.6.0',lifespan=lifespan)
app.include_router(journeys_router)
app.include_router(feedback_router)
WEB_ROOT = Path(__file__).resolve().parent / 'web'
app.mount('/ui-assets', StaticFiles(directory=WEB_ROOT, check_dir=False), name='ui-assets')


@app.get('/', include_in_schema=False)
def gui():
    return FileResponse(WEB_ROOT / 'index.html', headers={'Cache-Control': 'no-cache'})


@app.get('/ui/config')
def ui_config():
    configured = {name: bool(os.getenv(variable, '').strip()) for name, variable in (
        ('sql', 'SQL_DSN'), ('mongo', 'MONGO_URI'), ('lta', 'LTA_ACCOUNT_KEY'))}
    return {'mode': 'live', 'configured': configured,
            'ready': all(configured.values()), 'timezone': 'Asia/Singapore'}


@app.exception_handler(SQLError)
async def sql_unavailable(request, error):
    return JSONResponse(status_code=503, content={'detail': {
        'status': 'unavailable', 'reason': 'sql_unavailable',
        'message': 'Route and stop data is unavailable. Check the database connection and static import.'}},
        headers={'Retry-After': '10'})


@app.exception_handler(PyMongoError)
async def mongo_unavailable(request, error):
    return JSONResponse(status_code=503, content={'detail': {
        'status': 'unavailable', 'reason': 'mongo_unavailable',
        'message': 'Arrival and analytics data is temporarily unavailable. Check the MongoDB connection.'}},
        headers={'Retry-After': '10'})


def require_mongo():
    database = getattr(app.state, 'mongo', None)
    if database is None:
        raise HTTPException(503, detail={
            'status': 'unavailable', 'reason': 'mongo_not_configured',
            'message': 'Arrival data is not connected. Configure the server database to use this feature.'})
    return database

def sql_query(statement, params=()):
    if not os.getenv('SQL_DSN', '').strip():
        raise HTTPException(503, detail={
            'status': 'unavailable', 'reason': 'sql_not_configured',
            'message': 'Route and stop data is not connected. Configure the server database to use this feature.'})
    with sql_connection() as conn:
        conn.execute("SET LOCAL statement_timeout = '5s'")
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(statement,params)
            return cur.fetchall()

@app.get('/health')
def health():
    sql_query('SELECT 1')
    require_mongo().command('ping')
    return {'status':'ok'}


ROUTE_STATUS_SQL = '''WITH available AS (
    SELECT service_no, direction, route_coverage FROM transport.active_route_summaries
), missing AS (
    SELECT s.service_no, s.direction FROM transport.bus_services s
    WHERE s.is_active AND NOT EXISTS (
        SELECT 1 FROM available a
        WHERE a.service_no=s.service_no AND a.direction=s.direction)
), partial AS (
    SELECT service_no,direction FROM available WHERE route_coverage='partial'
)
SELECT (SELECT count(*) FROM transport.bus_stops WHERE is_active) AS stops,
       (SELECT count(*) FROM transport.bus_services WHERE is_active) AS "serviceDirections",
       (SELECT count(*) FROM available) AS "availableDirections",
       (SELECT count(*) FROM available WHERE route_coverage='no_detected_gaps') AS "gapFreeDirections",
       (SELECT count(*) FROM partial) AS "partialDirections",
       (SELECT count(*) FROM missing) AS "unavailableDirections",
       COALESCE((SELECT json_agg(m ORDER BY m."serviceNo", m.direction)
           FROM (SELECT service_no AS "serviceNo", direction FROM missing
                 ORDER BY service_no, direction LIMIT 30) m), '[]'::json)
           AS "unavailableExamples",
       COALESCE((SELECT json_agg(m ORDER BY m."serviceNo", m.direction)
           FROM (SELECT service_no AS "serviceNo", direction FROM partial
                 ORDER BY service_no, direction LIMIT 30) m), '[]'::json)
           AS "partialExamples",
       (SELECT max(committed_at) FROM transport.import_runs) AS "lastImportAt"'''


@app.get('/data/status')
def data_status(section: Literal['all', 'routes', 'sampling']='all'):
    """Bounded, read-only coverage checks; each store can fail independently.

    Configuration is not evidence that the sampler process is running. Only
    unexpired snapshots are reported as recent. Never expose connection values.
    """
    result = {'timezone': 'Asia/Singapore'}
    if section in ('all', 'routes'):
        try:
            result['routes'] = {'status': 'available', **sql_query(ROUTE_STATUS_SQL)[0]}
        except (SQLError, HTTPException):
            result['routes'] = {'status': 'unavailable'}
    if section in ('all', 'sampling'):
        stops = sorted(set(x.strip() for x in os.getenv('SAMPLE_STOPS', '').split(',') if x.strip()))
        services = sorted(set(x.strip() for x in os.getenv('SAMPLE_SERVICES', '').split(',') if x.strip()))
        valid = (1 <= len(stops) <= 20 and all(re.fullmatch(r'[0-9]{5}', x) for x in stops)
                 and all(re.fullmatch(r'[A-Za-z0-9]{1,12}', x) for x in services))
        sampling = {'configured': bool(valid), 'stops': stops if valid else [],
                    'services': services if valid else [], 'storageStatus': 'not_checked',
                    'freshStops': [], 'latestSnapshotAt': None}
        if valid:
            try:
                now = datetime.now(timezone.utc)
                snapshots = list(require_mongo().arrival_latest.find(
                    {'_id': {'$in': stops}},
                    {'_id': 1, 'capturedAt': 1, 'expiresAt': 1}).limit(20).max_time_ms(3000))
                captured = [doc['capturedAt'] for doc in snapshots if doc.get('capturedAt')]
                sampling.update(storageStatus='available',
                    latestSnapshotAt=max(captured) if captured else None,
                    freshStops=sorted(doc['_id'] for doc in snapshots
                        if doc.get('expiresAt') and doc['expiresAt'] > now))
            except (PyMongoError, HTTPException):
                sampling['storageStatus'] = 'unavailable'
        result['sampling'] = sampling
    return jsonable_encoder(result)

@app.get('/sampling/options')
def sampling_options():
    """Names and service choices for this installation's configured sample.

    Read SQL and Mongo independently. A fresh snapshot can reveal a service
    even when that service's static route direction was excluded at import.
    """
    codes, service_filter = sampling_selection()
    result = {'configured': bool(codes), 'stops': [],
              'services': service_filter, 'source': 'configured'}
    if not codes:
        return result
    options = {code: {'stopCode': code, 'description': f'Bus stop {code}',
                      'roadName': '', 'services': set()} for code in codes}
    warnings = []
    try:
        rows = sql_query(CONFIGURED_STOP_OPTIONS_SQL, (codes, codes))
        for row in rows:
            code = row['stopCode']
            if code in options:
                options[code].update(description=row['description'], roadName=row['roadName'])
                options[code]['services'].update(row.get('services') or [])
    except (SQLError, HTTPException):
        warnings.append('Stop names and route service lists are temporarily unavailable.')
    try:
        now = datetime.now(timezone.utc)
        snapshots = list(require_mongo().arrival_latest.find(
            {'_id': {'$in': codes}, 'expiresAt': {'$gt': now}},
            {'_id': 1, 'observations.meta.serviceNo': 1}).limit(20).max_time_ms(3000))
        for snapshot in snapshots:
            if snapshot.get('_id') in options:
                for observation in snapshot.get('observations', []):
                    number = observation.get('meta', {}).get('serviceNo')
                    if isinstance(number, str) and re.fullmatch(r'[A-Za-z0-9]{1,12}', number):
                        options[snapshot['_id']]['services'].add(number)
    except (PyMongoError, HTTPException):
        warnings.append('Recent sampled services are temporarily unavailable.')
    for option in options.values():
        option['services'] = sorted(number for number in option['services']
                                    if not service_filter or number in service_filter)
    result['stops'] = list(options.values())
    if warnings:
        result['warning'] = ' '.join(warnings)
    return result


@app.get('/routes/longest')
def longest():
    return sql_query('SELECT * FROM transport.longest_routes()')

@app.get('/routes/examples')
def route_examples():
    """At most five distinct, forward journeys in the imported network.

    These are connectivity examples, not a claim that the service runs now.
    Current arrivals can be checked at the returned origin stop.
    """
    return sql_query(DIRECT_ROUTE_EXAMPLES_SQL)

@app.get('/routes/road')
def road(name: str=Query(min_length=1,max_length=150)):
    return sql_query('SELECT * FROM transport.routes_on_road(%s)',(name,))

@app.get('/routes/direct')
def direct(origin: str=Query(pattern=r'^[0-9]{5}$'),destination: str=Query(pattern=r'^[0-9]{5}$')):
    return sql_query('SELECT * FROM transport.direct_routes(%s,%s)',(origin,destination))

@app.get('/routes/compare')
def compare(origin: str=Query(pattern=r'^[0-9]{5}$'), destination: str=Query(pattern=r'^[0-9]{5}$'),
            date_from: date | None = None, date_to: date | None = None):
    return compare_routes(origin, destination, sql_query, getattr(app.state, 'live', None),
                          getattr(app.state, 'mongo', None), date_from, date_to)

@app.get('/stops/nearby')
def nearby(lat: float=Query(ge=-90,le=90),lon: float=Query(ge=-180,le=180),
           radius: float=Query(default=500,ge=1,le=5000)):
    return sql_query('SELECT * FROM transport.nearby_stops(%s,%s,%s)',(lat,lon,radius))


@app.get('/stops/search')
def search_stops(query: str=Query(min_length=1,max_length=150),
                 limit: int=Query(default=10,ge=1,le=50)):
    query = query.strip()
    if not query:
        raise HTTPException(422, 'Enter a stop code, stop name, or road name')
    # Treat wildcard characters as literal input; SQL remains parameterized.
    pattern = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    return sql_query('''SELECT stop_code, description, road_name, latitude, longitude
        FROM transport.bus_stops
        WHERE is_active AND (stop_code ILIKE %s OR description ILIKE %s OR road_name ILIKE %s)
        ORDER BY CASE WHEN stop_code = %s THEN 0 ELSE 1 END, description, stop_code
        LIMIT %s''', (pattern,pattern,pattern,query,limit))

@app.get('/arrivals/{stop}')
def arrivals(stop: str):
    """All services at any five-digit stop; shared 20-second live cache.

    no_data does not distinguish an invalid stop from no operating service or
    an empty provider response. Stale fallback is always labeled explicitly.
    """
    try:
        from .domain import stop_code
        stop_code(stop)
        require_mongo()
        return jsonable_encoder(add_feedback_keys(app.state.live.get(stop)))
    except ValueError:
        raise HTTPException(422,'Stop must contain exactly five digits')
    except LiveUnavailable as error:
        raise HTTPException(503,detail={'status':'unavailable','reason':error.reason},
                            headers={'Retry-After':str(error.retry_after)})
    except PyMongoError:
        raise HTTPException(503,'Live cache temporarily unavailable',headers={'Retry-After':'5'})

@app.get('/sampled-arrivals/{stop}')
def sampled_arrivals(stop: str):
    """Original scheduled-sampler snapshot, which may filter SAMPLE_SERVICES."""
    from .domain import stop_code
    try:
        stop_code(stop)
    except ValueError:
        raise HTTPException(422,'Stop must contain exactly five digits')
    now = datetime.now(timezone.utc)
    doc = require_mongo().arrival_latest.find_one({'_id':stop,'expiresAt':{'$gt':now}})
    if not doc:
        return {'status':'unavailable','stopCode':stop,'observations':[]}
    # Recalculate countdown at read time instead of returning old ingestion countdowns.
    for obs in doc['observations']:
        for bus in obs['arrivals']:
            bus['etaSecondsNow'] = (bus['eta']-now).total_seconds() if bus['eta'] else None
    return jsonable_encoder({'status':'available',**doc})

@app.get('/analytics/peak')
def peak(service: str=Query(pattern=r'^[A-Za-z0-9]{1,12}$'),
         stop: str=Query(pattern=r'^[0-9]{5}$'),
         date_from: date | None = None, date_to: date | None = None):
    try:
        start,end = analytics_window(date_from,date_to)
    except ValueError as error:
        raise HTTPException(422,str(error)) from None
    results = list(require_mongo().arrival_observations.aggregate(
        morning_peak(service,start,end,stop),allowDiskUse=True,maxTimeMS=10000))
    return jsonable_encoder({'start':start,'endExclusive':end,'timezone':'Asia/Singapore',
        'metric':'Ordinal crowding 0=SEA, 1=SDA, 2=LSD; not occupancy percent',
        'rows':results})

@app.get('/analytics/trend')
def trend(service: str=Query(pattern=r'^[A-Za-z0-9]{1,12}$'),
          stop: str=Query(pattern=r'^[0-9]{5}$'),
         date_from: date | None = None, date_to: date | None = None):
    try:
        start,end = analytics_window(date_from,date_to)
    except ValueError as error:
        raise HTTPException(422,str(error)) from None
    return jsonable_encoder(list(require_mongo().arrival_observations.aggregate(
        half_hour_trend(service,start,end,stop),allowDiskUse=True,maxTimeMS=10000)))
