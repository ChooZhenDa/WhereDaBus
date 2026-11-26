"""Partial imports exclude whole incomplete directions without weakening checks."""
import copy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from backend.domain import select_complete_routes, validate_static
from backend.storage import import_static

ROOT = Path(__file__).resolve().parents[1]


class PartialStaticTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = json.loads((ROOT / 'fixtures/static.json').read_text())
        # DEMO1 now has sequences 1, 3, 4; DEMO2 remains complete.
        del self.snapshot['BusRoutes'][1]

    def select(self):
        return select_complete_routes(*(self.snapshot[k] for k in ('BusStops','BusServices','BusRoutes')))

    def sql_double(self, previous_count=None):
        connection = MagicMock()
        cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None if previous_count is None else (previous_count,)
        return connection, cursor

    def test_strict_default_still_rejects_gap_before_opening_sql(self):
        with patch('backend.storage.sql_connection') as connect:
            with self.assertRaisesRegex(ValueError, 'Incomplete stop sequence'):
                import_static(snapshot=self.snapshot)
        connect.assert_not_called()

    def test_select_keeps_only_complete_direction_and_does_not_mutate_snapshot(self):
        before = copy.deepcopy(self.snapshot)
        routes, missing, excluded = self.select()
        self.assertEqual(routes, before['BusRoutes'][-2:])
        self.assertEqual(missing, [{'serviceNo':'DEMO1','direction':1}])
        self.assertEqual(excluded, [{
            'serviceNo':'DEMO1','direction':1,'reason':'Incomplete stop sequence',
            'rowCount':3,'firstSequence':1,'lastSequence':4,
            'missingRanges':[{'from':2,'to':2}],'missingRangeCount':1,
        }])
        self.assertEqual(self.snapshot, before)

    def test_missing_first_sequence_excludes_entire_direction(self):
        self.snapshot['BusRoutes'] = [r for r in self.snapshot['BusRoutes']
                                      if not (r['ServiceNo'] == 'DEMO1' and r['StopSequence'] == 1)]
        routes, _, excluded = self.select()
        self.assertEqual(len(routes), 2)
        self.assertEqual(excluded[0]['missingRanges'], [{'from':1,'to':2}])

    def test_huge_gap_has_bounded_report(self):
        self.snapshot['BusRoutes'][2]['StopSequence'] = 10**9
        _, _, excluded = self.select()
        self.assertEqual(excluded[0]['missingRanges'][-1], {'from':4,'to':10**9-1})

    def test_missing_service_direction_is_reported_alongside_exclusion(self):
        service = copy.deepcopy(self.snapshot['BusServices'][0])
        service['Direction'] = 2
        self.snapshot['BusServices'].append(service)
        _, missing, excluded = self.select()
        self.assertEqual(missing, [{'serviceNo':'DEMO1','direction':1},
                                   {'serviceNo':'DEMO1','direction':2}])
        self.assertEqual(len(excluded), 1)

    def test_bad_fields_in_excluded_group_are_fatal_before_sql(self):
        for field,value in [('Distance', -1), ('Distance', 'NaN'),
                            ('BusStopCode', '99999'), ('ServiceNo', 'UNKNOWN'),
                            ('Operator', 'OTHER'), ('StopSequence', 0),
                            ('StopSequence', 1.5), ('StopSequence', True),
                            ('StopSequence', 2147483648),
                            ('Direction', 1.5)]:
            with self.subTest(field=field, value=value):
                snapshot = copy.deepcopy(self.snapshot)
                snapshot['BusRoutes'][0][field] = value
                with patch('backend.storage.sql_connection') as connect:
                    with self.assertRaises(ValueError):
                        import_static(snapshot=snapshot, skip_incomplete_routes=True)
                connect.assert_not_called()

    def test_duplicate_in_excluded_group_is_fatal(self):
        self.snapshot['BusRoutes'].append(copy.deepcopy(self.snapshot['BusRoutes'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate route occurrence'):
            self.select()

    def test_decreasing_distance_in_excluded_group_is_fatal(self):
        self.snapshot['BusRoutes'][2]['Distance'] = 1
        with self.assertRaisesRegex(ValueError, 'Cumulative route distance decreases'):
            self.select()

    def test_malformed_later_group_is_not_hidden_by_first_gap(self):
        self.snapshot['BusRoutes'][-1]['Distance'] = -1
        with self.assertRaisesRegex(ValueError, 'Invalid distance'):
            self.select()

    def test_no_complete_directions_refuses_to_connect(self):
        self.snapshot['BusRoutes'] = [r for r in self.snapshot['BusRoutes'] if r['ServiceNo'] == 'DEMO1']
        with patch('backend.storage.sql_connection') as connect:
            with self.assertRaisesRegex(ValueError, 'No complete route directions'):
                import_static(snapshot=self.snapshot, skip_incomplete_routes=True)
        connect.assert_not_called()

    def test_import_writes_retained_rows_and_complete_metadata_atomically(self):
        connection, cursor = self.sql_double()
        with patch('backend.storage.sql_connection', return_value=connection):
            with self.assertLogs('backend.storage', level='WARNING'):
                result = import_static(snapshot=self.snapshot, skip_incomplete_routes=True)
        batches = cursor.executemany.call_args_list
        services = next(c.args[1] for c in batches if 'INSERT INTO transport.bus_services' in c.args[0])
        routes = next(c.args[1] for c in batches if 'INSERT INTO transport.bus_routes' in c.args[0])
        self.assertEqual(len(services),2)
        self.assertEqual([(r[0],r[2]) for r in routes], [('DEMO2',1),('DEMO2',2)])
        self.assertEqual(result['routeOccurrences'],2)
        self.assertEqual(result['excludedRouteDirections'][0]['serviceNo'],'DEMO1')
        self.assertEqual(result['missingRouteDirections'], [{'serviceNo':'DEMO1','direction':1}])
        run = next(c for c in cursor.execute.call_args_list if 'INSERT INTO transport.import_runs' in c.args[0])
        self.assertEqual(run.args[1][-1],2)
        self.assertEqual(connection.__exit__.call_args.args, (None,None,None))

    def test_route_drop_guard_uses_retained_count_not_provider_count(self):
        # Five fetched rows would pass the old threshold of 3.2, but only two
        # rows are safe to import. The existing snapshot must remain untouched.
        connection, cursor = self.sql_double(previous_count=4)
        with patch('backend.storage.sql_connection', return_value=connection):
            with self.assertRaisesRegex(ValueError, 'Route count dropped'):
                import_static(snapshot=self.snapshot, skip_incomplete_routes=True)
        cursor.executemany.assert_not_called()
        self.assertFalse(any('DELETE FROM' in c.args[0] for c in cursor.execute.call_args_list))
        self.assertIs(connection.__exit__.call_args.args[0], ValueError)

    def test_explicit_large_change_override_still_requires_complete_directions(self):
        connection, _ = self.sql_double(previous_count=100)
        with patch('backend.storage.sql_connection', return_value=connection):
            with self.assertLogs('backend.storage', level='WARNING'):
                result = import_static(snapshot=self.snapshot, skip_incomplete_routes=True, allow_large_change=True)
        self.assertEqual(result['routeOccurrences'],2)

    def test_complete_snapshot_has_identical_rows_in_both_modes(self):
        snapshot = json.loads((ROOT / 'fixtures/static.json').read_text())
        self.assertEqual(validate_static(*(snapshot[k] for k in ('BusStops','BusServices','BusRoutes'))), [])
        routes, missing, excluded = select_complete_routes(*(snapshot[k] for k in ('BusStops','BusServices','BusRoutes')))
        self.assertEqual(routes,snapshot['BusRoutes'])
        self.assertEqual((missing,excluded),([],[]))

    def test_cli_passes_explicit_partial_import_option(self):
        from backend.cli import main
        with patch('sys.argv', ['backend.cli','sync-static','--skip-incomplete-routes']):
            with patch('backend.cli.LTAClient') as client, patch('backend.cli.import_static', return_value={}) as importer:
                with patch('sys.stdout', new_callable=io.StringIO):
                    main()
        importer.assert_called_once_with(client.return_value, allow_large_change=False,
                                         skip_incomplete_routes=True, keep_incomplete_routes=False)


if __name__ == '__main__':
    unittest.main()
