"""HTTP contracts for the GUI integration; no live database or LTA calls.

Install the application's dependencies and httpx before running this module.
The original dependency-free domain tests still run without these packages.
"""
import os
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock, patch

try:
    from fastapi.testclient import TestClient
    from psycopg import OperationalError
    from pymongo.errors import ServerSelectionTimeoutError
    from backend import api
except ModuleNotFoundError:
    API_AVAILABLE = False
else:
    API_AVAILABLE = True


@unittest.skipUnless(API_AVAILABLE, 'GUI API tests require FastAPI, httpx, psycopg and pymongo')
class GUIAPITests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.client_context = TestClient(api.app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.environment.stop()

    def test_gui_loads_without_database_configuration(self):
        result = self.client.get('/')
        self.assertEqual(result.status_code, 200)
        self.assertIn('text/html', result.headers['content-type'])
        self.assertIn('WhereDaBus', result.text)

    def test_config_contains_booleans_and_never_connection_values(self):
        with patch.dict(os.environ, {
            'SQL_DSN': 'secret-sql', 'MONGO_URI': 'secret-mongo',
            'LTA_ACCOUNT_KEY': 'secret-key',
        }):
            result = self.client.get('/ui/config')
        self.assertEqual(result.json(), {
            'mode': 'live', 'configured': {'sql': True, 'mongo': True, 'lta': True},
            'ready': True, 'timezone': 'Asia/Singapore',
        })
        self.assertNotIn('secret', result.text)

    def test_disconnected_features_return_actionable_503(self):
        for url in (
            '/health', '/routes/longest', '/stops/search?query=campus',
            '/stops/nearby?lat=1.3&lon=103.8', '/arrivals/01001',
            '/sampled-arrivals/01001', '/analytics/peak?service=1&stop=01001',
            '/analytics/trend?service=1&stop=01001',
        ):
            with self.subTest(url=url):
                result = self.client.get(url)
                self.assertEqual(result.status_code, 503)
                self.assertEqual(result.json()['detail']['status'], 'unavailable')
                self.assertIn('Configure', result.json()['detail']['message'])

    def test_invalid_inputs_are_rejected_before_database_access(self):
        for url in (
            '/arrivals/not-a-stop', '/sampled-arrivals/12',
            '/stops/nearby?lat=91&lon=103.8', '/stops/nearby?lat=1.3&lon=103.8&radius=6000',
            '/routes/direct?origin=123&destination=01002',
            '/analytics/peak?service=x%20y&stop=01001',
            '/stops/search?query=%20%20', '/stops/search?query=a&limit=1000',
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 422)

    def test_stop_search_is_parameterized_and_escapes_wildcards(self):
        rows = [{'stop_code': '01001', 'description': 'Campus',
                 'road_name': 'Campus Road', 'latitude': 1.3, 'longitude': 103.8}]
        with patch.object(api, 'sql_query', return_value=rows) as query:
            result = self.client.get('/stops/search', params={'query': " a%_' ", 'limit': 5})
        self.assertEqual(result.json(), rows)
        statement, parameters = query.call_args.args
        self.assertNotIn("a%_'", statement)
        self.assertIn('WHERE is_active', statement)
        self.assertEqual(parameters, ("%a\\%\\_'%", "%a\\%\\_'%", "%a\\%\\_'%", "a%_'", 5))

    def test_existing_route_contract_is_preserved(self):
        rows = [{'service_no': 'DEMO1', 'direction': 1}]
        with patch.object(api, 'sql_query', return_value=rows) as query:
            result = self.client.get('/routes/direct?origin=01001&destination=01002')
        self.assertEqual(result.json(), rows)
        query.assert_called_once_with('SELECT * FROM transport.direct_routes(%s,%s)', ('01001', '01002'))

    def test_route_examples_preserve_stop_codes_and_need_no_mongo(self):
        rows = [{'origin_code': '01001', 'origin_description': 'Campus',
                 'destination_code': '01003', 'destination_description': 'Station',
                 'service_no': 'DEMO1', 'direction': 1}]
        with patch.object(api, 'sql_query', return_value=rows) as query, \
                patch.object(api, 'require_mongo', side_effect=AssertionError('not needed')):
            result = self.client.get('/routes/examples')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), rows)
        query.assert_called_once_with(api.DIRECT_ROUTE_EXAMPLES_SQL)

    def test_route_examples_do_not_invent_pairs_without_imported_data(self):
        with patch.object(api, 'sql_query', return_value=[]):
            result = self.client.get('/routes/examples')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), [])

    def test_route_example_failures_hide_database_details(self):
        with patch.object(api, 'sql_query', side_effect=OperationalError('secret-dsn')):
            result = self.client.get('/routes/examples')
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json()['detail']['reason'], 'sql_unavailable')
        self.assertNotIn('secret-dsn', result.text)

    def test_sql_errors_hide_connection_details(self):
        with patch.object(api, 'sql_query', side_effect=OperationalError('password=very-secret')):
            result = self.client.get('/routes/longest')
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json()['detail']['reason'], 'sql_unavailable')
        self.assertNotIn('very-secret', result.text)
        self.assertEqual(result.headers['retry-after'], '10')

    def test_mongo_errors_hide_connection_details(self):
        database = Mock()
        database.arrival_observations.aggregate.side_effect = ServerSelectionTimeoutError('secret-mongo-host')
        with patch.object(api, 'require_mongo', return_value=database):
            result = self.client.get('/analytics/peak?service=1&stop=01001')
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json()['detail']['reason'], 'mongo_unavailable')
        self.assertNotIn('secret-mongo-host', result.text)

    def test_live_stale_status_and_refresh_delay_are_preserved(self):
        snapshot = {'status': 'stale', 'stopCode': '01001', 'observations': [],
                    'reason': 'provider_cooldown', 'refreshAfterSeconds': 90}
        with patch.object(api, 'require_mongo', return_value=Mock()):
            api.app.state.live = Mock()
            api.app.state.live.get.return_value = snapshot
            result = self.client.get('/arrivals/01001')
        self.assertEqual(result.json(), snapshot)

    def test_data_status_is_available_without_config_and_hides_secrets(self):
        with patch.dict(os.environ, {'LTA_ACCOUNT_KEY': 'private-key'}):
            result = self.client.get('/data/status')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['routes'], {'status': 'unavailable'})
        self.assertFalse(result.json()['sampling']['configured'])
        self.assertNotIn('private-key', result.text)

    def test_route_coverage_does_not_require_mongo(self):
        coverage = {'stops': 5209, 'serviceDirections': 100, 'availableDirections': 99,
                    'unavailableDirections': 1,
                    'unavailableExamples': [{'serviceNo': '100', 'direction': 1}],
                    'lastImportAt': None}
        with patch.object(api, 'sql_query', return_value=[coverage]), \
                patch.object(api, 'require_mongo', side_effect=AssertionError('must not access Mongo')):
            result = self.client.get('/data/status?section=routes')
        self.assertEqual(result.json()['routes'], {'status': 'available', **coverage})
        self.assertNotIn('sampling', result.json())

    def test_data_status_keeps_sampling_results_when_sql_fails(self):
        now = datetime.now(timezone.utc)
        database = Mock()
        database.arrival_latest.find.return_value.limit.return_value.max_time_ms.return_value = [
            {'_id': '01001', 'capturedAt': now, 'expiresAt': now + timedelta(minutes=1)},
            {'_id': '01002', 'capturedAt': now - timedelta(minutes=3),
             'expiresAt': now - timedelta(minutes=1)},
        ]
        with patch.dict(os.environ, {'SAMPLE_STOPS': '01002, 01001,01001', 'SAMPLE_SERVICES': '15'}), \
                patch.object(api, 'sql_query', side_effect=OperationalError('secret-password')), \
                patch.object(api, 'require_mongo', return_value=database):
            result = self.client.get('/data/status')
        sampling = result.json()['sampling']
        self.assertEqual(result.json()['routes'], {'status': 'unavailable'})
        self.assertEqual(sampling['stops'], ['01001', '01002'])
        self.assertEqual(sampling['services'], ['15'])
        self.assertEqual(sampling['freshStops'], ['01001'])
        self.assertTrue(sampling['latestSnapshotAt'].startswith(now.isoformat()[:19]))
        self.assertEqual(sampling['storageStatus'], 'available')
        self.assertNotIn('secret-password', result.text)
        database.arrival_latest.find.assert_called_once_with(
            {'_id': {'$in': ['01001', '01002']}}, {'_id': 1, 'capturedAt': 1, 'expiresAt': 1})

    def test_data_status_keeps_route_coverage_when_mongo_fails(self):
        with patch.dict(os.environ, {'SAMPLE_STOPS': '01001'}), \
                patch.object(api, 'sql_query', return_value=[{'availableDirections': 9}]), \
                patch.object(api, 'require_mongo', side_effect=ServerSelectionTimeoutError('secret-host')):
            result = self.client.get('/data/status')
        self.assertEqual(result.json()['routes']['availableDirections'], 9)
        self.assertEqual(result.json()['sampling']['storageStatus'], 'unavailable')
        self.assertNotIn('secret-host', result.text)

    def test_invalid_sampling_values_are_not_exposed_or_queried(self):
        with patch.dict(os.environ, {'SAMPLE_STOPS': 'private-mistaken-value'}), \
                patch.object(api, 'require_mongo', side_effect=AssertionError('must not access Mongo')):
            result = self.client.get('/data/status?section=sampling')
        self.assertFalse(result.json()['sampling']['configured'])
        self.assertEqual(result.json()['sampling']['stops'], [])
        self.assertNotIn('private-mistaken-value', result.text)
        self.assertNotIn('routes', result.json())

    def test_status_rejects_unknown_section(self):
        self.assertEqual(self.client.get('/data/status?section=credentials').status_code, 422)


if __name__ == '__main__':
    unittest.main()
