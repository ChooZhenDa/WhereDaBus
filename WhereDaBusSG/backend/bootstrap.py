"""Initialize this fresh installation without adopting an unrelated database.

Run once before the API: ``python -m backend.bootstrap``. PostgreSQL schema
creation and recognized upgrades are transactional. Repeat runs verify the
version and Mongo indexes; they never import sample data or delete route rows.
"""
from pathlib import Path
import json
import os
import time

from .storage import initialize_mongo, mongo_database, sql_connection


SCHEMA_VERSION = 3
SQL_ROOT = Path(__file__).resolve().parents[1] / 'sql'


class SchemaMismatch(RuntimeError):
    """An existing schema cannot be safely treated as this installation."""


def initialize_sql(connect=None, sql_root=None):
    connect = connect or sql_connection
    sql_root = Path(sql_root or SQL_ROOT)
    with connect() as connection:
        connection.execute("SET LOCAL lock_timeout = '10s'")
        connection.execute("SET LOCAL statement_timeout = '120s'")
        connection.execute('SELECT pg_advisory_xact_lock(51004)')
        # Serialize schema upgrades with the importer and stop-only refresh.
        connection.execute('SELECT pg_advisory_xact_lock(51003)')
        exists = connection.execute("SELECT to_regnamespace('transport')").fetchone()[0]
        if exists is not None:
            marker = connection.execute("SELECT to_regclass('transport.schema_version')").fetchone()[0]
            if marker is None:
                raise SchemaMismatch('Existing transport schema has no fresh-setup version marker. Use a new database volume.')
            versions = connection.execute('SELECT version FROM transport.schema_version ORDER BY version').fetchall()
            if versions == [(SCHEMA_VERSION,)]:
                return 'ready'
            if versions not in ([(1,)], [(2,)]):
                raise SchemaMismatch('Existing transport schema version does not match this setup. Use its matching application release.')
            previous = versions[0][0]
            if previous == 1:
                for filename in ('003_prepare_coverage_migration.sql', '002_queries.sql'):
                    connection.execute((sql_root / filename).read_text(encoding='utf-8'))
            connection.execute((sql_root / '004_saved_journeys.sql').read_text(encoding='utf-8'))
            connection.execute('UPDATE transport.schema_version SET version=%s, applied_at=now() WHERE version=%s',
                               (SCHEMA_VERSION, previous))
            return 'upgraded'
        for filename in ('001_schema.sql', '002_queries.sql', '004_saved_journeys.sql'):
            connection.execute((sql_root / filename).read_text(encoding='utf-8'))
        connection.execute('''CREATE TABLE transport.schema_version (
            version integer PRIMARY KEY,
            applied_at timestamptz NOT NULL DEFAULT now()
        )''')
        connection.execute('INSERT INTO transport.schema_version(version) VALUES (%s)', (SCHEMA_VERSION,))
    return 'created'


def initialize_document_store(factory=None, initializer=None):
    factory = factory or mongo_database
    initializer = initializer or initialize_mongo
    client, database = factory()
    try:
        database.command('ping')
        initializer(database)
    finally:
        client.close()
    return 'ready'


def transient(error):
    if isinstance(error, (ConnectionError, TimeoutError)):
        return True
    # Keep offline tests independent of installed database drivers.
    try:
        from psycopg import OperationalError
        if isinstance(error, OperationalError):
            return True
    except ImportError:
        pass
    try:
        from pymongo.errors import ConnectionFailure
        return isinstance(error, ConnectionFailure)
    except ImportError:
        return False


def retry(operation, attempts=6, delay=2, sleep=None):
    """Bounded readiness retries; validation/schema errors fail immediately."""
    sleep = sleep or time.sleep
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as error:
            if not transient(error) or attempt + 1 == attempts:
                raise
            sleep(delay)


def bootstrap(sql_initializer=None, mongo_initializer=None):
    sql_result = retry(sql_initializer or initialize_sql)
    mongo_result = retry(mongo_initializer or initialize_document_store)
    return {'sql': sql_result, 'mongo': mongo_result, 'schemaVersion': SCHEMA_VERSION}


def main():
    missing = [name for name in ('SQL_DSN', 'MONGO_URI') if not os.getenv(name, '').strip()]
    if missing:
        print(json.dumps({'status': 'failed', 'message': 'Configure ' + ', '.join(missing) + ' before setup.'}), flush=True)
        return 1
    try:
        result = bootstrap()
    except SchemaMismatch as error:
        # These messages are defined here and contain no server-provided values.
        print(json.dumps({'status': 'failed', 'message': str(error)}), flush=True)
        return 1
    except Exception as error:
        # Driver exceptions can embed DSNs or keys. Only emit a safe type name.
        print(json.dumps({'status': 'failed', 'errorType': type(error).__name__,
                          'message': 'Database setup failed. Check database readiness, credentials and collection compatibility.'}), flush=True)
        return 1
    print(json.dumps({'status': 'ready', **result}), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
