"""Seed an unpopulated route catalogue from the submission's text-format pg_dump."""
import argparse
import json
import sys
from uuid import uuid4

from .storage import sql_connection


# Only these exact COPY headers identify input. All executable dump text is ignored.
COLUMNS = {
    'bus_stops': 'stop_code, road_name, description, latitude, longitude, is_active, updated_at',
    'bus_services': 'service_no, direction, operator, category, origin_code, destination_code, loop_description, is_active, updated_at',
    'bus_routes': 'service_no, direction, stop_sequence, stop_code, distance_km',
}


def read_network(stream):
    headers = {f'COPY transport.{table} ({columns}) FROM stdin;'.encode(): table
               for table, columns in COLUMNS.items()}
    blocks = {}
    for line in stream:
        table = headers.get(line.rstrip(b'\r\n'))
        if table is None:
            continue
        if table in blocks:
            raise ValueError(f'Duplicate packaged COPY section for {table}.')
        rows = bytearray()
        count = 0
        for line in stream:
            if line.rstrip(b'\r\n') == b'\\.':
                break
            rows.extend(line)
            count += 1
        else:
            raise ValueError(f'Incomplete packaged COPY section for {table}.')
        if not count:
            raise ValueError(f'Empty packaged COPY section for {table}.')
        blocks[table] = (bytes(rows), count)
    if blocks.keys() != COLUMNS.keys():
        raise ValueError('Packaged SQL is missing required network COPY sections.')
    return blocks


def seed_network(stream):
    with sql_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(51003)')
        cur.execute('''SELECT EXISTS (SELECT 1 FROM transport.bus_services)
                           OR EXISTS (SELECT 1 FROM transport.bus_routes)''')
        if cur.fetchone()[0]:
            return {'status': 'kept-existing-network'}

        blocks = read_network(stream)
        cur.execute('CREATE TEMP TABLE submission_stops (LIKE transport.bus_stops) ON COMMIT DROP')
        for table, columns in COLUMNS.items():
            destination = 'pg_temp.submission_stops' if table == 'bus_stops' else f'transport.{table}'
            with cur.copy(f'COPY {destination} ({columns}) FROM STDIN') as copy:
                copy.write(blocks[table][0])
            if table == 'bus_stops':
                cur.execute(f'''INSERT INTO transport.bus_stops ({columns})
                                SELECT {columns} FROM pg_temp.submission_stops
                                ON CONFLICT (stop_code) DO NOTHING''')

        stops, services, routes = (blocks[table][1] for table in COLUMNS)
        # This import event supplies both the data-status timestamp and the next
        # live import's route-count baseline. Earlier import history is preserved.
        cur.execute('''INSERT INTO transport.import_runs
                       (run_id, fetched_at, stop_count, service_count, route_count)
                       VALUES (%s, now(), %s, %s, %s)''', (uuid4(), stops, services, routes))
        return {'status': 'seeded-packaged-network', 'snapshotStops': stops,
                'serviceDirections': services, 'routeOccurrences': routes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dump', nargs='?', type=argparse.FileType('rb'), default=sys.stdin.buffer)
    args = parser.parse_args()
    try:
        print(json.dumps(seed_network(args.dump)))
    except (OSError, ValueError) as error:
        print(f'Could not read the packaged route network: {error}', file=sys.stderr)
        return 1
    except Exception as error:
        print(f'Packaged route setup failed ({type(error).__name__}). Check database readiness and the submission SQL file.',
              file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
