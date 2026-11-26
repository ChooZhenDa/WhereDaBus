"""On-demand arrivals for any five-digit stop code, separate from history.

The shared cache coordinates refreshes across API instances. On-demand traffic
never enters the fixed-cadence time series and never applies SAMPLE_SERVICES.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError
from uuid import uuid4
import math

from .domain import normalize_arrivals, stop_code

FRESH_SECONDS = 20
STALE_SECONDS = 120
LEASE_SECONDS = 30


class LiveUnavailable(Exception):
    def __init__(self, reason, retry_after=20):
        self.reason = reason
        self.retry_after = retry_after
        super().__init__(reason)


def utc_now():
    return datetime.now(timezone.utc)


def provider_retry_seconds(error, now):
    """Honor provider Retry-After without retrying inside the HTTP request."""
    if not isinstance(error, HTTPError) or not error.headers:
        return FRESH_SECONDS
    raw = error.headers.get('Retry-After')
    if not raw:
        return FRESH_SECONDS
    try:
        delay = float(raw)
    except (TypeError, ValueError):
        try:
            delay = (parsedate_to_datetime(raw)-now).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return FRESH_SECONDS
    if not math.isfinite(delay):
        return FRESH_SECONDS
    if delay > 365*86400:
        return 365*86400
    return max(FRESH_SECONDS, math.ceil(delay))


def public_snapshot(doc, now, stale=False, reason=None):
    observations = deepcopy(doc['observations'])
    for obs in observations:
        for bus in obs['arrivals']:
            bus['etaSecondsNow'] = (bus['eta']-now).total_seconds() if bus['eta'] else None
    status = 'stale' if stale else ('available' if observations else 'no_data')
    result = {'status':status, 'stopCode':doc['_id'],
              'capturedAt':doc['capturedAt'], 'freshUntil':doc['freshUntil'],
              'ageSeconds':max(0,(now-doc['capturedAt']).total_seconds()),
              'refreshAfterSeconds':FRESH_SECONDS, 'observations':observations}
    if reason:
        result['reason'] = reason
    return result


class LiveArrivals:
    """Cache-aside workflow. Inject cache/client/clock for deterministic tests."""
    def __init__(self, cache, client, now=utc_now):
        self.cache, self.client, self.now = cache, client, now

    def _fallback(self, code, reason, retry_after=20):
        doc, now = self.cache.get(code), self.now()
        # TTL can be extended for leases/cooldowns, so use actual payload age.
        if (doc and 'capturedAt' in doc
                and now-doc['capturedAt'] < timedelta(seconds=STALE_SECONDS)):
            fresh = doc.get('freshUntil',now) > now
            result = public_snapshot(doc,now,stale=not fresh,reason=None if fresh else reason)
            if not fresh:
                result['refreshAfterSeconds'] = retry_after
            return result
        raise LiveUnavailable(reason,retry_after)

    def get(self, code):
        code = stop_code(code)
        now, doc = self.now(), self.cache.get(code)
        if doc and doc.get('freshUntil',now) > now:
            return public_snapshot(doc,now)
        if self.client is None:
            return self._fallback(code,'lta_key_not_configured')
        if doc and doc.get('nextAttemptAt',now) > now:
            delay = max(1,math.ceil((doc['nextAttemptAt']-now).total_seconds()))
            return self._fallback(code,'provider_cooldown',delay)
        token = uuid4().hex
        if not self.cache.acquire(code,token,now):
            return self._fallback(code,'refresh_in_progress',2)
        try:
            payload = self.client.get('v3/BusArrival',{'BusStopCode':code})
            if not payload:
                payload = {'BusStopCode':code,'Services':[]}
            if payload.get('BusStopCode') != code:
                raise ValueError('Unexpected stop in provider response')
            captured = self.now()
            observations = normalize_arrivals(payload,captured,'live-'+token)
        except Exception as error:
            now = self.now()
            delay = provider_retry_seconds(error,now)
            self.cache.fail(code,token,now,delay)
            return self._fallback(code,'provider_unavailable',delay)
        doc = {'_id':code,'capturedAt':captured,
               'freshUntil':captured+timedelta(seconds=FRESH_SECONDS),
               'expiresAt':captured+timedelta(seconds=STALE_SECONDS),
               'observations':observations}
        if not self.cache.complete(code,token,doc,self.now()):
            return self._fallback(code,'refresh_in_progress',2)
        return public_snapshot(doc,self.now())


class MongoLiveCache:
    """Regular Mongo collection: unique _id and conditional writes fence leases."""
    def __init__(self, db):
        self.collection = db.arrival_live

    def get(self, code):
        return self.collection.find_one({'_id':code})

    def acquire(self, code, token, now):
        from pymongo import ReturnDocument
        from pymongo.errors import DuplicateKeyError
        available = {'_id':code,'$and':[
            {'$or':[{'leaseUntil':{'$lte':now}},{'leaseUntil':{'$exists':False}}]},
            {'$or':[{'nextAttemptAt':{'$lte':now}},{'nextAttemptAt':{'$exists':False}}]},
            {'$or':[{'freshUntil':{'$lte':now}},{'freshUntil':{'$exists':False}}]},
        ]}
        try:
            result = self.collection.find_one_and_update(available,
                {'$set':{'leaseOwner':token,'leaseUntil':now+timedelta(seconds=LEASE_SECONDS)},
                 '$max':{'expiresAt':now+timedelta(seconds=LEASE_SECONDS)}},
                upsert=True,return_document=ReturnDocument.AFTER)
            return result is not None
        except DuplicateKeyError:
            return False

    def complete(self, code, token, doc, now):
        result = self.collection.update_one(
            {'_id':code,'leaseOwner':token,'leaseUntil':{'$gt':now}},
            {'$set':{k:v for k,v in doc.items() if k!='_id'},
             '$unset':{'leaseOwner':'','leaseUntil':'','nextAttemptAt':''}})
        return result.matched_count == 1

    def fail(self, code, token, now, delay):
        self.collection.update_one({'_id':code,'leaseOwner':token},
            {'$set':{'nextAttemptAt':now+timedelta(seconds=delay)},
             '$max':{'expiresAt':now+timedelta(seconds=delay)},
             '$unset':{'leaseOwner':'','leaseUntil':''}})
