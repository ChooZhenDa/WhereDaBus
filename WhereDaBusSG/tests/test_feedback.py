from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import TestCase, skipUnless
from unittest.mock import Mock

try:
    from fastapi import HTTPException
    from backend.feedback import add_feedback_keys, current_eta, timing_key
except ImportError:
    READY = False
else:
    READY = True


@skipUnless(READY, 'Install application dependencies')
class FeedbackTimingTests(TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.meta = {'stopCode': '12345', 'serviceNo': '12', 'operator': 'SBST'}
        self.bus = {'eta': self.now + timedelta(minutes=5), 'originCode': '12300',
                    'destinationCode': '12399', 'visitNumber': 1, 'rank': 1}
        self.snapshot = {'status': 'available', 'freshUntil': self.now + timedelta(seconds=18),
                         'observations': [{'meta': self.meta, 'arrivals': [self.bus]}]}
        self.live = Mock()
        self.live.get.return_value = self.snapshot
        self.request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(live=self.live)))

    def test_utc_equivalence_and_rank_stability(self):
        eta_sgt = self.bus['eta'].astimezone(timezone(timedelta(hours=8)))
        self.assertEqual(timing_key(self.meta, self.bus),
                         timing_key(self.meta, {**self.bus, 'rank': 2, 'eta':eta_sgt}))

    def test_changed_time_visit_service_stop_and_terminal_are_separate(self):
        original = timing_key(self.meta, self.bus)
        for key, value in [('eta', self.bus['eta'] + timedelta(seconds=1)),
                           ('visitNumber', 2), ('destinationCode', '18001'), ('originCode', None)]:
            self.assertNotEqual(timing_key(self.meta, {**self.bus, key:value}), original)
        for key, value in [('stopCode', '90000'), ('operator', 'SMRT'), ('serviceNo', '11')]:
            self.assertNotEqual(timing_key({**self.meta, key:value}, self.bus), original)

    def test_key_appears_only_on_fresh_upcoming_arrival(self):
        past = {**self.bus, 'eta': self.now-timedelta(minutes=1)}
        self.snapshot['observations'][0]['arrivals'].append(past)
        public = add_feedback_keys(deepcopy(self.snapshot), self.now)
        live, departed = public['observations'][0]['arrivals']
        self.assertIn('feedbackKey', live)
        self.assertNotIn('feedbackKey', departed)
        self.assertEqual(self.bus['eta'], current_eta(self.request, '12345', live['feedbackKey']))
        self.snapshot['freshUntil'] = self.now-timedelta(seconds=2)
        expired = add_feedback_keys(deepcopy(self.snapshot), self.now)
        self.assertNotIn('feedbackKey', expired['observations'][0]['arrivals'][0])
        with self.assertRaises(HTTPException) as error:
            current_eta(self.request, '12345', live['feedbackKey'])
        self.assertEqual(error.exception.status_code, 409)

    def test_changed_time_and_wrong_stop_rejected(self):
        key = timing_key(self.meta, self.bus)
        with self.assertRaises(HTTPException):
            current_eta(self.request, '54321', key)
        self.bus['eta'] += timedelta(minutes=1)
        with self.assertRaises(HTTPException) as error:
            current_eta(self.request, '12345', key)
        self.assertEqual(error.exception.status_code, 409)
