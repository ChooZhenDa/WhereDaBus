"""Current route predictions must identify the selected vehicle visit and onward leg."""
from datetime import datetime, timedelta, timezone
from unittest import TestCase, skipUnless
from unittest.mock import Mock

try:
    from backend.route_comparison import compare_routes, next_bus, serves_journey
    from backend.live import LiveUnavailable
    from pymongo.errors import PyMongoError
except ModuleNotFoundError:
    AVAILABLE = False
else:
    AVAILABLE = True


def pattern(direction, stops, complete=True):
    return {'direction': direction, 'stops': stops.split(), 'complete': complete}


def route(number='86', direction=1, origin='B', board=2, destination='D', alight=4):
    return {'service_no': number, 'operator': 'OP', 'direction': direction,
            'origin_code': origin, 'destination_code': destination,
            'origin_sequence': board, 'destination_sequence': alight}


def prediction(start='A', end='E', visit=1, minutes=3, monitored=1):
    return {'originCode': start, 'destinationCode': end, 'visitNumber': visit,
            'eta': datetime.now(timezone.utc) + timedelta(minutes=minutes),
            'monitored': monitored, 'load': 'SEA'}


@skipUnless(AVAILABLE, 'Install application dependencies')
class ComparisonTests(TestCase):
    def test_short_working_and_direction(self):
        patterns = [pattern(1, 'A B C D E'), pattern(2, 'E D C B A')]
        self.assertTrue(serves_journey(route(), patterns, prediction()))
        self.assertTrue(serves_journey(route(), patterns, prediction(end='D')))
        self.assertFalse(serves_journey(route(), patterns, prediction(end='C')))
        self.assertFalse(serves_journey(route(), patterns, prediction(start='E', end='A')))
        self.assertFalse(serves_journey(route(), patterns, prediction(end=None)))
        self.assertFalse(serves_journey(route(), patterns, prediction(start=None, visit=2)))

    def test_loop_requires_selected_boarding_occurrence(self):
        patterns = [pattern(1, 'A B C B D A')]
        chosen = route(board=4, alight=5)
        self.assertTrue(serves_journey(chosen, patterns, prediction(end='A', visit=2)))
        self.assertFalse(serves_journey(chosen, patterns, prediction(end='A', visit=1)))
        self.assertFalse(serves_journey(chosen, patterns, prediction(end='A', visit=None)))
        self.assertFalse(serves_journey(chosen, patterns, prediction(start=None, end='A', visit=1)))
        self.assertTrue(serves_journey(chosen, patterns, prediction(start='C', end='A', visit=1)))

    def test_ambiguous_direction_and_missing_topology(self):
        same_endpoints = [pattern(1, 'A B C D E'), pattern(2, 'A B X D E')]
        self.assertFalse(serves_journey(route(), same_endpoints, prediction()))
        same_endpoints[1] = pattern(2, 'A B D', complete=False)
        self.assertFalse(serves_journey(route(), same_endpoints, prediction()))

    def test_gap_before_boarding_on_proven_full_direction(self):
        leg = {**route(board=3, alight=5), 'segment_coverage': 'no_detected_gaps'}
        onward = {**pattern(1, 'A B C D E', False), 'sequences': [1, 3, 4, 5, 6],
                  'origin': 'A', 'terminal': 'E'}
        reverse = {**pattern(2, 'E D C A', False), 'sequences': [1, 2, 3, 5],
                   'origin': 'E', 'terminal': 'A'}
        self.assertTrue(serves_journey(leg, [onward, reverse], prediction()))
        self.assertFalse(serves_journey(leg, [onward, reverse], prediction(visit=2)))
        self.assertFalse(serves_journey(leg, [onward, reverse], prediction(end='C')))
        self.assertFalse(serves_journey(leg, [onward, reverse], prediction(start='E', end='A')))
        self.assertFalse(serves_journey({**leg, 'segment_coverage': 'partial'},
                                        [onward, reverse], prediction()))

    def test_skip_departed_and_short_buses_and_hide_unmonitored_load(self):
        now = datetime.now(timezone.utc)
        selected = prediction(minutes=5, monitored=0)
        buses = [prediction(minutes=-1), prediction(end='C', minutes=1), selected]
        row = next_bus(route(), [pattern(1, 'A B C D E')], buses, 'available', now)
        self.assertEqual(row, {'eta': selected['eta'], 'load': None,
                               'monitored': 0, 'status': 'available'})

    def test_one_live_fetch_one_history_query_and_earliest_boarding_first(self):
        rows = [{**route(n), 'patterns': [pattern(1, 'A B C D E')]} for n in ['86', '136']]
        live, db = Mock(), Mock()
        live.get.return_value = {'status': 'available', 'observations': [
            {'meta': {'serviceNo': n, 'operator': 'OP', 'stopCode': 'B'},
             'arrivals': [prediction(minutes=m)]} for n, m in [('86', 6), ('136', 2)]]}
        db.arrival_observations.aggregate.return_value = [
            {'_id': {'serviceNo': n, 'operator': 'OP'}, 'validSamples': 30,
             'daysWithValidSamples': days, 'observations': 40, 'seatsAvailablePct': 70}
            for n, days in [('86', 2), ('136', 3)]]
        result = compare_routes('B', 'D', Mock(return_value=rows), live, db)
        live.get.assert_called_once_with('B')
        db.arrival_observations.aggregate.assert_called_once()
        criteria = db.arrival_observations.aggregate.call_args.args[0][0]['$match']
        self.assertEqual(criteria['meta.stopCode'], 'B')
        self.assertEqual(criteria['meta.serviceNo'], {'$in': ['136', '86']})
        earlier, later = result['routes']
        self.assertEqual((earlier['service_no'], later['service_no']), ('136', '86'))
        self.assertEqual(earlier['history']['seatsAvailablePct'], 70)
        self.assertEqual(later['history']['status'], 'limited')
        self.assertIsNone(later['history']['seatsAvailablePct'])
        self.assertNotIn('patterns', earlier)

    def test_stale_missing_and_expired_snapshot_leave_static_routes(self):
        sql = Mock(return_value=[{**route(), 'patterns': [pattern(1, 'A B C D E')]}])
        live, db = Mock(), Mock()
        db.arrival_observations.aggregate.side_effect = PyMongoError('offline')
        for snapshot in [{'status': 'stale'}, {'status': 'available',
                          'freshUntil': datetime.now(timezone.utc) - timedelta(seconds=1)}]:
            live.get.return_value = snapshot
            result = compare_routes('B', 'D', sql, live, db)['routes'][0]
            self.assertEqual(result['live']['status'], 'stale')
            self.assertIsNone(result['live']['eta'])
            self.assertEqual(result['history']['status'], 'unavailable')
        live.get.side_effect = LiveUnavailable('provider_unavailable')
        self.assertEqual(compare_routes('B', 'D', sql, live, None)['routes'][0]['live']['status'],
                         'unavailable')
        compare_routes('B', 'D', Mock(return_value=[]), live, None)
        self.assertEqual(live.get.call_count, 3)
