"""Driver imports are lazy so domain/unit tests need no database installation."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import logging
import os
from uuid import uuid4
from .domain import (normalize_arrivals, validate_static, select_complete_routes,
                     retain_available_routes, stop_code)

def sql_connection():
    import psycopg
    return psycopg.connect(os.environ['SQL_DSN'],connect_timeout=10)

def mongo_database():
    from pymongo import MongoClient
    client = MongoClient(os.environ['MONGO_URI'],tz_aware=True,
                         serverSelectionTimeoutMS=10000,w='majority')
    return client, client[os.getenv('MONGO_DB','wheredabus')]

def import_static(client=None, snapshot=None, allow_large_change=False,
                  skip_incomplete_routes=False, keep_incomplete_routes=False):
    if skip_incomplete_routes and keep_incomplete_routes:
        raise ValueError('Choose only one incomplete-route import policy')
    fetched = datetime.now(timezone.utc)
    if snapshot is None:
        # Fetch complete datasets before acquiring a database transaction/lock.
        snapshot = {k:client.all_rows(k) for k in ('BusStops','BusServices','BusRoutes')}
    stops, services, routes = (snapshot[k] for k in ('BusStops','BusServices','BusRoutes'))
    excluded_route_directions = []
    partial_route_directions = []
    if keep_incomplete_routes:
        routes, missing_route_directions, partial_route_directions = retain_available_routes(stops,services,routes)
    elif skip_incomplete_routes:
        routes, missing_route_directions, excluded_route_directions = select_complete_routes(stops,services,routes)
    else:
        missing_route_directions = validate_static(stops,services,routes)
    with sql_connection() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(51003)')
            cur.execute('SELECT route_count FROM transport.import_runs ORDER BY committed_at DESC LIMIT 1')
            previous = cur.fetchone()
            if previous and len(routes) < previous[0]*0.8 and not allow_large_change:
                raise ValueError('Route count dropped >20%; inspect feed and explicitly allow if expected')
            cur.executemany('''INSERT INTO transport.bus_stops
              (stop_code,road_name,description,latitude,longitude)
              VALUES (%s,%s,%s,%s,%s) ON CONFLICT(stop_code) DO UPDATE SET
              road_name=EXCLUDED.road_name,description=EXCLUDED.description,
              latitude=EXCLUDED.latitude,longitude=EXCLUDED.longitude,
              is_active=true,updated_at=now()''',
              [(s['BusStopCode'],s['RoadName'],s['Description'],s['Latitude'],s['Longitude']) for s in stops])
            cur.executemany('''INSERT INTO transport.bus_services
              (service_no,direction,operator,category,origin_code,destination_code,loop_description)
              VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(service_no,direction) DO UPDATE SET
              operator=EXCLUDED.operator,category=EXCLUDED.category,
              origin_code=EXCLUDED.origin_code,destination_code=EXCLUDED.destination_code,
              loop_description=EXCLUDED.loop_description,is_active=true,updated_at=now()''',
              [(s['ServiceNo'],int(s['Direction']),s['Operator'],s.get('Category',''),
                s.get('OriginCode') or None,s.get('DestinationCode') or None,
                s.get('LoopDesc') or '') for s in services])
            cur.execute('CREATE TEMP TABLE current_services(service_no text,direction smallint) ON COMMIT DROP')
            cur.executemany('INSERT INTO current_services VALUES (%s,%s)',
                            [(s['ServiceNo'],int(s['Direction'])) for s in services])
            cur.execute('''UPDATE transport.bus_services s SET is_active=false,updated_at=now()
              WHERE s.is_active AND NOT EXISTS(SELECT 1 FROM current_services c
              WHERE c.service_no=s.service_no AND c.direction=s.direction)''')
            # DELETE rather than TRUNCATE preserves MVCC visibility for concurrent readers.
            cur.execute('DELETE FROM transport.bus_routes')
            cur.executemany('INSERT INTO transport.bus_routes VALUES (%s,%s,%s,%s,%s)',
              [(r['ServiceNo'],int(r['Direction']),int(r['StopSequence']),r['BusStopCode'],
                Decimal(str(r['Distance']))) for r in routes])
            cur.execute('''UPDATE transport.bus_stops SET is_active=false,updated_at=now()
              WHERE is_active AND NOT (stop_code=ANY(%s))''',([s['BusStopCode'] for s in stops],))
            cur.execute('INSERT INTO transport.import_runs(run_id,fetched_at,stop_count,service_count,route_count) VALUES (%s,%s,%s,%s,%s)',
                        (uuid4(),fetched,len(stops),len(services),len(routes)))
    warnings = []
    if partial_route_directions:
        message = (f'Retained {len(partial_route_directions)} route direction(s) with detected sequence gaps. '
                   'Known route occurrences remain searchable; journeys crossing gaps have partial route details. '
                   'No route rows were invented or resequenced. See partialRouteDirections in the result.')
        warnings.append(message)
        logging.getLogger(__name__).warning(message)
    if excluded_route_directions:
        message = (f'Excluded {len(excluded_route_directions)} incomplete route direction(s); '
                   'only complete directions were imported. No route rows were invented or resequenced. '
                   'See excludedRouteDirections in the result; refresh later to retry the provider feed.')
        warnings.append(message)
        logging.getLogger(__name__).warning(message)
    if missing_route_directions:
        message = (f'Imported static data; {len(missing_route_directions)} service '
                   'direction(s) have no route rows. Their service metadata is retained, '
                   'but route searches cannot use those directions. '
                   'See missingRouteDirections in the result.')
        warnings.append(message)
        logging.getLogger(__name__).warning(message)
    return {'mode': 'keep-incomplete-routes' if keep_incomplete_routes else
                    'complete-directions-only' if skip_incomplete_routes else 'strict',
            'stops':len(stops),'serviceDirections':len(services),
            'routeOccurrences':len(routes),
            'partialRouteDirections':partial_route_directions,
            'excludedRouteDirections':excluded_route_directions,
            'missingRouteDirections':missing_route_directions,'warnings':warnings}

def initialize_mongo(db):
    names = db.list_collection_names()
    if 'arrival_observations' not in names:
        db.create_collection('arrival_observations',timeseries={
            'timeField':'observedAt','metaField':'meta','granularity':'minutes'},
            expireAfterSeconds=90*86400)
    else:
        info = next(db.list_collections(filter={'name':'arrival_observations'}))
        ts = info.get('options',{}).get('timeseries',{})
        if ts.get('timeField') != 'observedAt' or ts.get('metaField') != 'meta':
            raise ValueError('Existing collection has incompatible time-series configuration')
    db.arrival_observations.create_index([('meta.serviceNo',1),('meta.stopCode',1),('observedAt',1)],name='service_stop_time')
    db.arrival_observations.create_index([('meta.stopCode',1),('observedAt',-1)],name='stop_time')
    db.arrival_inbox.create_index('expiresAt',expireAfterSeconds=0)
    db.arrival_inbox.create_index([('projected',1),('capturedAt',1)])
    db.arrival_latest.create_index('expiresAt',expireAfterSeconds=0)
    db.arrival_live.create_index('expiresAt',expireAfterSeconds=0)
    db.arrival_feedback.create_index('expiresAt', expireAfterSeconds=0)
    db.arrival_feedback.create_index([('stop', 1), ('expiresAt', 1)])

def project_inbox(db, inbox):
    """At least once projection. A crash after insert may duplicate measurements.

    Every analytics pipeline deduplicates logicalKey. Pending inbox documents
    have NO TTL until successful projection, so outages cannot silently expire them.
    """
    docs = normalize_arrivals(inbox['payload'],inbox['capturedAt'],inbox['_id'],inbox['selectedServices'])
    if inbox.get('source') == 'SYNTHETIC fixture':
        for doc in docs:
            doc['source'] = 'SYNTHETIC fixture'
    if docs:
        db.arrival_observations.insert_many(docs,ordered=False)
    # Monotonic atomic update prevents an older replay replacing the current cache.
    captured = inbox['capturedAt']
    cache = {'capturedAt':captured,'expiresAt':captured+timedelta(seconds=120),
             # PyMongo adds ObjectIds to inserted dictionaries; keep HTTP cache JSON-safe.
             'observations':[{k:v for k,v in doc.items() if k != '_id'} for doc in docs]}
    db.arrival_latest.update_one({'_id':inbox['stopCode']},[
        {'$replaceWith':{'$cond':[
            {'$gte':[{'$literal':captured},{'$ifNull':['$capturedAt',datetime(1970,1,1,tzinfo=timezone.utc)]}]},
            {'$literal':{'_id':inbox['stopCode'],**cache}}, '$$ROOT']}}
    ],upsert=True)
    db.arrival_inbox.update_one({'_id':inbox['_id']},{'$set':{
        'projected':True,'projectedAt':datetime.now(timezone.utc),
        'expiresAt':datetime.now(timezone.utc)+timedelta(days=2)}})
    return len(docs)

def poll_stop(db, client, code, selected_services=None, slot=None):
    from pymongo import ReturnDocument
    from pymongo.errors import DuplicateKeyError
    code = stop_code(code)
    slot = slot or datetime.now(timezone.utc).replace(second=0,microsecond=0).isoformat()
    # One poll/stop/minute for the centrally configured sampling cohort.
    poll_id = hashlib.sha256(f'{code}|{slot}'.encode()).hexdigest()
    inbox = db.arrival_inbox.find_one({'_id':poll_id})
    if inbox is None:
        payload = client.get('v3/BusArrival',{'BusStopCode':code})
        if not payload:
            payload = {'BusStopCode':code,'Services':[]}
        if payload.get('BusStopCode') != code:
            raise ValueError('API stop does not match requested stop')
        captured = datetime.now(timezone.utc)
        # Validate before the durable write. Unknown load codes are represented as null.
        normalize_arrivals(payload,captured,poll_id,selected_services)
        candidate = {'_id':poll_id,'stopCode':code,'slot':slot,'capturedAt':captured,
                     'payload':payload,'selectedServices':sorted(selected_services or []),
                     'projected':False}
        try:
            inbox = db.arrival_inbox.find_one_and_update({'_id':poll_id},
                {'$setOnInsert':candidate},upsert=True,return_document=ReturnDocument.AFTER)
        except DuplicateKeyError:
            inbox = db.arrival_inbox.find_one({'_id':poll_id})
    return 0 if inbox['projected'] else project_inbox(db,inbox)

def replay(db, limit=100):
    count = 0
    for inbox in db.arrival_inbox.find({'projected':False}).sort('capturedAt',1).limit(limit):
        count += project_inbox(db,inbox)
    return count
