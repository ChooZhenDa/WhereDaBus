"""Read-only evidence queries for the two exported databases."""
import json
from datetime import date, datetime, timezone
from backend.analytics import half_hour_trend, morning_peak
from backend.analytics_window import analytics_window
from backend.storage import mongo_database, sql_connection

def capture():
    client, db = mongo_database()
    try:
        samples = db.arrival_observations
        first, last = analytics_window(date(2026,11,22), date(2026,11,22))
        trend = list(samples.aggregate(half_hour_trend('70M', first, last, '66359')))
        target = next(row for row in trend if row['_id']['bucket'].hour == 2 and row['_id']['bucket'].minute == 0)
        first, last = analytics_window(date(2026,11,18), date(2026,11,20))
        peak = list(samples.aggregate(morning_peak('860', first, last, '59059')))
        with sql_connection() as conn:
            inventory = {}
            for table in ('bus_stops','bus_services','bus_routes','import_runs'):
                inventory[table] = conn.execute('SELECT COUNT(*) FROM transport.' + table).fetchone()[0]
            inventory['schemaVersion'] = conn.execute('SELECT version FROM transport.schema_version').fetchone()[0]
            lookup = conn.execute("""SELECT service_no, direction, stop_sequence FROM transport.bus_routes
                WHERE stop_code='66359' AND service_no='70M' ORDER BY direction, stop_sequence""").fetchall()
            journeys = conn.execute("""SELECT service_no, direction, stops_between, distance_km
                FROM transport.direct_routes('59059','59009') ORDER BY service_no""").fetchall()
            inventory['stopLookup'] = [dict(service=row[0],direction=row[1],stopSequence=row[2]) for row in lookup]
            inventory['directRoutes'] = [dict(service=row[0],direction=row[1],stopsBetween=row[2],km=float(row[3])) for row in journeys]
        by_source = list(samples.aggregate([{'$group':{'_id':'$source','observations':{'$sum':1}}},{'$sort':{'_id':1}}]))
        return {'sql':inventory,'mongo':{'physicalDocuments':samples.count_documents({}), 'bySource':by_source,
                'trend70M':{'stop':'66359','singaporeDate':'2026-11-22','bins':len(trend),
                'observations':sum(r['observations'] for r in trend),'eligible':sum(r['validSamples'] for r in trend),
                'tenAM':target},'morning860':{'stop':'59059','firstSGT':'2026-11-18','lastSGT':'2026-11-20','rows':peak}}}
    finally:
        client.close()

if __name__ == '__main__':
    print(json.dumps(capture(), default=lambda v: v.isoformat(), indent=2))
