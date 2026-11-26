import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from backend import bootstrap


class Connection:
    def __init__(self, schema=None, marker=None, versions=None, fail_text=None):
        self.schema, self.marker = schema, marker
        self.versions = versions if versions is not None else [(3,)]
        self.fail_text = fail_text
        self.calls = []
        self.committed = self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        self.committed = kind is None
        self.rolled_back = kind is not None

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if sql == self.fail_text:
            raise ValueError('Invalid schema script')
        cursor = Mock()
        if 'to_regnamespace' in sql:
            cursor.fetchone.return_value = (self.schema,)
        elif 'to_regclass' in sql:
            cursor.fetchone.return_value = (self.marker,)
        elif 'SELECT version' in sql:
            cursor.fetchall.return_value = self.versions
        return cursor


class BootstrapTests(unittest.TestCase):
    def test_fresh_schema_and_marker_commit_together(self):
        conn = Connection()
        with patch.object(Path, 'read_text', side_effect=['SCHEMA SCRIPT', 'QUERIES SCRIPT', 'SAVED JOURNEYS']):
            result = bootstrap.initialize_sql(lambda: conn)
        statements = [sql for sql, params in conn.calls]
        self.assertEqual(result, 'created')
        self.assertTrue(conn.committed)
        self.assertIn('SCHEMA SCRIPT', statements)
        self.assertIn('QUERIES SCRIPT', statements)
        self.assertLess(statements.index('QUERIES SCRIPT'), len(statements) - 1)
        self.assertIn('SAVED JOURNEYS', statements)
        self.assertEqual(conn.calls[-1][1], (3,))

    def test_repeat_setup_verifies_marker_without_running_scripts(self):
        conn = Connection(schema='transport', marker='transport.schema_version')
        with patch.object(Path, 'read_text') as read:
            self.assertEqual(bootstrap.initialize_sql(lambda: conn), 'ready')
        read.assert_not_called()
        self.assertTrue(conn.committed)
        self.assertFalse(any(sql.startswith(('CREATE ', 'INSERT ', 'DELETE ', 'DROP ')) for sql, _ in conn.calls))

    def test_v1_upgrade_replaces_queries_and_marker_in_same_transaction(self):
        conn = Connection(schema='transport', marker='transport.schema_version', versions=[(1,)])
        with patch.object(Path, 'read_text', side_effect=['PREPARE UPGRADE', 'QUERIES V2', 'SAVED JOURNEYS']):
            self.assertEqual(bootstrap.initialize_sql(lambda: conn), 'upgraded')
        statements = [sql for sql, _ in conn.calls]
        self.assertIn('SELECT pg_advisory_xact_lock(51003)', statements)
        self.assertLess(statements.index('PREPARE UPGRADE'), statements.index('QUERIES V2'))
        self.assertTrue(conn.committed)
        self.assertTrue(conn.calls[-1][0].startswith('UPDATE transport.schema_version'))
        self.assertIn('SAVED JOURNEYS', statements)
        self.assertEqual(conn.calls[-1][1], (3, 1))
        self.assertFalse(any('DELETE FROM transport.bus_' in sql for sql in statements))

    def test_v2_upgrade_only_adds_journeys_before_advancing_marker(self):
        conn = Connection(schema='transport', marker='transport.schema_version', versions=[(2,)])
        with patch.object(Path, 'read_text', return_value='SAVED JOURNEYS') as read:
            self.assertEqual(bootstrap.initialize_sql(lambda: conn), 'upgraded')
        self.assertEqual(read.call_count, 1)
        self.assertEqual(conn.calls[-2], ('SAVED JOURNEYS', None))
        self.assertEqual(conn.calls[-1][1], (3, 2))
        self.assertTrue(conn.committed)

    def test_failed_upgrade_rolls_back_without_changing_marker(self):
        conn = Connection(schema='transport', marker='transport.schema_version', versions=[(1,)], fail_text='BROKEN V2')
        with patch.object(Path, 'read_text', side_effect=['PREPARE UPGRADE', 'BROKEN V2']):
            with self.assertRaises(ValueError):
                bootstrap.initialize_sql(lambda: conn)
        self.assertTrue(conn.rolled_back)
        self.assertFalse(any(sql.startswith('UPDATE transport.schema_version') for sql, _ in conn.calls))

    def test_multiple_or_empty_version_markers_are_refused(self):
        for versions in ([], [(1,), (2,)]):
            conn = Connection(schema='transport', marker='transport.schema_version', versions=versions)
            with self.assertRaises(bootstrap.SchemaMismatch):
                bootstrap.initialize_sql(lambda: conn)
            self.assertTrue(conn.rolled_back)

    def test_unknown_existing_schema_is_not_adopted(self):
        conn = Connection(schema='transport')
        with self.assertRaises(bootstrap.SchemaMismatch):
            bootstrap.initialize_sql(lambda: conn)
        self.assertTrue(conn.rolled_back)
        self.assertFalse(any(sql.startswith('CREATE ') for sql, _ in conn.calls))

    def test_incompatible_version_is_refused(self):
        conn = Connection(schema='transport', marker='transport.schema_version', versions=[(4,)])
        with self.assertRaises(bootstrap.SchemaMismatch):
            bootstrap.initialize_sql(lambda: conn)
        self.assertTrue(conn.rolled_back)

    def test_failed_script_does_not_record_success(self):
        conn = Connection(fail_text='BROKEN SCRIPT')
        with patch.object(Path, 'read_text', side_effect=['SCHEMA SCRIPT', 'BROKEN SCRIPT']):
            with self.assertRaises(ValueError):
                bootstrap.initialize_sql(lambda: conn)
        self.assertTrue(conn.rolled_back)
        self.assertFalse(any('CREATE TABLE transport.schema_version' in sql for sql, _ in conn.calls))

    def test_mongo_client_closes_on_success_and_failure(self):
        for error in (None, ValueError('incompatible collection')):
            with self.subTest(error=error):
                client, database = Mock(), Mock()
                initializer = Mock(side_effect=error)
                if error:
                    with self.assertRaises(ValueError):
                        bootstrap.initialize_document_store(lambda: (client, database), initializer)
                else:
                    self.assertEqual(bootstrap.initialize_document_store(lambda: (client, database), initializer), 'ready')
                client.close.assert_called_once()
                database.command.assert_called_once_with('ping')

    def test_transient_failure_is_retried_with_a_bound(self):
        operation = Mock(side_effect=[ConnectionError(), ConnectionError(), 'ready'])
        sleep = Mock()
        self.assertEqual(bootstrap.retry(operation, attempts=3, delay=2, sleep=sleep), 'ready')
        self.assertEqual(operation.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        failure = Mock(side_effect=ConnectionError())
        with self.assertRaises(ConnectionError):
            bootstrap.retry(failure, attempts=2, sleep=Mock())
        self.assertEqual(failure.call_count, 2)

    def test_schema_errors_are_not_retried(self):
        operation = Mock(side_effect=bootstrap.SchemaMismatch('wrong schema'))
        with self.assertRaises(bootstrap.SchemaMismatch):
            bootstrap.retry(operation, sleep=Mock())
        operation.assert_called_once()

    def test_sql_finishes_before_mongo_and_failure_skips_mongo(self):
        calls = []
        result = bootstrap.bootstrap(lambda: calls.append('sql') or 'ready', lambda: calls.append('mongo') or 'ready')
        self.assertEqual(calls, ['sql', 'mongo'])
        self.assertEqual(result['schemaVersion'], 3)
        mongo = Mock()
        with self.assertRaises(bootstrap.SchemaMismatch):
            bootstrap.bootstrap(Mock(side_effect=bootstrap.SchemaMismatch()), mongo)
        mongo.assert_not_called()

    def test_cli_missing_configuration_is_helpful(self):
        output = io.StringIO()
        with patch.dict('os.environ', {}, clear=True), contextlib.redirect_stdout(output):
            self.assertEqual(bootstrap.main(), 1)
        self.assertIn('SQL_DSN', json.loads(output.getvalue())['message'])

    def test_cli_hides_driver_exception_secrets(self):
        output = io.StringIO()
        with patch.dict('os.environ', {'SQL_DSN': 'secret-a', 'MONGO_URI': 'secret-b'}, clear=True):
            with patch.object(bootstrap, 'bootstrap', side_effect=RuntimeError('password=secret-c')):
                with contextlib.redirect_stdout(output):
                    self.assertEqual(bootstrap.main(), 1)
        self.assertNotIn('secret', output.getvalue())
        self.assertEqual(json.loads(output.getvalue())['errorType'], 'RuntimeError')


if __name__ == '__main__':
    unittest.main()
