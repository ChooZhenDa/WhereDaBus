"""Bounded HTTPS client with pagination and retry handling using the stdlib."""
import json
import random
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = 'https://datamall2.mytransport.sg/ltaodataservice/'

class LTAClient:
    def __init__(self, account_key, timeout=20, attempts=4):
        if not account_key:
            raise ValueError('Set LTA_ACCOUNT_KEY')
        self.account_key, self.timeout, self.attempts = account_key, timeout, attempts

    def get(self, endpoint, params=None):
        if endpoint not in {'BusStops','BusServices','BusRoutes','v3/BusArrival'}:
            raise ValueError('Unrecognized LTA endpoint')
        url = BASE+endpoint+('?' + urlencode(params) if params else '')
        for attempt in range(self.attempts):
            delay = min(2**attempt + random.random(),30)
            try:
                req = Request(url,headers={'AccountKey':self.account_key,'Accept':'application/json'})
                with urlopen(req,timeout=self.timeout) as response:
                    # Bound individual responses to avoid unbounded memory use.
                    body = response.read(8_000_001)
                if len(body) > 8_000_000:
                    raise ValueError('LTA response exceeds configured limit')
                if not body.strip() and endpoint == 'v3/BusArrival':
                    return {'BusStopCode':params['BusStopCode'],'Services':[]}
                data = json.loads(body)
                if not isinstance(data,dict):
                    raise ValueError('Expected an LTA JSON object')
                return data
            except HTTPError as error:
                if error.code not in {429,500,502,503,504} or attempt+1 == self.attempts:
                    raise
                retry_after = error.headers.get('Retry-After')
                if retry_after:
                    try:
                        delay = float(retry_after)
                    except ValueError:
                        delay = (parsedate_to_datetime(retry_after)-datetime.now(timezone.utc)).total_seconds()
                    if delay > 60:
                        raise  # let a later job retry; do not retry before provider's delay
                    delay = max(0,delay)
            except (URLError,TimeoutError):
                if attempt+1 == self.attempts:
                    raise
            time.sleep(delay)
        raise RuntimeError('Retry loop exhausted')

    def all_rows(self, endpoint):
        rows, skip = [], 0
        for _ in range(1000):
            page = self.get(endpoint,{'$skip':skip}).get('value')
            if not isinstance(page,list):
                raise ValueError('Missing value array; do not overwrite SQL data')
            rows.extend(page)
            if len(page) < 500:
                return rows
            skip += len(page)
        raise ValueError('Pagination limit exceeded')
