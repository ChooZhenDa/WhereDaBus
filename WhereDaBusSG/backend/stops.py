"""Import stop locations independently of the route and service datasets."""
import math
from .domain import stop_code
from .storage import sql_connection

def validated_stop_rows(records):
    """Validate the entire stop feed before any SQL writes, without mutating it."""
    if not isinstance(records,list) or not records:
        raise ValueError('Refusing an empty or malformed bus-stop snapshot')
    rows, seen = [], set()
    for record in records:
        if not isinstance(record,dict):
            raise ValueError('Invalid bus-stop record')
        code = stop_code(record.get('BusStopCode'))
        if code in seen:
            raise ValueError(f'Duplicate bus stop: {code}')
        road, description = record.get('RoadName'), record.get('Description')
        if not isinstance(road,str):
            raise ValueError(f'Missing road name for bus stop {code}')
        if not isinstance(description,str) or not description.strip():
            raise ValueError(f'Missing description for bus stop {code}')
        try:
            if isinstance(record.get('Latitude'),bool) or isinstance(record.get('Longitude'),bool):
                raise ValueError()
            lat,lon = float(record['Latitude']),float(record['Longitude'])
        except (KeyError,TypeError,ValueError):
            raise ValueError(f'Invalid coordinates for bus stop {code}') from None
        if not (math.isfinite(lat) and math.isfinite(lon) and
                -90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError(f'Invalid coordinates for bus stop {code}')
        rows.append((code,road,description,lat,lon))
        seen.add(code)
    return rows

def sync_stops(client=None, records=None, allow_large_change=False):
    """Atomically upsert stops and retire absent stops, retaining referenced rows.

    No BusRoutes/BusServices requests or writes are made. The shared advisory
    lock serializes this operation with the full static importer. The full-import
    audit table is not used because this operation does not fetch a full snapshot.
    """
    if records is None:
        if client is None:
            raise ValueError('Supply an LTA client or a bus-stop snapshot')
        records = client.all_rows('BusStops')
    rows = validated_stop_rows(records)
    with sql_connection() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(51003)')
            cur.execute('SELECT count(*) FROM transport.bus_stops WHERE is_active')
            previous_count = cur.fetchone()[0]
            if previous_count and len(rows) < previous_count*0.8 and not allow_large_change:
                raise ValueError('Bus-stop count dropped >20%; inspect feed before allowing this change')
            cur.executemany('''INSERT INTO transport.bus_stops
              (stop_code,road_name,description,latitude,longitude)
              VALUES (%s,%s,%s,%s,%s) ON CONFLICT(stop_code) DO UPDATE SET
              road_name=EXCLUDED.road_name,description=EXCLUDED.description,
              latitude=EXCLUDED.latitude,longitude=EXCLUDED.longitude,
              is_active=true,updated_at=now()''',rows)
            cur.execute('''UPDATE transport.bus_stops SET is_active=false,updated_at=now()
              WHERE is_active AND NOT (stop_code=ANY(%s))''',([r[0] for r in rows],))
            retired = cur.rowcount
    return {'mode':'stops-only','stops':len(rows),'deactivatedStops':retired,
            'routesUpdated':False,
            'note':'Stop locations refreshed. Service lists still depend on the last successful route import.'}
