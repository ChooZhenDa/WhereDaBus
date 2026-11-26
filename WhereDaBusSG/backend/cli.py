import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from .lta import LTAClient
from .stops import sync_stops
from .diagnostics import diagnose_static
from .sampling_options import choose_sample_stops
from .storage import (import_static,initialize_mongo,mongo_database,
                      poll_stop,replay,project_inbox)

def seed_demo(db):
    """Explicitly synthetic examples, never presented as LTA measurements."""
    from .analytics import previous_seven_days
    root = Path(__file__).resolve().parents[1]
    snapshot = json.loads((root/'fixtures/static.json').read_text())
    result = import_static(snapshot=snapshot)
    initialize_mongo(db)
    payload = json.loads((root/'fixtures/arrival.json').read_text())
    start,end = previous_seven_days()
    for day in range(7):
        for index,load in enumerate(('SEA','SDA','LSD')):
            # start is midnight Singapore in UTC; add local hours to that instant.
            captured = start + timedelta(days=day,hours=7,minutes=30*index)
            sample = json.loads(json.dumps(payload))
            sample['Services'][0]['NextBus']['Load'] = load
            sample['Services'][0]['NextBus']['EstimatedArrival'] = (captured+timedelta(minutes=5)).isoformat()
            poll_id = f'SYNTHETIC-{captured.isoformat()}'
            inbox = {'_id':poll_id,'stopCode':'01001','capturedAt':captured,
                     'payload':sample,'selectedServices':[],'projected':False,
                     'source':'SYNTHETIC fixture'}
            db.arrival_inbox.update_one({'_id':poll_id},{'$setOnInsert':inbox},upsert=True)
            stored = db.arrival_inbox.find_one({'_id':poll_id})
            if not stored['projected']:
                project_inbox(db,stored)
    return {**result,'fixture':'SYNTHETIC; not live LTA observations'}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command',choices=['init-mongo','sync-static','sync-stops','diagnose-static','choose-sample-stops','poll','replay','seed-demo'])
    parser.add_argument('--stops',default=os.getenv('SAMPLE_STOPS',''))
    parser.add_argument('--services',default=os.getenv('SAMPLE_SERVICES',''))
    parser.add_argument('--slot',help='Stable schedule identifier for retries; otherwise UTC minute')
    parser.add_argument('--allow-large-change',action='store_true')
    route_policy = parser.add_mutually_exclusive_group()
    route_policy.add_argument('--skip-incomplete-routes',action='store_true',
                        help='For sync-static: exclude entire route directions with sequence gaps; report coverage')
    route_policy.add_argument('--keep-incomplete-routes',action='store_true',
                        help='For sync-static: retain valid route rows and report gaps without inventing missing stops')
    args = parser.parse_args()
    if args.command == 'choose-sample-stops':
        try:
            result = choose_sample_stops()
        except ValueError as error:
            print(json.dumps({'status': 'failed', 'message': str(error)}))
            raise SystemExit(1)
        except Exception:
            # Driver errors may include credentials or private host details.
            print(json.dumps({'status': 'failed', 'message':
                'Automatic stop selection could not read imported stops. Run Refresh-Data.cmd, then try again.'}))
            raise SystemExit(1)
    elif args.command == 'sync-static':
        result = import_static(LTAClient(os.getenv('LTA_ACCOUNT_KEY')),
                               allow_large_change=args.allow_large_change,
                               skip_incomplete_routes=args.skip_incomplete_routes,
                               keep_incomplete_routes=args.keep_incomplete_routes)
    elif args.command == 'sync-stops':
        result = sync_stops(LTAClient(os.getenv('LTA_ACCOUNT_KEY')),
                            allow_large_change=args.allow_large_change)
    elif args.command == 'diagnose-static':
        result = diagnose_static(LTAClient(os.getenv('LTA_ACCOUNT_KEY')))
    else:
        client,db = mongo_database()
        try:
            if args.command == 'init-mongo':
                initialize_mongo(db)
                result = {'initialized':True}
            elif args.command == 'seed-demo':
                result = seed_demo(db)
            elif args.command == 'replay':
                result = {'projected':replay(db)}
            else:
                stops = sorted(set(x.strip() for x in args.stops.split(',') if x.strip()))
                if not stops or len(stops)>20:
                    raise ValueError('Configure 1 to 20 SAMPLE_STOPS')
                services = {x.strip() for x in args.services.split(',') if x.strip()}
                lta = LTAClient(os.getenv('LTA_ACCOUNT_KEY'))
                slot = args.slot or datetime.now(timezone.utc).replace(second=0,microsecond=0).isoformat()
                result = {'projected':0,'failedStops':[]}
                for stop in stops:
                    try:
                        result['projected'] += poll_stop(db,lta,stop,services,slot)
                    except Exception as exc:
                        # Log type only: never leak DSNs, keys or complete API payloads.
                        result['failedStops'].append({'stop':stop,'errorType':type(exc).__name__})
                print(json.dumps(result))
                if result['failedStops']:
                    raise SystemExit(1)
                return
        finally:
            client.close()
    print(json.dumps(result))

if __name__ == '__main__':
    main()
