"""Date-filter HTTP contracts using a mocked Mongo boundary, not real-engine tests."""
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from tests import test_gui_api as gui_tests
API_AVAILABLE = gui_tests.API_AVAILABLE
import unittest
if API_AVAILABLE:
    from backend import api


@unittest.skipUnless(API_AVAILABLE, 'Install application and test dependencies')
class AnalyticsAPITests(unittest.TestCase):
    setUp = gui_tests.GUIAPITests.setUp
    tearDown = gui_tests.GUIAPITests.tearDown
    def test_selected_dates_reach_both_mongo_pipelines(self):
        for kind in ['peak','trend']:
            db=Mock();db.arrival_observations.aggregate.return_value=[]
            with patch.object(api,'require_mongo',return_value=db):
                result=self.client.get('/analytics/'+kind,params={
                    'service':'15','stop':'22599','date_from':'2026-11-10','date_to':'2026-11-12'})
            self.assertEqual(result.status_code,200)
            match=db.arrival_observations.aggregate.call_args.args[0][0]['$match']
            self.assertEqual(match['meta.stopCode'],'22599')
            self.assertEqual(match['observedAt']['$gte'],datetime(2026,11,9,16,tzinfo=timezone.utc))
            self.assertEqual(match['observedAt']['$lt'],datetime(2026,11,12,16,tzinfo=timezone.utc))
            self.assertEqual(db.arrival_observations.aggregate.call_args.kwargs['maxTimeMS'],10000)

    def test_bad_date_selections_fail_before_mongo_access(self):
        for query in ['date_from=2026-11-10', 'date_to=2026-11-10',
                      'date_from=2026-11-12&date_to=2026-11-10',
                      'date_from=2026-01-01&date_to=2026-03-01',
                      'date_from=3030-01-01&date_to=3030-01-01',
                      'date_from=2026-02-30&date_to=2026-03-01']:
            with patch.object(api,'require_mongo') as db:
                response=self.client.get('/analytics/trend?service=15&stop=22599&'+query)
            self.assertEqual(response.status_code,422)
            db.assert_not_called()

    def test_empty_and_zero_valid_results_are_not_fabricated(self):
        db=Mock();db.arrival_observations.aggregate.return_value=[]
        with patch.object(api,'require_mongo',return_value=db):
            self.assertEqual(self.client.get('/analytics/trend?service=15&stop=22599').json(),[])
        rows=[{'_id':{'operator':'TEST'},'observations':10,'validSamples':0,'meanCrowdingOrdinal':None}]
        db.arrival_observations.aggregate.return_value=rows
        with patch.object(api,'require_mongo',return_value=db):
            result=self.client.get('/analytics/peak?service=15&stop=22599').json()
        self.assertEqual(result['rows'],rows)
