import copy
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from backend.domain import normalize_arrivals,parse_time,validate_static,stop_code
from backend.analytics import previous_seven_days,morning_peak,half_hour_trend,crowded_stops
from backend.lta import LTAClient

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026,10,8,23,30,tzinfo=timezone.utc)

class DomainTests(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads((ROOT/'fixtures/arrival.json').read_text())
        self.static = json.loads((ROOT/'fixtures/static.json').read_text())

    def doc(self):
        return normalize_arrivals(self.payload,NOW,'poll-1')[0]

    def validate(self):
        validate_static(*(self.static[k] for k in ('BusStops','BusServices','BusRoutes')))

    def test_leading_zero_is_preserved(self):
        self.assertEqual(stop_code('01001'),'01001')
        with self.assertRaises(ValueError): stop_code(1001)

    def test_utc_conversion(self):
        self.assertEqual(parse_time('2026-10-09T07:35:00+08:00'),NOW+timedelta(minutes=5))

    def test_naive_eta_is_unknown(self):
        self.assertIsNone(parse_time('2026-10-09T07:35:00'))

    def test_invalid_eta_is_unknown(self):
        self.assertIsNone(parse_time('bad'))

    def test_eta_seconds_and_type(self):
        bus = self.doc()['arrivals'][0]
        self.assertEqual(bus['etaSeconds'],300)
        self.assertEqual(bus['vehicleType'],'DD')

    def test_missing_bus_slots_remain_missing(self):
        self.assertEqual([x['rank'] for x in self.doc()['arrivals']],[1,2,3])
        self.assertIsNone(self.doc()['arrivals'][1]['loadScore'])

    def test_unknown_load_is_not_empty_bus(self):
        self.payload['Services'][0]['NextBus']['Load'] = 'NEW-CODE'
        self.assertIsNone(self.doc()['arrivals'][0]['loadScore'])

    def test_schedule_only_is_retained_for_analysis_filter(self):
        self.payload['Services'][0]['NextBus']['Monitored'] = 0
        self.assertEqual(self.doc()['arrivals'][0]['monitored'],0)

    def test_zero_location_is_unknown(self):
        self.payload['Services'][0]['NextBus'].update(Latitude='0.0',Longitude='0.0')
        self.assertIsNone(self.doc()['arrivals'][0]['location'])

    def test_nan_location_is_unknown(self):
        self.payload['Services'][0]['NextBus']['Latitude'] = 'NaN'
        self.assertIsNone(self.doc()['arrivals'][0]['location'])

    def test_negative_eta_not_clamped(self):
        self.payload['Services'][0]['NextBus']['EstimatedArrival'] = (NOW-timedelta(seconds=1)).isoformat()
        self.assertEqual(self.doc()['arrivals'][0]['etaSeconds'],-1)

    def test_selected_service_filter(self):
        self.assertEqual(normalize_arrivals(self.payload,NOW,'p',{'OTHER'}),[])

    def test_id_stable_on_replay_different_on_new_poll(self):
        first = self.doc()['logicalKey']
        self.assertEqual(first,self.doc()['logicalKey'])
        self.assertNotEqual(first,normalize_arrivals(self.payload,NOW,'poll-2')[0]['logicalKey'])

    def test_receipt_time_requires_timezone(self):
        with self.assertRaises(ValueError): normalize_arrivals(self.payload,NOW.replace(tzinfo=None),'p')

    def test_empty_service_response(self):
        self.payload['Services'] = []
        self.assertEqual(normalize_arrivals(self.payload,NOW,'p'),[])

    def test_duplicate_service_rejected(self):
        self.payload['Services'] *= 2
        with self.assertRaises(ValueError): self.doc()

    def test_loop_stop_allowed(self):
        self.validate()

    def test_missing_route_occurrence_rejected(self):
        del self.static['BusRoutes'][1]
        with self.assertRaises(ValueError): self.validate()

    def test_decreasing_distance_rejected(self):
        self.static['BusRoutes'][2]['Distance'] = 0.5
        with self.assertRaises(ValueError): self.validate()

    def test_non_finite_distance_rejected(self):
        self.static['BusRoutes'][2]['Distance'] = 'NaN'
        with self.assertRaises(ValueError): self.validate()

    def test_missing_fk_rejected(self):
        self.static['BusRoutes'][0]['BusStopCode'] = '99999'
        with self.assertRaises(ValueError): self.validate()

    def test_empty_snapshot_rejected(self):
        self.static['BusStops'] = []
        with self.assertRaises(ValueError): self.validate()

    def test_inconsistent_operator_rejected(self):
        self.static['BusRoutes'][0]['Operator'] = 'OTHER'
        with self.assertRaises(ValueError): self.validate()

    def test_singapore_midnight_window(self):
        start,end = previous_seven_days(datetime(2026,10,10,12,tzinfo=timezone.utc))
        self.assertEqual(start,datetime(2026,10,2,16,tzinfo=timezone.utc))
        self.assertEqual(end,datetime(2026,10,9,16,tzinfo=timezone.utc))

    def test_pagination_fetches_all_pages(self):
        client = LTAClient('test')
        with patch.object(client,'get',side_effect=[{'value':[{}]*500},{'value':[{}]*2}]) as get:
            self.assertEqual(len(client.all_rows('BusStops')),502)
            self.assertEqual(get.call_args.args[1],{'$skip':500})

    def test_pagination_malformed_page_rejected(self):
        client = LTAClient('test')
        with patch.object(client,'get',return_value={'error':'unavailable'}):
            with self.assertRaises(ValueError): client.all_rows('BusStops')

    def test_range_is_bounded(self):
        with self.assertRaises(ValueError): morning_peak('1',NOW,NOW+timedelta(days=32))

if __name__ == '__main__': unittest.main()
