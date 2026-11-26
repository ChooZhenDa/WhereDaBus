"""Safe automatic cohorts and readable choices, without production data."""
import io
import json
import os
from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch

from fastapi.testclient import TestClient
from psycopg import OperationalError
from pymongo.errors import ServerSelectionTimeoutError

from backend import api, cli, sampling_options as selections


class AutomaticSelectionTests(TestCase):
    def test_choose_returns_only_database_rows_and_passes_optional_filter(self):
        rows = [{'stopCode': '01001', 'description': 'Actual imported name',
                 'roadName': 'Actual imported road', 'services': ['15']}]
        connection = MagicMock()
        cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = rows
        with patch.dict(os.environ, {'SAMPLE_SERVICES': '15,15'}, clear=True), \
                patch.object(selections, 'sql_connection', return_value=connection):
            self.assertEqual(selections.choose_sample_stops(), {'stops': rows})
        cursor.execute.assert_called_once_with(selections.CHOOSE_SAMPLE_STOPS_SQL, (['15'], ['15']))

    def test_empty_import_fails_instead_of_inventing_codes(self):
        connection = MagicMock()
        connection.__enter__.return_value.cursor.return_value.__enter__.return_value.fetchall.return_value = []
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(selections, 'sql_connection', return_value=connection):
            with self.assertRaisesRegex(ValueError, 'Refresh-Data.cmd'):
                selections.choose_sample_stops()

    def test_unmatched_service_filter_has_specific_guidance(self):
        connection = MagicMock()
        connection.__enter__.return_value.cursor.return_value.__enter__.return_value.fetchall.return_value = []
        with patch.dict(os.environ, {'SAMPLE_SERVICES': '15'}, clear=True), \
                patch.object(selections, 'sql_connection', return_value=connection):
            with self.assertRaisesRegex(ValueError, 'service filter'):
                selections.choose_sample_stops()

    def test_invalid_filter_fails_before_database_access(self):
        with patch.dict(os.environ, {'SAMPLE_SERVICES': 'secret invalid value'}, clear=True), \
                patch.object(selections, 'sql_connection') as connect:
            with self.assertRaisesRegex(ValueError, 'invalid service number'):
                selections.choose_sample_stops()
        connect.assert_not_called()

    def test_cli_success_is_machine_readable_without_mongo_or_provider(self):
        output = io.StringIO()
        with patch('sys.argv', ['cli', 'choose-sample-stops']), \
                patch.object(cli, 'choose_sample_stops', return_value={'stops': [{'stopCode': '01001'}]}), \
                patch.object(cli, 'mongo_database', side_effect=AssertionError('unexpected Mongo')), \
                patch.object(cli, 'LTAClient', side_effect=AssertionError('unexpected LTA')), \
                patch('sys.stdout', output):
            cli.main()
        self.assertEqual(json.loads(output.getvalue()), {'stops': [{'stopCode': '01001'}]})

    def test_cli_database_failure_does_not_leak_credentials(self):
        output = io.StringIO()
        with patch('sys.argv', ['cli', 'choose-sample-stops']), \
                patch.object(cli, 'choose_sample_stops', side_effect=OperationalError('password=SECRET')), \
                patch('sys.stdout', output):
            with self.assertRaises(SystemExit) as error:
                cli.main()
        self.assertEqual(error.exception.code, 1)
        self.assertNotIn('SECRET', output.getvalue())
        self.assertEqual(json.loads(output.getvalue())['status'], 'failed')


