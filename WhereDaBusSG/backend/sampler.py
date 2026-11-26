"""Optional ongoing historical sampling for the analytics screens.

The first cycle starts immediately. A normal cycle runs once per minute. If
work overruns, wait a full minute before starting again; missed slots are never
replayed as a burst of provider requests. Live GUI requests remain separate.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
import signal
import threading
import time
from urllib.error import HTTPError

from .domain import service_no, stop_code
from .lta import LTAClient
from .live import provider_retry_seconds
from .storage import mongo_database, poll_stop, replay


INTERVAL_SECONDS = 60


@dataclass(frozen=True)
class SamplerConfig:
    stops: tuple
    services: frozenset
    account_key: str


def configuration(environment=None):
    environment = os.environ if environment is None else environment
    codes = sorted(set(item.strip() for item in environment.get('SAMPLE_STOPS', '').split(',') if item.strip()))
    if not 1 <= len(codes) <= 20:
        raise ValueError('Set SAMPLE_STOPS to 1–20 comma-separated five-digit stop codes.')
    try:
        for code in codes:
            stop_code(code)
    except ValueError:
        raise ValueError('Every SAMPLE_STOPS entry must contain exactly five digits.') from None
    services = frozenset(item.strip() for item in environment.get('SAMPLE_SERVICES', '').split(',') if item.strip())
    try:
        for service in services:
            service_no(service)
    except ValueError:
        raise ValueError('SAMPLE_SERVICES entries must contain 1–12 letters or digits.') from None
    key = environment.get('LTA_ACCOUNT_KEY', '').strip()
    if not key:
        raise ValueError('Set LTA_ACCOUNT_KEY before starting the sampler.')
    if not environment.get('MONGO_URI', '').strip():
        raise ValueError('Set MONGO_URI before starting the sampler.')
    return SamplerConfig(tuple(codes), services, key)


def utc_now():
    return datetime.now(timezone.utc)


def run_cycle(database, provider, config, stopped, now=None, replay_fn=None, poll_fn=None, cooldowns=None):
    now = now or utc_now
    replay_fn = replay_fn or replay
    poll_fn = poll_fn or poll_stop
    cooldowns = {} if cooldowns is None else cooldowns
    slot = now().astimezone(timezone.utc).replace(second=0, microsecond=0).isoformat()
    result = {'slot': slot, 'replayed': 0, 'projected': 0, 'failedStops': [], 'deferredStops': []}
    if stopped.is_set():
        return result
    try:
        result['replayed'] = replay_fn(database, limit=100)
    except Exception as error:
        result['replayErrorType'] = type(error).__name__
    for code in config.stops:
        if stopped.is_set():
            break
        current = now()
        if any(cooldowns.get(key, current) > current for key in ('*', code)):
            result['deferredStops'].append(code)
            continue
        try:
            result['projected'] += poll_fn(database, provider, code, config.services, slot)
        except Exception as error:
            result['failedStops'].append({'stop': code, 'errorType': type(error).__name__})
            # Rate limits/service unavailability can apply to the whole account.
            # Keep the supplied delay across cycles and avoid hitting each stop
            # in turn while the provider has explicitly requested a pause.
            current = now()
            key = '*' if isinstance(error, HTTPError) and error.code in (429, 503) else code
            cooldowns[key] = current + timedelta(seconds=provider_retry_seconds(error, current))
    return result


def run(config, stopped, factory=None, provider_factory=None, cycle=None, monotonic=None, emit=None):
    factory = factory or mongo_database
    provider_factory = provider_factory or LTAClient
    cycle = cycle or run_cycle
    monotonic = monotonic or time.monotonic
    emit = emit or (lambda value: print(json.dumps(value), flush=True))
    # One request per stop/cycle. Retries happen in a later cycle, so an outage
    # does not multiply traffic or delay shutdown with exponential sleeps.
    provider = provider_factory(config.account_key, timeout=8, attempts=1)
    client, database = factory()
    cooldowns = {}
    try:
        while not stopped.is_set():
            started = monotonic()
            try:
                emit({'status': 'sampled', **cycle(database, provider, config, stopped, cooldowns=cooldowns)})
            except Exception as error:
                emit({'status': 'retrying', 'errorType': type(error).__name__})
            elapsed = max(0, monotonic() - started)
            delay = INTERVAL_SECONDS - elapsed if elapsed < INTERVAL_SECONDS else INTERVAL_SECONDS
            stopped.wait(delay)
    finally:
        client.close()


def main():
    try:
        config = configuration()
    except ValueError as error:
        print(json.dumps({'status': 'failed', 'message': str(error)}), flush=True)
        return 1
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda _signum, _frame: stopped.set())
    print(json.dumps({'status': 'starting', 'stops': list(config.stops),
                      'services': sorted(config.services), 'intervalSeconds': INTERVAL_SECONDS}), flush=True)
    try:
        run(config, stopped)
    except Exception as error:
        print(json.dumps({'status': 'failed', 'errorType': type(error).__name__,
                          'message': 'Sampler could not start. Check the MongoDB connection and server configuration.'}), flush=True)
        return 1
    print(json.dumps({'status': 'stopped'}), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
