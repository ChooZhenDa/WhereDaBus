"""Repeatable grading evidence. Run with python -m backend.evidence.

Audit/benchmark only read application data. CRUD uses rolled-back SQL rows and
one labelled synthetic document in a separate Mongo *_evidence database.
No AccountKey, DSN, passwords, user rows or raw provider payloads are emitted.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import os
import platform
import statistics
import sys
from time import perf_counter
from uuid import uuid4

from .analytics import half_hour_trend, morning_peak
from .storage import mongo_database, sql_connection


def timing_summary(samples):
    if not samples or any(not math.isfinite(x) or x < 0 for x in samples):
        raise ValueError('Timings must be finite, non-negative and non-empty')
    ordered = sorted(samples)
    return {'repetitions': len(samples), 'medianMs': statistics.median(samples),
            'p95MsNearestRank': ordered[math.ceil(.95 * len(ordered)) - 1],
            'minMs': min(samples), 'maxMs': max(samples), 'samplesMs': samples}


def measure(operation, repetitions=10, warmups=2):
    for _ in range(warmups):
        operation()
    samples, sizes = [], []
    for _ in range(repetitions):
        start = perf_counter()
        rows = operation()  # Materialize every result before stopping the timer.
        samples.append((perf_counter() - start) * 1000)
        sizes.append(len(rows))
    return {**timing_summary(samples), 'warmupsExcluded': warmups,
            'resultCounts': sizes, 'resultCountStable': len(set(sizes)) == 1}


def selected_window(latest):
    if not latest:
        return None
    timestamp = latest['observedAt']
    if timestamp.tzinfo is None:
        raise ValueError('Observation timestamp must have timezone information')
    now = datetime.now(timezone.utc)
    if timestamp > now + timedelta(minutes=5):
        raise ValueError('Observation is in the future; check the computer clock')
    return {'service': latest['meta']['serviceNo'], 'stop': latest['meta']['stopCode'],
            'start': timestamp - timedelta(hours=48), 'endExclusive': timestamp + timedelta(seconds=1)}


def sql_audit():
    with sql_connection() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        conn.execute("SET LOCAL statement_timeout = '15s'")
        counts = {t: conn.execute('SELECT count(*) FROM transport.' + t).fetchone()[0]
                  for t in ('bus_stops', 'bus_services', 'bus_routes', 'import_runs')}
        return {'version': conn.execute('SHOW server_version').fetchone()[0],
                'counts': counts,
                'schemaVersion': conn.execute('SELECT version FROM transport.schema_version').fetchone()[0],
                'constraints': conn.execute("""SELECT c.conrelid::regclass::text,
                    c.conname, pg_get_constraintdef(c.oid)
                    FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
                    WHERE n.nspname='transport' ORDER BY 1,2""").fetchall(),
                'indexes': conn.execute("SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='transport' ORDER BY 1,2").fetchall(),
                'latestImport': conn.execute('SELECT committed_at,stop_count,service_count,route_count FROM transport.import_runs ORDER BY committed_at DESC LIMIT 1').fetchall(),
                'longestRoutes': conn.execute('SELECT * FROM transport.longest_routes()').fetchall()}


def mongo_audit(db):
    names = ('arrival_observations', 'arrival_latest', 'arrival_inbox', 'arrival_live')
    collections = {}
    for name in names:
        info = next(db.list_collections(filter={'name': name}), None)
        collections[name] = {'exists': info is not None}
        if info:
            collections[name].update(count=db[name].count_documents({}, maxTimeMS=15000),
                                     options=info.get('options', {}),
                                     indexes=list(db[name].list_indexes()))
    latest = next(db.arrival_observations.find({}, {'observedAt': 1, 'meta': 1, 'source': 1})
                  .sort('observedAt', -1).limit(1).max_time_ms(15000), None)
    return {'version': db.client.server_info()['version'], 'collections': collections,
            'latestObservation': latest, 'selection': selected_window(latest),
            'sources': list(db.arrival_observations.aggregate([
                {'$group': {'_id': '$source', 'documents': {'$sum': 1}}}], maxTimeMS=15000)),
            'note': 'Counts are taken sequentially while the application may be writing. Source labels are stored provenance, not independent verification.'}


def sql_crud():
    from psycopg.errors import ForeignKeyViolation, UniqueViolation
    user_id, missing_id = uuid4(), uuid4()
    steps = []
    with sql_connection() as conn:
        try:
            conn.execute("SET LOCAL statement_timeout = '10s'")
            conn.execute("SET LOCAL lock_timeout = '3s'")
            service = conn.execute('SELECT service_no,direction FROM transport.bus_services WHERE is_active ORDER BY service_no,direction LIMIT 1').fetchone()
            if not service:
                return {'status': 'skipped', 'reason': 'Import static data first'}
            conn.execute('INSERT INTO transport.users(user_id,auth_subject,email,display_name) VALUES (%s,%s,%s,%s)',
                         (user_id, 'proof:' + str(user_id), str(user_id) + '@example.invalid', 'SYNTHETIC CRUD PROOF'))
            conn.execute('INSERT INTO transport.favourite_routes(user_id,service_no,direction) VALUES (%s,%s,%s)', (user_id, *service))
            before = conn.execute('SELECT display_name FROM transport.users WHERE user_id=%s', (user_id,)).fetchone()[0]
            steps.append({'operation': 'CREATE and READ', 'value': before, 'favourites': 1})
            conn.execute('UPDATE transport.users SET display_name=%s,updated_at=now() WHERE user_id=%s', ('UPDATED SYNTHETIC PROOF', user_id))
            after = conn.execute('SELECT display_name FROM transport.users WHERE user_id=%s', (user_id,)).fetchone()[0]
            if after != 'UPDATED SYNTHETIC PROOF':
                raise AssertionError('SQL update verification failed')
            steps.append({'operation': 'UPDATE and READ', 'value': after})
            for name, values, error_type in (
                ('duplicate favourite rejected', (user_id, *service), UniqueViolation),
                ('missing user rejected', (missing_id, *service), ForeignKeyViolation)):
                rejected = False
                try:
                    with conn.transaction():  # Nested transaction creates a savepoint.
                        conn.execute('INSERT INTO transport.favourite_routes(user_id,service_no,direction) VALUES (%s,%s,%s)', values)
                except error_type:
                    rejected = True
                if not rejected:
                    raise AssertionError(name + ': constraint did not reject the invalid row')
                steps.append({'operation': name, 'passed': True})
            conn.execute('DELETE FROM transport.favourite_routes WHERE user_id=%s', (user_id,))
            remaining = conn.execute('SELECT count(*) FROM transport.favourite_routes WHERE user_id=%s', (user_id,)).fetchone()[0]
            if remaining != 0:
                raise AssertionError('SQL delete verification failed')
            steps.append({'operation': 'DELETE and READ', 'remainingFavourites': remaining})
            conn.execute('INSERT INTO transport.favourite_routes(user_id,service_no,direction) VALUES (%s,%s,%s)', (user_id, *service))
            conn.execute('DELETE FROM transport.users WHERE user_id=%s', (user_id,))
            remaining = conn.execute('SELECT count(*) FROM transport.favourite_routes WHERE user_id=%s', (user_id,)).fetchone()[0]
            if remaining != 0:
                raise AssertionError('Cascade verification failed')
            steps.append({'operation': 'ON DELETE CASCADE', 'remainingFavourites': remaining})
        finally:
            conn.rollback()  # Never commit synthetic demonstration rows.
        leftover = conn.execute('SELECT count(*) FROM transport.users WHERE user_id=%s', (user_id,)).fetchone()[0]
        if leftover:
            raise AssertionError('Rollback verification failed')
    return {'status': 'passed', 'tables': ['transport.users', 'transport.favourite_routes'],
            'steps': steps, 'cleanup': 'Transaction rolled back; no synthetic rows retained',
            'scope': 'Database demonstration, not a GUI account feature'}


def mongo_crud(db):
    from pymongo.errors import DuplicateKeyError
    proof_db = db.client[db.name + '_evidence']
    collection = proof_db['crud_proofs']
    collection.create_index('expiresAt', expireAfterSeconds=0)
    key = str(uuid4())
    steps = []
    query = {'_id': key, 'source': 'SYNTHETIC CRUD PROOF'}
    try:
        collection.insert_one({**query, 'load': 'SEA', 'loadScore': 0,
                               'expiresAt': datetime.now(timezone.utc) + timedelta(hours=1)})
        before = collection.find_one(query)
        if not before or before['load'] != 'SEA':
            raise AssertionError('Mongo create verification failed')
        steps.append({'operation': 'CREATE and READ', 'load': before['load']})
        changed = collection.update_one(query, {'$set': {'load': 'SDA', 'loadScore': 1}})
        after = collection.find_one(query)
        if changed.modified_count != 1 or after['load'] != 'SDA':
            raise AssertionError('Mongo update verification failed')
        steps.append({'operation': 'UPDATE and READ', 'load': after['load']})
        rejected = False
        try:
            collection.insert_one({**query, 'expiresAt': datetime.now(timezone.utc) + timedelta(hours=1)})
        except DuplicateKeyError:
            rejected = True
        if not rejected:
            raise AssertionError('Duplicate _id was accepted')
        steps.append({'operation': 'duplicate _id rejected', 'passed': True})
        removed = collection.delete_one(query)
        if removed.deleted_count != 1 or collection.find_one(query) is not None:
            raise AssertionError('Mongo delete verification failed')
        steps.append({'operation': 'DELETE and READ', 'remaining': 0})
    finally:
        collection.delete_one(query)  # Only this run's labelled random ID.
    return {'status': 'passed', 'database': proof_db.name, 'collection': collection.name,
            'steps': steps, 'cleanup': 'Only this run\'s synthetic document removed; empty collection/index retained',
            'scope': 'Ordinary demonstration collection; production time-series measurements are not edited'}


def benchmark(db, repetitions):
    from .route_examples import DIRECT_ROUTE_EXAMPLES_SQL
    results = {'method': '2 warmups then sequential driver calls on reused connections; all result rows consumed. No HTTP, UI or LTA network time. Active sampling and caching may affect measurements.',
               'comparisonLimit': 'Different queries/data: these timings do not rank SQL against MongoDB.'}
    with sql_connection() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        conn.execute("SET LOCAL statement_timeout = '15s'")
        examples = conn.execute(DIRECT_ROUTE_EXAMPLES_SQL).fetchall()
        results['sqlLongest'] = measure(lambda: conn.execute('SELECT * FROM transport.longest_routes()').fetchall(), repetitions)
        results['sqlExplain'] = conn.execute('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT * FROM transport.longest_routes()').fetchone()[0]
        results['sqlExplainNote'] = 'Function Scan may hide internal PL/pgSQL plans. Inspect sql/002_queries.sql; this is not proof of an internal index scan.'
        results['directRouteExampleCount'] = len(examples)
    latest = next(db.arrival_observations.find({}, {'observedAt': 1, 'meta': 1}).sort('observedAt', -1).limit(1).max_time_ms(15000), None)
    selection = selected_window(latest)
    if not selection:
        results['mongoTrend'] = {'status': 'skipped', 'reason': 'No sampled history. Start sampling first.'}
        return results
    results['selection'] = selection
    pipeline = half_hour_trend(selection['service'], selection['start'], selection['endExclusive'], selection['stop'])
    operation = lambda: list(db.arrival_observations.aggregate(pipeline, allowDiskUse=True, maxTimeMS=15000))
    results['mongoTrend'] = measure(operation, repetitions)
    results['trendRows'] = operation()
    results['morningRows'] = list(db.arrival_observations.aggregate(
        morning_peak(selection['service'], selection['start'], selection['endExclusive'], selection['stop']),
        allowDiskUse=True, maxTimeMS=15000))
    results['mongoExplain'] = db.command('explain', {
        'aggregate': 'arrival_observations', 'pipeline': pipeline, 'cursor': {},
        'allowDiskUse': True, 'maxTimeMS': 15000}, verbosity='executionStats')
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['audit', 'crud', 'benchmark'])
    parser.add_argument('--repetitions', type=int, default=10)
    args = parser.parse_args(argv)
    if not 5 <= args.repetitions <= 30:
        parser.error('Use between 5 and 30 repetitions')
    report = {'evidenceVersion': 1, 'mode': args.mode, 'startedAtUtc': datetime.now(timezone.utc),
              'python': platform.python_version(), 'platform': platform.platform(), 'results': {}, 'errors': []}
    client = None
    def capture(name, call):
        try:
            report['results'][name] = call()
        except Exception as error:
            # Raw driver messages may contain DSNs or usernames.
            report['errors'].append({'stage': name, 'errorType': type(error).__name__,
                                     'message': 'Stage failed. Check running services, imported data and permissions; no result is claimed.'})
    if args.mode == 'audit':
        capture('sql', sql_audit)
    elif args.mode == 'crud':
        capture('sql', sql_crud)
    try:
        client, db = mongo_database()
        if args.mode == 'audit': capture('mongo', lambda: mongo_audit(db))
        elif args.mode == 'crud': capture('mongo', lambda: mongo_crud(db))
        else: capture('benchmark', lambda: benchmark(db, args.repetitions))
    except Exception as error:
        report['errors'].append({'stage': 'mongoConnection', 'errorType': type(error).__name__})
    finally:
        if client is not None: client.close()
    report['finishedAtUtc'] = datetime.now(timezone.utc)
    def skipped(value):
        if isinstance(value, dict):
            return value.get('status') == 'skipped' or any(skipped(x) for x in value.values())
        if isinstance(value, list):
            return any(skipped(x) for x in value)
        return False
    report['status'] = 'failed' if report['errors'] else 'incomplete' if skipped(report['results']) else 'completed'
    from bson.json_util import dumps
    print(dumps(report, indent=2, default=str))
    return 0 if report['status'] == 'completed' else 1


if __name__ == '__main__':
    sys.exit(main())