class SamplingOptionsHTTPTests(TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.context = TestClient(api.app)
        self.client = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def database(self, snapshots):
        database = Mock()
        database.arrival_latest.find.return_value.limit.return_value.max_time_ms.return_value = snapshots
        return database

    def test_blank_config_is_read_only_and_returns_no_fake_defaults(self):
        with patch.object(api, 'sql_query', side_effect=AssertionError('unexpected SQL')), \
                patch.object(api, 'require_mongo', side_effect=AssertionError('unexpected Mongo')):
            result = self.client.get('/sampling/options')
        self.assertEqual(result.json(), {'configured': False, 'stops': [], 'services': [], 'source': 'configured'})

    def test_names_and_filtered_services_include_fresh_snapshot_only_service(self):
        database = self.database([{'_id': '01001', 'observations': [
            {'meta': {'serviceNo': '100'}}, {'meta': {'serviceNo': '15'}},
            {'meta': {'serviceNo': 'INVALID VALUE'}}]}])
        rows = [{'stopCode': '01001', 'description': 'Imported stop', 'roadName': 'Imported road',
                 'services': ['15', '20']}]
        with patch.dict(os.environ, {'SAMPLE_STOPS': '01001', 'SAMPLE_SERVICES': '100,15'}), \
                patch.object(api, 'sql_query', return_value=rows), \
                patch.object(api, 'require_mongo', return_value=database):
            result = self.client.get('/sampling/options')
        self.assertEqual(result.json()['stops'], [{'stopCode': '01001', 'description': 'Imported stop',
                         'roadName': 'Imported road', 'services': ['100', '15']}])
        query, projection = database.arrival_latest.find.call_args.args
        self.assertEqual(query['_id'], {'$in': ['01001']})
        self.assertIsInstance(query['expiresAt']['$gt'], datetime)
        self.assertEqual(query['expiresAt']['$gt'].tzinfo, timezone.utc)
        self.assertEqual(projection, {'_id': 1, 'observations.meta.serviceNo': 1})

    def test_sql_failure_keeps_names_fallback_and_sampled_service_choices(self):
        database = self.database([{'_id': '01001', 'observations': [{'meta': {'serviceNo': '100'}}]}])
        with patch.dict(os.environ, {'SAMPLE_STOPS': '01001'}), \
                patch.object(api, 'sql_query', side_effect=OperationalError('password=SECRET')), \
                patch.object(api, 'require_mongo', return_value=database):
            result = self.client.get('/sampling/options')
        self.assertEqual(result.json()['stops'][0], {'stopCode': '01001', 'description': 'Bus stop 01001',
                         'roadName': '', 'services': ['100']})
        self.assertNotIn('SECRET', result.text)
        self.assertIn('warning', result.json())

    def test_mongo_failure_keeps_imported_choices(self):
        rows = [{'stopCode': '01001', 'description': 'Imported', 'roadName': 'Road', 'services': ['15']}]
        with patch.dict(os.environ, {'SAMPLE_STOPS': '01001'}), \
                patch.object(api, 'sql_query', return_value=rows), \
                patch.object(api, 'require_mongo', side_effect=ServerSelectionTimeoutError('SECRET')):
            result = self.client.get('/sampling/options')
        self.assertEqual(result.json()['stops'], rows)
        self.assertNotIn('SECRET', result.text)

    def test_invalid_config_is_not_exposed_or_queried(self):
        with patch.dict(os.environ, {'SAMPLE_STOPS': 'SECRET', 'SAMPLE_SERVICES': '15'}), \
                patch.object(api, 'sql_query', side_effect=AssertionError('unexpected SQL')):
            result = self.client.get('/sampling/options')
        self.assertFalse(result.json()['configured'])
        self.assertNotIn('SECRET', result.text)

    def test_unrelated_snapshot_cannot_add_an_unconfigured_stop(self):
        database = self.database([{'_id': '99999', 'observations': [{'meta': {'serviceNo': '100'}}]}])
        with patch.dict(os.environ, {'SAMPLE_STOPS': '01001'}), \
                patch.object(api, 'sql_query', return_value=[]), \
                patch.object(api, 'require_mongo', return_value=database):
            result = self.client.get('/sampling/options')
        self.assertEqual(len(result.json()['stops']), 1)
        self.assertEqual(result.json()['stops'][0]['services'], [])
