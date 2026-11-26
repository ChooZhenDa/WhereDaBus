"""Verify the isolated review database and restore the dated BSON history."""
import json
from pathlib import Path
from zipfile import ZipFile

from backend.bootstrap import initialize_sql
from backend.sample_transfer import restore_samples
from backend.storage import initialize_mongo, mongo_database, sql_connection
from snapshot_results import capture

DATA = Path('/review-data')
DATABASE = 'wheredabus_submission_review'

def main():
    expected = json.loads((DATA / 'snapshot-results.json').read_text(encoding='utf-8-sig'))
    client, db = mongo_database()
    try:
        with sql_connection() as conn:
            if conn.execute('SELECT current_database()').fetchone()[0] != DATABASE or db.name != DATABASE:
                raise RuntimeError('Review requires the dedicated submission database.')
            conn.execute('SELECT pg_advisory_xact_lock(829615042)')
            if not conn.execute("SELECT to_regclass('transport.bus_stops')").fetchone()[0]:
                raise RuntimeError('The SQL snapshot did not load during first-time database setup.')
            initialize_sql()
            if 'arrival_observations' not in db.list_collection_names():
                db.create_collection('arrival_observations', timeseries={
                    'timeField':'observedAt', 'metaField':'meta', 'granularity':'minutes'})
            if 'expireAfterSeconds' in db.arrival_observations.options():
                raise RuntimeError('Use a dedicated review volume to preserve the historical timestamps.')
            initialize_mongo(db)
            with ZipFile(DATA / 'samples.zip') as backup:
                result = restore_samples(db.arrival_observations, backup)
            actual = json.loads(json.dumps(capture(), default=lambda v: v.isoformat()))
            if actual != expected:
                raise RuntimeError('Restored query results differ from data/snapshot-results.json.')
            print(json.dumps({'status':'verified','transfer':result,'sql':actual['sql'],
                              'physicalObservations':actual['mongo']['physicalDocuments']}), flush=True)
    finally:
        client.close()

if __name__ == '__main__':
    main()
