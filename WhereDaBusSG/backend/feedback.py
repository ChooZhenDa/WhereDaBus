"""Short-lived rider reports for a published arrival at one boarding stop."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, Response
from pydantic import BaseModel

from .journeys import Owner, browser_owner
from .live import LiveUnavailable


router = APIRouter(prefix='/feedback')
Stop = Annotated[str, Path(pattern=r'^[0-9]{5}$')]
Timing = Annotated[str, Path(pattern=r'^[0-9a-f]{64}$')]
ISSUES = ('full', 'delayed', 'skipped')


class RiderReport(BaseModel):
    issue: Literal['full', 'delayed', 'skipped']


def timing_key(meta, bus):
    # A rank shifts as earlier buses leave. The provider supplies no vehicle
    # identifier, so a changed ETA must not inherit another timing's reports.
    fields = [meta['stopCode'], meta['serviceNo'], meta['operator'],
              bus['eta'].astimezone(timezone.utc).isoformat(timespec='milliseconds'),
              bus.get('originCode'), bus.get('destinationCode'), bus.get('visitNumber')]
    return sha256(json.dumps(fields, separators=(',', ':')).encode()).hexdigest()


def add_feedback_keys(snapshot, now=None):
    now = now or datetime.now(timezone.utc)
    if snapshot.get('status') != 'available' or snapshot.get('freshUntil', now + timedelta(seconds=1)) <= now:
        return snapshot
    for obs in snapshot.get('observations', ()):
        for bus in obs['arrivals']:
            if bus.get('eta') and bus['eta'] >= now:
                bus['feedbackKey'] = timing_key(obs['meta'], bus)
    return snapshot


def optional_owner(response: Response, key: Annotated[str | None, Header(alias='X-Journey-Key')] = None):
    response.headers['Cache-Control'] = 'no-store'
    return browser_owner(response, key) if key is not None else None


def collection(request):
    db = getattr(request.app.state, 'mongo', None)
    if db is None:
        raise HTTPException(503, 'Rider feedback is temporarily unavailable.')
    return db.arrival_feedback


def empty_summary():
    return {'counts': dict.fromkeys(ISSUES, 0), 'mine': None}


def summaries(reports, stop, owner, timing=None):
    match = {'stop': stop, 'expiresAt': {'$gt': datetime.now(timezone.utc)}}
    if timing is not None:
        match['timing'] = timing
    groups = reports.aggregate([
        {'$match': match},
        {'$group': {'_id': {'timing': '$timing', 'issue': '$issue'},
                    'total': {'$sum': 1},
                    'mine': {'$max': {'$eq': ['$owner', owner]} if owner is not None else False}}},
    ], maxTimeMS=2500)
    result = {}
    for row in groups:
        key, issue = row['_id']['timing'], row['_id']['issue']
        item = result.setdefault(key, empty_summary())
        item['counts'][issue] = row['total']
        if row['mine']:
            item['mine'] = issue
    return result


@router.get('/{stop}')
def read_feedback(stop: Stop, request: Request, owner: Annotated[bytes | None, Depends(optional_owner)]):
    return {'reports': summaries(collection(request), stop, owner)}


def current_eta(request, stop, timing):
    service = getattr(request.app.state, 'live', None)
    if service is None:
        raise HTTPException(503, 'Arrival data is temporarily unavailable.')
    try:
        snapshot = service.get(stop)
    except LiveUnavailable:
        raise HTTPException(503, 'Live arrivals are temporarily unavailable.') from None
    now = datetime.now(timezone.utc)
    if snapshot['status'] != 'available' or snapshot['freshUntil'] <= now:
        raise HTTPException(409, 'Refresh arrivals to report a current bus timing.')
    for obs in snapshot.get('observations', ()):
        if obs['meta']['stopCode'] == stop:
            for bus in obs['arrivals']:
                if bus.get('eta') and bus['eta'] >= now and timing_key(obs['meta'], bus) == timing:
                    return bus['eta']
    raise HTTPException(409, 'This arrival time has changed. Refresh and choose the current arrival.')


@router.put('/{stop}/{timing}')
def put_feedback(stop: Stop, timing: Timing, body: RiderReport, request: Request, owner: Owner):
    reports = collection(request)
    eta = current_eta(request, stop, timing)
    now = datetime.now(timezone.utc)
    expires = min(eta + timedelta(minutes=5), now + timedelta(minutes=30))
    # This deterministic primary key makes concurrent inserts and retries of a
    # single rider's report one upsert. Another rider gets a separate document.
    report_id = sha256(owner + bytes.fromhex(timing)).hexdigest()
    reports.update_one({'_id': report_id},
                       {'$setOnInsert': {'stop': stop, 'timing': timing, 'owner': owner},
                        '$set': {'issue': body.issue, 'expiresAt': expires}}, upsert=True)
    return summaries(reports, stop, owner, timing).get(timing, empty_summary())


@router.delete('/{stop}/{timing}')
def clear_feedback(stop: Stop, timing: Timing, request: Request, owner: Owner):
    reports = collection(request)
    report_id = sha256(owner + bytes.fromhex(timing)).hexdigest()
    reports.delete_one({'_id': report_id, 'stop': stop})
    return summaries(reports, stop, owner, timing).get(timing, empty_summary())
