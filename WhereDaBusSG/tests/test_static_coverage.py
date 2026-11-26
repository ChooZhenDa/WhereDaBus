"""Regression coverage for a service direction absent from the route feed."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from backend.domain import validate_static
from backend.storage import import_static

ROOT = Path(__file__).resolve().parents[1]

class StaticCoverageTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = json.loads((ROOT / 'fixtures/static.json').read_text())

    def validate(self):
        return validate_static(*(self.snapshot[k] for k in ('BusStops','BusServices','BusRoutes')))

    def add_unrouted_direction(self):
        service = copy.deepcopy(self.snapshot['BusServices'][0])
        service['Direction'] = 2
        self.snapshot['BusServices'].append(service)

    def sql_double(self, previous_count=None):
        connection = MagicMock()
        cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None if previous_count is None else (previous_count,)
        return connection, cursor

    def test_complete_fixture_has_no_coverage_gaps(self):
        self.assertEqual(self.validate(), [])

    def test_missing_reverse_direction_is_reported_without_mutating_data(self):
        self.add_unrouted_direction()
        before = copy.deepcopy(self.snapshot)
        self.assertEqual(self.validate(), [{'serviceNo':'DEMO1','direction':2}])
        self.assertEqual(self.snapshot, before)

    def test_service_with_no_route_rows_is_reported(self):
        self.snapshot['BusRoutes'] = [r for r in self.snapshot['BusRoutes'] if r['ServiceNo'] != 'DEMO2']
        self.assertEqual(self.validate(), [{'serviceNo':'DEMO2','direction':1}])

    def test_multiple_coverage_gaps_have_stable_order(self):
        self.add_unrouted_direction()
        self.snapshot['BusRoutes'] = [r for r in self.snapshot['BusRoutes'] if r['ServiceNo'] != 'DEMO2']
        self.assertEqual(self.validate(), [
            {'serviceNo':'DEMO1','direction':2},
            {'serviceNo':'DEMO2','direction':1},
        ])

    def test_entirely_empty_routes_are_still_rejected(self):
        self.snapshot['BusRoutes'] = []
        with self.assertRaisesRegex(ValueError, 'empty infrastructure'):
            self.validate()

    def test_gap_in_a_present_route_is_still_rejected(self):
        self.add_unrouted_direction()
        del self.snapshot['BusRoutes'][1]
        with self.assertRaisesRegex(ValueError, 'Incomplete stop sequence'):
            self.validate()

    def test_unknown_service_reference_is_still_rejected(self):
        self.add_unrouted_direction()
        self.snapshot['BusRoutes'][0]['ServiceNo'] = 'UNKNOWN'
        with self.assertRaisesRegex(ValueError, 'Dangling or duplicate'):
            self.validate()

    def test_unknown_stop_reference_is_still_rejected(self):
        self.add_unrouted_direction()
        self.snapshot['BusRoutes'][0]['BusStopCode'] = '99999'
        with self.assertRaisesRegex(ValueError, 'Dangling or duplicate'):
            self.validate()

    def test_inconsistent_operator_is_still_rejected(self):
        self.add_unrouted_direction()
        self.snapshot['BusRoutes'][0]['Operator'] = 'OTHER'
        with self.assertRaisesRegex(ValueError, 'Inconsistent operator'):
            self.validate()

    def test_duplicate_occurrence_is_still_rejected(self):
        self.add_unrouted_direction()
        self.snapshot['BusRoutes'].append(copy.deepcopy(self.snapshot['BusRoutes'][0]))
        with self.assertRaisesRegex(ValueError, 'Dangling or duplicate'):
            self.validate()

    def test_decreasing_distance_is_still_rejected(self):
        self.add_unrouted_direction()
        self.snapshot['BusRoutes'][2]['Distance'] = 0.5
        with self.assertRaisesRegex(ValueError, 'Cumulative route distance'):
            self.validate()

    def test_import_keeps_stops_and_services_and_reports_missing_routes(self):
        self.add_unrouted_direction()
        connection, cursor = self.sql_double()
        with patch('backend.storage.sql_connection', return_value=connection):
            with self.assertLogs('backend.storage', level='WARNING') as captured:
                result = import_static(snapshot=self.snapshot)
        self.assertEqual(result['stops'],3)
        self.assertEqual(result['serviceDirections'],3)
        self.assertEqual(result['routeOccurrences'],6)
        self.assertEqual(result['missingRouteDirections'],[{'serviceNo':'DEMO1','direction':2}])
        self.assertTrue(result['warnings'])
        self.assertIn('no route rows',captured.output[0])
        # Observe what would be sent to SQL, without pretending this executes SQL.
        batches = {call.args[0].split('VALUES')[0].strip():call.args[1]
                   for call in cursor.executemany.call_args_list}
        stops = next(rows for sql,rows in batches.items() if 'INSERT INTO transport.bus_stops' in sql)
        services = next(rows for sql,rows in batches.items() if 'INSERT INTO transport.bus_services' in sql)
        routes = next(rows for sql,rows in batches.items() if 'INSERT INTO transport.bus_routes' in sql)
        self.assertEqual({r[0] for r in stops},{'01001','01002','01003'})
        self.assertIn(('DEMO1',2),{(r[0],r[1]) for r in services})
        self.assertNotIn(('DEMO1',2),{(r[0],r[1]) for r in routes})
        self.assertEqual(len(routes),6)
        self.assertEqual(connection.__exit__.call_args.args,(None,None,None))

    def test_malformed_snapshot_is_rejected_before_sql(self):
        self.add_unrouted_direction()
        self.snapshot['BusRoutes'][0]['Distance'] = -1
        with patch('backend.storage.sql_connection') as connect:
            with self.assertRaisesRegex(ValueError, 'Invalid distance'):
                import_static(snapshot=self.snapshot)
            connect.assert_not_called()

    def test_large_route_drop_guard_still_blocks_before_writes(self):
        self.add_unrouted_direction()
        connection, cursor = self.sql_double(previous_count=100)
        with patch('backend.storage.sql_connection', return_value=connection):
            with self.assertRaisesRegex(ValueError, 'Route count dropped'):
                import_static(snapshot=self.snapshot)
        cursor.executemany.assert_not_called()
        self.assertIs(connection.__exit__.call_args.args[0],ValueError)

    def test_clean_import_has_no_coverage_warnings(self):
        connection, _ = self.sql_double()
        with patch('backend.storage.sql_connection', return_value=connection):
            result = import_static(snapshot=self.snapshot)
        self.assertEqual(result['missingRouteDirections'],[])
        self.assertEqual(result['warnings'],[])

if __name__ == '__main__':
    unittest.main()
