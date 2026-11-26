"""Behavior checks for on-demand arrivals, with deterministic cache/clock doubles.
MongoDB atomic lease semantics are separately covered by integration.py.
"""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from email.message import Message
from pathlib import Path
import json
import unittest
from urllib.error import HTTPError

from backend.live import LiveArrivals,LiveUnavailable,provider_retry_seconds


class MemoryCache:
    def __init__(self):
        self.rows = {}
        self.busy = False
        self.reject_completion = False

    def get(self,code):
        return deepcopy(self.rows.get(code))

    def acquire(self,code,token,now):
        if self.busy:
            return False
        self.rows.setdefault(code,{'_id':code})['owner'] = token
        return True

    def complete(self,code,token,doc,now):
        if self.reject_completion:
            return False
        if self.rows[code]['owner'] != token:
            return False
        self.rows[code] = deepcopy(doc)
        return True

    def fail(self,code,token,now,delay):
        self.rows[code]['nextAttemptAt'] = now+timedelta(seconds=delay)
        self.rows[code]['expiresAt'] = now+timedelta(seconds=delay)


class Client:
    def __init__(self,payload):
        self.payload = payload
        self.calls = []
        self.error = None

    def get(self,endpoint,params):
        self.calls.append((endpoint,params))
        if self.error:
            raise self.error
        result = deepcopy(self.payload)
        if result is not None:
            result['BusStopCode'] = params['BusStopCode']
        return result


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026,10,8,23,30,tzinfo=timezone.utc)
        payload = json.loads((Path(__file__).resolve().parents[1]/'fixtures/arrival.json').read_text())
        self.client = Client(payload)
        self.cache = MemoryCache()
        self.live = LiveArrivals(self.cache,self.client,now=lambda:self.now)

    def advance(self,seconds):
        self.now += timedelta(seconds=seconds)

    def test_any_stop_is_forwarded_without_sampler_allowlist(self):
        result = self.live.get('83139')
        self.assertEqual(result['stopCode'],'83139')
        self.assertEqual(self.client.calls,[('v3/BusArrival',{'BusStopCode':'83139'})])

    def test_leading_zero_survives(self):
        self.live.get('01012')
        self.assertEqual(self.client.calls[0][1]['BusStopCode'],'01012')

    def test_invalid_code_does_not_call_provider(self):
        with self.assertRaises(ValueError): self.live.get('1012')
        self.assertEqual(self.client.calls,[])

    def test_fresh_cache_reuses_snapshot_but_updates_countdown(self):
        self.assertEqual(self.live.get('01001')['observations'][0]['arrivals'][0]['etaSecondsNow'],300)
        self.advance(10)
        result = self.live.get('01001')
        self.assertEqual(result['observations'][0]['arrivals'][0]['etaSecondsNow'],290)
        self.assertEqual(len(self.client.calls),1)
        self.assertNotIn('etaSecondsNow',self.cache.rows['01001']['observations'][0]['arrivals'][0])

    def test_twenty_second_boundary_fetches_new_response(self):
        self.live.get('01001')
        self.advance(20)
        self.live.get('01001')
        self.assertEqual(len(self.client.calls),2)

    def test_different_stops_have_independent_cache(self):
        self.live.get('01001');self.live.get('01002');self.live.get('01001')
        self.assertEqual(len(self.client.calls),2)

    def test_live_returns_all_services(self):
        second = deepcopy(self.client.payload['Services'][0])
        second['ServiceNo'] = 'OTHER'
        self.client.payload['Services'].append(second)
        result = self.live.get('01001')
        self.assertEqual([x['meta']['serviceNo'] for x in result['observations']],['DEMO1','OTHER'])

    def test_empty_response_is_cached_as_no_data(self):
        self.client.payload = None
        self.assertEqual(self.live.get('01001')['status'],'no_data')
        self.live.get('01001')
        self.assertEqual(len(self.client.calls),1)

    def test_failure_serves_labeled_stale_data(self):
        self.live.get('01001');self.advance(21)
        self.client.error = TimeoutError('do not expose upstream error text')
        result = self.live.get('01001')
        self.assertEqual(result['status'],'stale')
        self.assertEqual(result['ageSeconds'],21)
        self.assertNotIn('upstream error text',str(result))

    def test_expired_payload_never_returned_even_when_ttl_extended(self):
        self.live.get('01001');self.advance(120)
        self.client.error = TimeoutError()
        with self.assertRaises(LiveUnavailable): self.live.get('01001')
        self.assertGreater(self.cache.rows['01001']['expiresAt'],self.now)

    def test_missing_key_is_actionable(self):
        self.live.client = None
        with self.assertRaises(LiveUnavailable) as cm: self.live.get('01001')
        self.assertEqual(cm.exception.reason,'lta_key_not_configured')

    def test_concurrent_refresh_reuses_recent_stale_snapshot(self):
        self.live.get('01001');self.advance(21)
        self.cache.busy = True
        result = self.live.get('01001')
        self.assertEqual(result['reason'],'refresh_in_progress')
        self.assertEqual(len(self.client.calls),1)

    def test_concurrent_refresh_without_snapshot_returns_retry(self):
        self.cache.busy = True
        with self.assertRaises(LiveUnavailable) as cm: self.live.get('01001')
        self.assertEqual(cm.exception.retry_after,2)
        self.assertEqual(self.client.calls,[])

    def test_cooldown_prevents_repeated_provider_requests(self):
        self.client.error = TimeoutError()
        with self.assertRaises(LiveUnavailable): self.live.get('01001')
        self.advance(1)
        with self.assertRaises(LiveUnavailable) as cm: self.live.get('01001')
        self.assertEqual(cm.exception.reason,'provider_cooldown')
        self.assertEqual(len(self.client.calls),1)

    def test_provider_retry_after_is_honored(self):
        headers = Message();headers['Retry-After'] = '90'
        self.client.error = HTTPError('https://example.test',429,'throttled',headers,None)
        with self.assertRaises(LiveUnavailable) as cm: self.live.get('01001')
        self.assertEqual(cm.exception.retry_after,90)
        self.advance(60)
        with self.assertRaises(LiveUnavailable): self.live.get('01001')
        self.assertEqual(len(self.client.calls),1)

    def test_malformed_retry_after_uses_safe_default(self):
        headers = Message();headers['Retry-After'] = 'bad-date'
        error = HTTPError('https://example.test',429,'throttled',headers,None)
        self.assertEqual(provider_retry_seconds(error,self.now),20)

    def test_lost_lease_does_not_return_uncommitted_snapshot(self):
        self.cache.reject_completion = True
        with self.assertRaises(LiveUnavailable): self.live.get('01001')

if __name__ == '__main__': unittest.main()
