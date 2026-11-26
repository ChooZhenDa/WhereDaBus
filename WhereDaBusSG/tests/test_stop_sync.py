"""Regression tests: nearby-stop imports must not depend on route coverage."""
import copy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock,patch

from backend.stops import sync_stops,validated_stop_rows
from backend.diagnostics import diagnose_static,sequence_issues
from backend.domain import validate_static
from backend import cli

ROOT=Path(__file__).resolve().parents[1]

class StopSyncTests(unittest.TestCase):
    def setUp(self):
        self.snapshot=json.loads((ROOT/'fixtures/static.json').read_text())
        self.stops=self.snapshot['BusStops']

    def connection(self,previous_count=0,retired=0):
        conn=MagicMock()
        cur=conn.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value=(previous_count,)
        cur.rowcount=retired
        return conn,cur

    def test_valid_rows_keep_codes_and_do_not_mutate_input(self):
        before=copy.deepcopy(self.stops)
        rows=validated_stop_rows(self.stops)
        self.assertEqual(rows[0],('01001','Demo Road','Demo Campus',1.3,103.8))
        self.assertEqual(self.stops,before)

    def test_numeric_coordinate_strings_are_converted(self):
        self.stops[0].update(Latitude='1.344',Longitude='103.701')
        self.assertEqual(validated_stop_rows(self.stops)[0][-2:],(1.344,103.701))

    def test_invalid_feeds_fail_before_sql(self):
        bad=[None,{},[],[None],self.stops+[copy.deepcopy(self.stops[0])]]
        for field,value in [('BusStopCode','1001'),('Latitude','NaN'),
                            ('Longitude','Infinity'),('Latitude',91),
                            ('Longitude',181),('Latitude',True),('Latitude',None),
                            ('Description',''),('Description',None),('RoadName',None)]:
            changed=copy.deepcopy(self.stops)
            changed[0][field]=value
            bad.append(changed)
        for records in bad:
            with self.subTest(records=records):
                with patch('backend.stops.sql_connection') as connect:
                    with self.assertRaises(ValueError):
                        sync_stops(records=records)
                    connect.assert_not_called()

    def test_fetches_only_bus_stops_and_preserves_route_service_tables(self):
        client=MagicMock()
        client.all_rows.side_effect=lambda name: self.stops if name=='BusStops' else self.fail('Unexpected feed request')
        conn,cur=self.connection(previous_count=3,retired=1)
        with patch('backend.stops.sql_connection',return_value=conn):
            result=sync_stops(client)
        client.all_rows.assert_called_once_with('BusStops')
        self.assertEqual(result['stops'],3)
        self.assertEqual(result['deactivatedStops'],1)
        self.assertFalse(result['routesUpdated'])
        self.assertEqual(result['mode'],'stops-only')
        writes=[call.args[0] for call in cur.execute.call_args_list+cur.executemany.call_args_list
                if call.args[0].lstrip().split()[0].upper() in ('INSERT','UPDATE','DELETE','TRUNCATE')]
        self.assertTrue(writes)
        for sql in writes:
            self.assertIn('transport.bus_stops',sql)
            self.assertNotIn('transport.bus_routes',sql)
            self.assertNotIn('transport.bus_services',sql)
            self.assertFalse(sql.lstrip().startswith(('DELETE','TRUNCATE')))
        self.assertEqual(conn.__exit__.call_args.args,(None,None,None))

    def test_upsert_reactivates_seen_stops_and_retires_missing_without_deleting(self):
        conn,cur=self.connection(previous_count=3,retired=1)
        with patch('backend.stops.sql_connection',return_value=conn):
            sync_stops(records=self.stops)
        query,rows=cur.executemany.call_args.args
        self.assertIn('ON CONFLICT(stop_code) DO UPDATE SET',query)
        self.assertIn('is_active=true',query)
        retirement=next(call for call in cur.execute.call_args_list if 'SET is_active=false' in call.args[0])
        self.assertEqual(retirement.args[1],(['01001','01002','01003'],))
        self.assertEqual(len(rows),3)

    def test_same_import_lock_is_acquired_before_writes(self):
        conn,cur=self.connection()
        with patch('backend.stops.sql_connection',return_value=conn):
            sync_stops(records=self.stops)
        self.assertEqual(cur.method_calls[0].args[0],'SELECT pg_advisory_xact_lock(51003)')

    def test_large_count_drop_prevents_upsert_and_retirement(self):
        conn,cur=self.connection(previous_count=100)
        with patch('backend.stops.sql_connection',return_value=conn):
            with self.assertRaisesRegex(ValueError,'Bus-stop count dropped'):
                sync_stops(records=self.stops)
        cur.executemany.assert_not_called()
        self.assertFalse(any('UPDATE transport' in call.args[0] for call in cur.execute.call_args_list))
        self.assertIs(conn.__exit__.call_args.args[0],ValueError)

    def test_explicit_large_change_override_is_required_to_proceed(self):
        conn,cur=self.connection(previous_count=100)
        with patch('backend.stops.sql_connection',return_value=conn):
            result=sync_stops(records=self.stops,allow_large_change=True)
        self.assertEqual(result['stops'],3)
        cur.executemany.assert_called_once()

    def test_network_failure_precedes_database_access(self):
        client=MagicMock()
        client.all_rows.side_effect=TimeoutError('provider timeout')
        with patch('backend.stops.sql_connection') as connect:
            with self.assertRaises(TimeoutError):
                sync_stops(client)
            connect.assert_not_called()

    def test_sql_failure_is_not_reported_as_success(self):
        conn,cur=self.connection()
        cur.executemany.side_effect=RuntimeError('test SQL failure')
        with patch('backend.stops.sql_connection',return_value=conn):
            with self.assertRaisesRegex(RuntimeError,'test SQL failure'):
                sync_stops(records=self.stops)
        self.assertIs(conn.__exit__.call_args.args[0],RuntimeError)
        self.assertFalse(any('SET is_active=false' in call.args[0] for call in cur.execute.call_args_list))

    def test_gap_in_routes_does_not_block_stop_only_sync(self):
        del self.snapshot['BusRoutes'][1]
        with self.assertRaisesRegex(ValueError,'Incomplete stop sequence'):
            validate_static(*(self.snapshot[k] for k in ('BusStops','BusServices','BusRoutes')))
        conn,_=self.connection()
        with patch('backend.stops.sql_connection',return_value=conn):
            result=sync_stops(records=self.stops)
        self.assertEqual(result['stops'],3)

class RouteDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.snapshot=json.loads((ROOT/'fixtures/static.json').read_text())

    def test_complete_routes_report_no_gaps(self):
        self.assertEqual(sequence_issues(self.snapshot['BusRoutes'])['routeSequenceIssueCount'],0)

    def test_reports_exact_service_direction_and_missing_sequence(self):
        del self.snapshot['BusRoutes'][1]
        report=sequence_issues(self.snapshot['BusRoutes'])
        self.assertEqual(report['routeSequenceIssueCount'],1)
        issue=report['sequenceIssues'][0]
        self.assertEqual((issue['serviceNo'],issue['direction']),('DEMO1',1))
        self.assertEqual(issue['missingRanges'],[{'from':2,'to':2}])

    def test_unsorted_input_is_not_a_gap(self):
        self.snapshot['BusRoutes'].reverse()
        self.assertEqual(sequence_issues(self.snapshot['BusRoutes'])['routeSequenceIssueCount'],0)

    def test_missing_first_sequence_is_reported(self):
        del self.snapshot['BusRoutes'][0]
        issue=sequence_issues(self.snapshot['BusRoutes'])['sequenceIssues'][0]
        self.assertEqual(issue['missingRanges'],[{'from':1,'to':1}])

    def test_duplicates_are_reported(self):
        self.snapshot['BusRoutes'].append(copy.deepcopy(self.snapshot['BusRoutes'][0]))
        issue=sequence_issues(self.snapshot['BusRoutes'])['sequenceIssues'][0]
        self.assertEqual(issue['duplicateSequences'],[1])

    def test_large_gap_does_not_expand_into_a_huge_list(self):
        routes=[{'ServiceNo':'X','Direction':1,'StopSequence':1000000000}]
        issue=sequence_issues(routes)['sequenceIssues'][0]
        self.assertEqual(issue['missingRanges'],[{'from':1,'to':999999999}])

    def test_invalid_sequence_values_are_reported(self):
        routes=[{'ServiceNo':'X','Direction':1,'StopSequence':x} for x in (0,-1,True,1.2,'bad',None)]
        self.assertEqual(len(sequence_issues(routes)['invalidRowExamples']),6)

    def test_group_examples_are_bounded(self):
        routes=[{'ServiceNo':str(n),'Direction':1,'StopSequence':2} for n in range(30)]
        report=sequence_issues(routes,max_examples=5)
        self.assertEqual(report['routeSequenceIssueCount'],30)
        self.assertEqual(len(report['sequenceIssues']),5)
        self.assertTrue(report['examplesTruncated'])

    def test_read_only_diagnostic_reports_gaps_without_sql(self):
        del self.snapshot['BusRoutes'][1]
        client=MagicMock()
        client.all_rows.side_effect=lambda name:self.snapshot[name]
        with patch('backend.storage.sql_connection') as connect:
            result=diagnose_static(client)
            connect.assert_not_called()
        self.assertFalse(result['validForFullImport'])
        self.assertFalse(result['databaseChanged'])
        self.assertEqual(result['sequenceIssues'][0]['serviceNo'],'DEMO1')
        self.assertIn('Incomplete stop sequence',result['validationError'])

    def test_full_import_error_identifies_the_route(self):
        del self.snapshot['BusRoutes'][1]
        with self.assertRaisesRegex(ValueError,'service DEMO1, direction 1'):
            validate_static(*(self.snapshot[k] for k in ('BusStops','BusServices','BusRoutes')))

    def test_clean_diagnostic_is_valid(self):
        client=MagicMock()
        client.all_rows.side_effect=lambda name:self.snapshot[name]
        result=diagnose_static(client)
        self.assertTrue(result['validForFullImport'])
        self.assertIsNone(result['validationError'])

class StopCommandsTests(unittest.TestCase):
    def test_sync_stops_does_not_connect_to_mongo(self):
        with patch('sys.argv',['cli','sync-stops']),patch('backend.cli.LTAClient') as client:
            with patch('backend.cli.sync_stops',return_value={'stops':3}) as sync:
                with patch('backend.cli.mongo_database') as mongo,patch('sys.stdout',new_callable=io.StringIO) as output:
                    cli.main()
        sync.assert_called_once_with(client.return_value,allow_large_change=False)
        mongo.assert_not_called()
        self.assertEqual(json.loads(output.getvalue()),{'stops':3})

    def test_diagnose_static_uses_no_mongo(self):
        with patch('sys.argv',['cli','diagnose-static']),patch('backend.cli.LTAClient') as client:
            with patch('backend.cli.diagnose_static',return_value={'databaseChanged':False}) as diagnose:
                with patch('backend.cli.mongo_database') as mongo,patch('sys.stdout',new_callable=io.StringIO) as output:
                    cli.main()
        diagnose.assert_called_once_with(client.return_value)
        mongo.assert_not_called()
        self.assertFalse(json.loads(output.getvalue())['databaseChanged'])

if __name__=='__main__':
    unittest.main()
