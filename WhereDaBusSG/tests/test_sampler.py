from datetime import datetime, timedelta, timezone
import json
import threading
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError

from backend import sampler


class StopAfterWait:
    def __init__(self):
        self.done = False
        self.delays = []

    def is_set(self):
        return self.done

    def wait(self, delay):
        self.delays.append(delay)
        self.done = True
        return True


class SamplerTests(unittest.TestCase):
    def setUp(self):
        self.environment = {'SAMPLE_STOPS': '01001, 01002,01001',
                            'SAMPLE_SERVICES': '15, 15A,15',
                            'MONGO_URI': 'test-only', 'LTA_ACCOUNT_KEY': 'test-secret'}
        self.config = sampler.configuration(self.environment)

    def test_configuration_preserves_zeroes_and_deduplicates(self):
        self.assertEqual(self.config.stops, ('01001', '01002'))
        self.assertEqual(self.config.services, frozenset(('15', '15A')))

    def test_bad_or_unbounded_stops_fail_before_provider_requests(self):
        for value in ('', '123', 'abcde', ','.join(f'{n:05}' for n in range(21))):
            with self.subTest(value=value), self.assertRaises(ValueError):
                sampler.configuration({**self.environment, 'SAMPLE_STOPS': value})

    def test_key_and_database_are_required(self):
        for key in ('LTA_ACCOUNT_KEY', 'MONGO_URI'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                sampler.configuration({**self.environment, key: ''})

    def test_invalid_service_filter_is_rejected(self):
        with self.assertRaises(ValueError):
            sampler.configuration({**self.environment, 'SAMPLE_SERVICES': 'bad-service'})

    def test_cycle_replays_then_polls_stops_with_one_stable_slot(self):
        calls = []
        now = datetime(2026, 11, 12, 7, 9, 58, tzinfo=timezone.utc)
        replay = Mock(side_effect=lambda db, limit: calls.append('replay') or 2)
        poll = Mock(side_effect=lambda db, lta, code, services, slot: calls.append(code) or 1)
        result = sampler.run_cycle('db', 'provider', self.config, threading.Event(), lambda: now, replay, poll)
        self.assertEqual(calls, ['replay', '01001', '01002'])
        self.assertEqual(result['replayed'], 2)
        self.assertEqual(result['projected'], 2)
        self.assertEqual(result['slot'], '2026-11-12T07:09:00+00:00')
        for call in poll.call_args_list:
            self.assertEqual(call.args[3:], (self.config.services, result['slot']))

    def test_failed_replay_and_stop_do_not_block_other_stops_or_leak_secrets(self):
        replay = Mock(side_effect=RuntimeError('secret-database'))
        poll = Mock(side_effect=[RuntimeError('secret-key'), 3])
        result = sampler.run_cycle('db', 'provider', self.config, threading.Event(), replay_fn=replay, poll_fn=poll)
        self.assertEqual(result['projected'], 3)
        self.assertEqual(result['failedStops'], [{'stop': '01001', 'errorType': 'RuntimeError'}])
        self.assertEqual(result['replayErrorType'], 'RuntimeError')
        self.assertNotIn('secret', json.dumps(result))

    def test_shutdown_stops_before_next_provider_request(self):
        event = threading.Event()
        def poll(*args):
            event.set()
            return 1
        result = sampler.run_cycle('db', 'provider', self.config, event, replay_fn=Mock(return_value=0), poll_fn=Mock(side_effect=poll))
        self.assertEqual(result['projected'], 1)
        replay = Mock()
        poll = Mock()
        sampler.run_cycle('db', 'provider', self.config, event, replay_fn=replay, poll_fn=poll)
        replay.assert_not_called()
        poll.assert_not_called()

    def test_provider_cooldown_is_shared_and_survives_later_cycles(self):
        now = datetime(2026, 11, 12, 7, 9, tzinfo=timezone.utc)
        error = HTTPError('https://example.test', 429, 'rate limited', {'Retry-After': '120'}, None)
        poll = Mock(side_effect=error)
        cooldowns = {}
        result = sampler.run_cycle('db', 'provider', self.config, threading.Event(),
                                   lambda: now, Mock(return_value=0), poll, cooldowns)
        self.assertEqual(poll.call_count, 1)
        self.assertEqual(result['deferredStops'], ['01002'])
        later = sampler.run_cycle('db', 'provider', self.config, threading.Event(),
                                  lambda: now + timedelta(seconds=60), Mock(return_value=0), poll, cooldowns)
        self.assertEqual(poll.call_count, 1)
        self.assertEqual(later['deferredStops'], ['01001', '01002'])
        recovered = Mock(return_value=1)
        result = sampler.run_cycle('db', 'provider', self.config, threading.Event(),
                                   lambda: now + timedelta(seconds=121), Mock(return_value=0), recovered, cooldowns)
        self.assertEqual(result['projected'], 2)

    def test_loop_reuses_the_same_cooldown_state(self):
        class StopAfterTwo(StopAfterWait):
            def wait(self, delay):
                self.delays.append(delay)
                self.done = len(self.delays) == 2
                return self.done
        event = StopAfterTwo()
        states = []
        def cycle(*args, cooldowns):
            states.append(cooldowns)
            cooldowns['test'] = True
            return {}
        sampler.run(self.config, event, lambda: (Mock(), Mock()), Mock(), cycle,
                    Mock(side_effect=[0, 1, 60, 61]), Mock())
        self.assertIs(states[0], states[1])

    def test_loop_has_bounded_provider_attempts_and_closes_client(self):
        event = StopAfterWait()
        client, database, provider_factory = Mock(), Mock(), Mock()
        cycle = Mock(return_value={'projected': 1})
        emit = Mock()
        sampler.run(self.config, event, lambda: (client, database), provider_factory,
                    cycle, Mock(side_effect=[10, 14]), emit)
        self.assertEqual(event.delays, [56])
        provider_factory.assert_called_once_with('test-secret', timeout=8, attempts=1)
        client.close.assert_called_once()
        self.assertEqual(emit.call_args.args[0]['status'], 'sampled')

    def test_overrun_waits_a_full_interval_instead_of_catching_up(self):
        event = StopAfterWait()
        client = Mock()
        sampler.run(self.config, event, lambda: (client, Mock()), Mock(),
                    Mock(return_value={}), Mock(side_effect=[10, 90]), Mock())
        self.assertEqual(event.delays, [60])
        client.close.assert_called_once()

    def test_unexpected_cycle_error_waits_and_logs_only_type(self):
        event = StopAfterWait()
        emit = Mock()
        sampler.run(self.config, event, lambda: (Mock(), Mock()), Mock(),
                    Mock(side_effect=RuntimeError('secret')), Mock(side_effect=[0, 1]), emit)
        self.assertEqual(event.delays, [59])
        self.assertEqual(emit.call_args.args[0], {'status': 'retrying', 'errorType': 'RuntimeError'})


if __name__ == '__main__':
    unittest.main()
