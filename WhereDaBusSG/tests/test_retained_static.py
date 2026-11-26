"""Coverage import keeps authentic rows without weakening structural integrity."""
import copy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from backend.domain import retain_available_routes, validate_static
from backend.diagnostics import diagnose_static
from backend.storage import import_static

ROOT = Path(__file__).resolve().parents[1]


class RetainedStaticTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = json.loads((ROOT / 'fixtures/static.json').read_text())
        # DEMO1 now contains authentic occurrences 1, 3, 4; DEMO2 is unchanged.
        del self.snapshot['BusRoutes'][1]

    def retain(self):
        return retain_available_routes(*(self.snapshot[k] for k in ('BusStops','BusServices','BusRoutes')))

    def sql_double(self, previous_count=None):
        connection = MagicMock()
        cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None if previous_count is None else (previous_count,)
        return connection, cursor

    def test_retains_authentic_sequences_and_distances_without_mutating_source(self):
        before = copy.deepcopy(self.snapshot)
        routes, missing, partial = self.retain()
        self.assertEqual(routes, before['BusRoutes'])
        self.assertEqual(self.snapshot, before)
        self.assertIsNot(routes, self.snapshot['BusRoutes'])
        self.assertEqual(missing, [])
        self.assertEqual(partial[0]['serviceNo'], 'DEMO1')
        self.assertEqual(partial[0]['missingRanges'], [{'from':2,'to':2}])
        self.assertEqual([r['StopSequence'] for r in routes[:3]], [1,3,4])

    def test_all_directions_may_be_partial_if_their_rows_are_valid(self):
        self.snapshot['BusRoutes'] = [r for r in self.snapshot['BusRoutes'] if r['ServiceNo']=='DEMO1']
        routes, missing, partial = self.retain()
        self.assertEqual(len(routes), 3)
        self.assertEqual(missing, [{'serviceNo':'DEMO2','direction':1}])
        self.assertEqual(len(partial), 1)

    def test_missing_beginning_is_retained_and_reported(self):
        self.snapshot['BusRoutes'] = [r for r in self.snapshot['BusRoutes']
                                      if not (r['ServiceNo']=='DEMO1' and r['StopSequence']==1)]
        routes, _, partial = self.retain()
        self.assertEqual(len(routes),4)
        self.assertEqual(partial[0]['firstSequence'],3)
        self.assertEqual(partial[0]['missingRanges'],[{'from':1,'to':2}])

    def test_huge_sequence_gap_is_reported_without_expansion(self):
        self.snapshot['BusRoutes'][2]['StopSequence'] = 2147483647
        _, _, partial = self.retain()
        self.assertEqual(partial[0]['missingRanges'][-1],{'from':4,'to':2147483646})
        self.assertEqual(partial[0]['rowCount'],3)

    def test_only_absent_service_directions_are_missing(self):
        other=copy.deepcopy(self.snapshot['BusServices'][0]);other['Direction']=2
        self.snapshot['BusServices'].append(other)
        _, missing, partial = self.retain()
        self.assertEqual(missing,[{'serviceNo':'DEMO1','direction':2}])
        self.assertEqual([(x['serviceNo'],x['direction']) for x in partial],[('DEMO1',1)])

    def test_empty_route_feed_still_refuses_to_connect(self):
        self.snapshot['BusRoutes']=[]
        with patch('backend.storage.sql_connection') as connect:
            with self.assertRaisesRegex(ValueError,'empty infrastructure'):
                import_static(snapshot=self.snapshot,keep_incomplete_routes=True)
        connect.assert_not_called()

    def test_malformed_retained_rows_still_refuse_to_connect(self):
        for field,value in [('Distance',-1),('Distance','NaN'),('Distance',1000),
                            ('BusStopCode','99999'),('ServiceNo','UNKNOWN'),
                            ('Operator','OTHER'),('StopSequence',0),('StopSequence',1.5),
                            ('StopSequence',True),('StopSequence',2147483648),('Direction',1.5)]:
            with self.subTest(field=field,value=value):
                snapshot=copy.deepcopy(self.snapshot)
                snapshot['BusRoutes'][0][field]=value
                with patch('backend.storage.sql_connection') as connect:
                    with self.assertRaises((ValueError,ArithmeticError)):
                        import_static(snapshot=snapshot,keep_incomplete_routes=True)
                connect.assert_not_called()

    def test_duplicate_occurrences_still_fail(self):
        self.snapshot['BusRoutes'].append(copy.deepcopy(self.snapshot['BusRoutes'][0]))
        with self.assertRaisesRegex(ValueError,'duplicate route occurrence'):
            self.retain()

    def test_distance_decrease_in_any_direction_still_fails(self):
        self.snapshot['BusRoutes'][2]['Distance']=1
        with self.assertRaisesRegex(ValueError,'Cumulative route distance'):
            self.retain()

    def test_strict_validation_remains_opt_in_default(self):
        with self.assertRaisesRegex(ValueError,'Incomplete stop sequence'):
            validate_static(*(self.snapshot[k] for k in ('BusStops','BusServices','BusRoutes')))

    def test_import_writes_all_occurrences_and_reports_partial_not_missing(self):
        connection,cursor=self.sql_double(previous_count=5)
        with patch('backend.storage.sql_connection',return_value=connection):
            with self.assertLogs('backend.storage',level='WARNING'):
                result=import_static(snapshot=self.snapshot,keep_incomplete_routes=True)
        rows=next(c.args[1] for c in cursor.executemany.call_args_list
                  if c.args[0].startswith('INSERT INTO transport.bus_routes'))
        self.assertEqual([(r[0],r[2]) for r in rows],
                         [(r['ServiceNo'],r['StopSequence']) for r in self.snapshot['BusRoutes']])
        self.assertEqual(result['routeOccurrences'],5)
        self.assertEqual(result['mode'],'keep-incomplete-routes')
        self.assertEqual(result['excludedRouteDirections'],[])
        self.assertEqual(result['missingRouteDirections'],[])
        self.assertEqual(len(result['partialRouteDirections']),1)
        self.assertNotIn('only complete', ' '.join(result['warnings']))
        self.assertEqual(connection.__exit__.call_args.args,(None,None,None))
        run=next(c for c in cursor.execute.call_args_list if 'INSERT INTO transport.import_runs' in c.args[0])
        self.assertEqual(run.args[1][-1],5)

    def test_real_row_drop_guard_uses_retained_count_before_writing(self):
        connection,cursor=self.sql_double(previous_count=100)
        with patch('backend.storage.sql_connection',return_value=connection):
            with self.assertRaisesRegex(ValueError,'Route count dropped'):
                import_static(snapshot=self.snapshot,keep_incomplete_routes=True)
        cursor.executemany.assert_not_called()
        self.assertFalse(any('DELETE FROM' in c.args[0] for c in cursor.execute.call_args_list))
        self.assertIs(connection.__exit__.call_args.args[0],ValueError)

    def test_driver_failure_propagates_out_of_transaction(self):
        connection,cursor=self.sql_double()
        def fail_routes(statement,rows):
            if statement.startswith('INSERT INTO transport.bus_routes'):
                raise RuntimeError('test write failure')
        cursor.executemany.side_effect=fail_routes
        with patch('backend.storage.sql_connection',return_value=connection):
            with self.assertRaisesRegex(RuntimeError,'test write failure'):
                import_static(snapshot=self.snapshot,keep_incomplete_routes=True)
        self.assertIs(connection.__exit__.call_args.args[0],RuntimeError)
        self.assertFalse(any('INSERT INTO transport.import_runs' in c.args[0]
                             for c in cursor.execute.call_args_list))

    def test_conflicting_import_policies_fail_before_network_or_sql(self):
        client=MagicMock()
        with patch('backend.storage.sql_connection') as connect:
            with self.assertRaisesRegex(ValueError,'only one'):
                import_static(client,skip_incomplete_routes=True,keep_incomplete_routes=True)
        client.all_rows.assert_not_called();connect.assert_not_called()

    def test_cli_passes_retention_policy(self):
        from backend.cli import main
        with patch('sys.argv',['backend.cli','sync-static','--keep-incomplete-routes']):
            with patch('backend.cli.LTAClient') as client, patch('backend.cli.import_static',return_value={}) as importer:
                with patch('sys.stdout',new_callable=io.StringIO):main()
        importer.assert_called_once_with(client.return_value,allow_large_change=False,
                                         skip_incomplete_routes=False,keep_incomplete_routes=True)

    def test_cli_rejects_conflicting_policies(self):
        from backend.cli import main
        with patch('sys.argv',['backend.cli','sync-static','--keep-incomplete-routes','--skip-incomplete-routes']):
            with patch('backend.cli.LTAClient') as client, patch('sys.stderr',new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as error:main()
        self.assertEqual(error.exception.code,2);client.assert_not_called()

    def test_diagnostics_distinguishes_strict_from_coverage_import(self):
        client=MagicMock();client.all_rows.side_effect=lambda key:self.snapshot[key]
        with patch('backend.storage.sql_connection') as connect:
            result=diagnose_static(client)
        connect.assert_not_called()
        self.assertFalse(result['validForFullImport'])
        self.assertTrue(result['validForCoverageImport'])
        self.assertIsNone(result['coverageValidationError'])
        self.assertEqual(result['retainedRouteOccurrences'],5)
        self.assertEqual(result['partialDirectionCount'],1)
        self.assertFalse(result['databaseChanged'])

    def test_diagnostics_does_not_approve_bad_references(self):
        self.snapshot['BusRoutes'][0]['BusStopCode']='99999'
        client=MagicMock();client.all_rows.side_effect=lambda key:self.snapshot[key]
        result=diagnose_static(client)
        self.assertFalse(result['validForCoverageImport'])
        self.assertEqual(result['retainedRouteOccurrences'],0)
        self.assertIn('Dangling',result['coverageValidationError'])

    def test_diagnostics_normalizes_equivalent_direction_representations(self):
        for service_direction,route_direction in [(1,'1'),('1',1.0),(1.0,1)]:
            with self.subTest(service=service_direction,route=route_direction):
                snapshot=copy.deepcopy(self.snapshot)
                for row in snapshot['BusServices']:row['Direction']=service_direction
                for row in snapshot['BusRoutes']:row['Direction']=route_direction
                missing=copy.deepcopy(snapshot['BusServices'][0]);missing['Direction']='2'
                snapshot['BusServices'].append(missing)
                client=MagicMock();client.all_rows.side_effect=lambda key:snapshot[key]
                result=diagnose_static(client)
                self.assertTrue(result['validForCoverageImport'])
                self.assertEqual(result['missingRouteDirections'],[{'serviceNo':'DEMO1','direction':2}])

    def test_diagnostics_does_not_coerce_invalid_direction_to_valid_key(self):
        for invalid in (True,1.5,'not-a-direction',None):
            with self.subTest(direction=invalid):
                snapshot=copy.deepcopy(self.snapshot)
                for row in snapshot['BusRoutes']:
                    if row['ServiceNo']=='DEMO1':row['Direction']=invalid
                client=MagicMock();client.all_rows.side_effect=lambda key:snapshot[key]
                result=diagnose_static(client)
                self.assertFalse(result['validForCoverageImport'])
                self.assertEqual(result['missingRouteDirections'],[{'serviceNo':'DEMO1','direction':1}])

    def test_normal_refresh_selects_retention_policy(self):
        launcher=(ROOT/'scripts/manage.cmd').read_text()
        self.assertIn('backend.cli sync-static --keep-incomplete-routes',launcher)
        self.assertNotIn('backend.cli sync-static --skip-incomplete-routes',launcher)


if __name__=='__main__':unittest.main()
