import unittest
import contextlib
import io
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from backend.evidence import main, measure, mongo_crud, selected_window, sql_crud, timing_summary
class EvidenceTests(unittest.TestCase):
    def test_percentile_uses_nearest_rank(self):
        result=timing_summary(list(range(1,21)))
        self.assertEqual(result['medianMs'],10.5)
        self.assertEqual(result['p95MsNearestRank'],19)
    def test_bad_timings_rejected(self):
        for values in ([],[-1],[float('nan')],[float('inf')]):
            with self.assertRaises(ValueError): timing_summary(values)
    def test_measure_excludes_warmups_and_consumes_results(self):
        calls=[]
        def op():
            calls.append(1)
            return [1,2] if len(calls)<4 else [1]
        result=measure(op,repetitions=5,warmups=2)
        self.assertEqual(len(calls),7)
        self.assertEqual(result['repetitions'],5)
        self.assertEqual(result['resultCounts'],[2,1,1,1,1])
        self.assertFalse(result['resultCountStable'])
    def test_empty_history_never_invents_cohort(self):
        self.assertIsNone(selected_window(None))
    def test_window_is_anchored_to_actual_observation(self):
        now=datetime.now(timezone.utc)-timedelta(minutes=5)
        value=selected_window({'observedAt':now,'meta':{'serviceNo':'61','stopCode':'22599'}})
        self.assertEqual(value['start'],now-timedelta(hours=48))
        self.assertEqual(value['endExclusive'],now+timedelta(seconds=1))
        self.assertEqual(value['stop'],'22599')
    def test_future_clock_is_rejected(self):
        with self.assertRaises(ValueError):
            selected_window({'observedAt':datetime.now(timezone.utc)+timedelta(days=1),'meta':{}})
    def test_mongo_failure_cleans_only_own_labelled_record(self):
        db=MagicMock(); db.name='wheredabus'
        collection=db.client.__getitem__.return_value.__getitem__.return_value
        collection.find_one.return_value={'load':'SEA'}
        collection.update_one.side_effect=RuntimeError('interrupted')
        with self.assertRaises(RuntimeError): mongo_crud(db)
        query=collection.delete_one.call_args.args[0]
        self.assertEqual(query['source'],'SYNTHETIC CRUD PROOF')
        self.assertEqual(len(query['_id']),36)
        collection.delete_many.assert_not_called()
        db.arrival_observations.update_one.assert_not_called()
    def test_sql_failure_rolls_back(self):
        connection=MagicMock()
        def execute(statement,*args):
            if statement.startswith('UPDATE transport.users'): raise RuntimeError('interrupted')
            cursor=MagicMock()
            cursor.fetchone.return_value=('61',1) if statement.startswith('SELECT service_no') else ('SYNTHETIC CRUD PROOF',)
            return cursor
        connection.execute.side_effect=execute
        with patch('backend.evidence.sql_connection') as factory:
            factory.return_value.__enter__.return_value=connection
            with self.assertRaises(RuntimeError): sql_crud()
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()
    def test_failures_are_redacted_and_not_success(self):
        output=io.StringIO()
        with patch('backend.evidence.sql_audit',side_effect=RuntimeError('private-password-marker')), patch('backend.evidence.mongo_database',side_effect=RuntimeError('private-password-marker')), contextlib.redirect_stdout(output):
            code=main(['audit'])
        self.assertEqual(code,1)
        self.assertNotIn('private-password-marker',output.getvalue())
        self.assertEqual(json.loads(output.getvalue())['status'],'failed')
if __name__=='__main__': unittest.main()

