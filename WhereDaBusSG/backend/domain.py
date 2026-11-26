"""Pure parsing/validation: testable without network access or database drivers."""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import math
import re

UTC = timezone.utc
LOAD_SCORE = {'SEA': 0, 'SDA': 1, 'LSD': 2}

def stop_code(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{5}', value):
        raise ValueError('Expected a five-character stop code; preserve leading zeroes')
    return value

def service_no(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9]{1,12}', value):
        raise ValueError('Invalid service number')
    return value

def parse_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else None

def optional_code(value):
    return stop_code(value) if value else None

def normalize_arrivals(payload, observed_at, poll_id, selected_services=None):
    """One observation per stop/service/operator/poll; arrays retain next 3 buses.

    observed_at is backend receipt time, NOT an LTA measurement timestamp.
    Unknown/missing fields remain null; missing rank 1 is never promoted from rank 2.
    """
    if observed_at.tzinfo is None:
        raise ValueError('observed_at must be timezone-aware')
    observed_at = observed_at.astimezone(UTC)
    code = stop_code(payload['BusStopCode'])
    services = payload.get('Services', [])
    if not isinstance(services, list):
        raise ValueError('Services must be an array')
    docs, seen = [], set()
    for svc in services:
        number = service_no(svc['ServiceNo'])
        if selected_services and number not in selected_services:
            continue
        operator = str(svc.get('Operator') or '').strip()
        if not operator:
            raise ValueError('Missing operator')
        key = (number, operator)
        if key in seen:
            raise ValueError('Duplicate service/operator in payload')
        seen.add(key)
        buses = []
        for rank, field in enumerate(('NextBus', 'NextBus2', 'NextBus3'), 1):
            raw = svc.get(field) or {}
            if not isinstance(raw, dict):
                raise ValueError('Bus entry must be an object')
            eta = parse_time(raw.get('EstimatedArrival'))
            load = raw.get('Load') if raw.get('Load') in LOAD_SCORE else None
            vehicle = raw.get('Type') if raw.get('Type') in {'SD','DD','BD'} else None
            monitor = raw.get('Monitored')
            monitor = int(monitor) if monitor in (0,1,'0','1') else None
            visit = str(raw.get('VisitNumber') or '')
            point = None
            try:
                lat, lon = float(raw['Latitude']), float(raw['Longitude'])
                if (math.isfinite(lat) and math.isfinite(lon) and
                    -90 <= lat <= 90 and -180 <= lon <= 180 and (lat, lon) != (0, 0)):
                    point = {'type':'Point', 'coordinates':[lon, lat]}
            except (KeyError, TypeError, ValueError):
                pass
            buses.append({
                'rank':rank, 'eta':eta,
                'etaSeconds':(eta-observed_at).total_seconds() if eta else None,
                'load':load, 'loadScore':LOAD_SCORE.get(load),
                'vehicleType':vehicle, 'feature':raw.get('Feature') or None,
                'monitored':monitor, 'visitNumber':int(visit) if visit.isdigit() else None,
                'originCode':optional_code(raw.get('OriginCode')),
                'destinationCode':optional_code(raw.get('DestinationCode')),
                'location':point,
            })
        logical_key = hashlib.sha256(f'{poll_id}|{code}|{number}|{operator}'.encode()).hexdigest()
        docs.append({'schemaVersion':1, 'observedAt':observed_at,
                     'meta':{'stopCode':code,'serviceNo':number,'operator':operator},
                     'pollId':poll_id, 'logicalKey':logical_key, 'arrivals':buses,
                     'source':'LTA v3/BusArrival'})
    return docs

def validate_static(stops, services, routes):
    """Validate snapshot integrity; return service directions without route rows.

    A service may have no route occurrences in the fetched route feed. Keep
    its metadata and report that coverage gap without inventing a route.

    This strict mode also requires contiguous sequence numbers. Nondecreasing
    distance is required in every import mode. These are cross-row rules that
    SQL CHECK cannot enforce by itself.
    """
    missing, _ = _validate_static(stops, services, routes, skip_incomplete=False)
    return missing

def select_complete_routes(stops, services, routes):
    """Validate every row, then exclude whole directions with sequence gaps.

    This is an explicit partial-coverage import, not a repair of provider data.
    Bad references, duplicates, invalid fields and decreasing distances remain
    fatal even when they occur in a direction that would otherwise be excluded.
    """
    missing, excluded = _validate_static(stops, services, routes, skip_incomplete=True)
    excluded_keys = {(x['serviceNo'], x['direction']) for x in excluded}
    retained = [r for r in routes if (r['ServiceNo'], int(r['Direction'])) not in excluded_keys]
    if not retained:
        raise ValueError('No complete route directions remain; refusing to replace stored routes')
    missing_keys = {(x['serviceNo'], x['direction']) for x in missing} | excluded_keys
    coverage = [{'serviceNo':number, 'direction':direction}
                for number,direction in sorted(missing_keys)]
    return retained, coverage, excluded


def retain_available_routes(stops, services, routes):
    """Keep valid published occurrences and report detected sequence gaps.

    Gaps do not invalidate unrelated journeys within the same direction.
    Occurrences retain the provider's original sequence numbers and distances;
    absent rows are never fabricated. All structural checks still apply.
    "No detected gaps" does not prove that the provider supplied a route's end.
    """
    missing, partial = _validate_static(stops, services, routes, skip_incomplete=True)
    return list(routes), missing, partial

def _static_integer(value, name):
    """Reject fractional values and booleans instead of silently truncating."""
    try:
        result = int(value)
        valid = not isinstance(value, bool) and Decimal(str(value)) == result
    except (TypeError, ValueError, ArithmeticError):
        valid = False
    if not valid:
        raise ValueError(f'Invalid {name}')
    return result

def _validate_static(stops, services, routes, *, skip_incomplete):
    if not stops or not services or not routes:
        raise ValueError('Refusing an empty infrastructure snapshot')
    stop_ids = [stop_code(x['BusStopCode']) for x in stops]
    if len(set(stop_ids)) != len(stop_ids):
        raise ValueError('Duplicate bus stop')
    stop_ids = set(stop_ids)
    for s in stops:
        lat, lon = float(s['Latitude']), float(s['Longitude'])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError('Invalid coordinates')
        if not str(s['Description']).strip():
            raise ValueError('Missing description')
    service_map = {}
    for s in services:
        k = (service_no(s['ServiceNo']), _static_integer(s['Direction'], 'service direction'))
        if k[1] not in (1,2) or k in service_map:
            raise ValueError('Invalid or duplicate service direction')
        if not str(s['Operator']).strip():
            raise ValueError('Missing operator')
        for field in ('OriginCode','DestinationCode'):
            if s.get(field) and optional_code(s[field]) not in stop_ids:
                raise ValueError('Service references absent stop')
        service_map[k] = s
    grouped = defaultdict(list)
    keys = set()
    for r in routes:
        k = (service_no(r['ServiceNo']), _static_integer(r['Direction'], 'route direction'))
        seq = _static_integer(r['StopSequence'], 'stop sequence')
        if not 1 <= seq <= 2147483647:
            raise ValueError('Invalid stop sequence')
        if k not in service_map or r['BusStopCode'] not in stop_ids or (*k,seq) in keys:
            raise ValueError('Dangling or duplicate route occurrence')
        if r.get('Operator') != service_map[k]['Operator']:
            raise ValueError('Inconsistent operator across feeds')
        distance = Decimal(str(r['Distance']))
        if not distance.is_finite() or not 0 <= distance < 1000:
            raise ValueError('Invalid distance')
        keys.add((*k,seq))
        grouped[k].append((seq,distance))
    # Missing child rows are a coverage gap, not a dangling foreign key.
    # Still validate every route occurrence that is present below.
    excluded = []
    for (number,direction),values in sorted(grouped.items()):
        values.sort()
        if any(b[1] < a[1] for a,b in zip(values,values[1:])):
            raise ValueError('Cumulative route distance decreases')
        # Scan the published occurrences; never allocate a range up to an
        # untrusted sequence value. Keep a bounded set of diagnostic examples.
        ranges, range_count, previous = [], 0, 0
        for sequence,_ in values:
            if sequence > previous+1:
                range_count += 1
                if len(ranges) < 20:
                    ranges.append({'from':previous+1, 'to':sequence-1})
            previous = sequence
        if range_count:
            if skip_incomplete:
                excluded.append({'serviceNo':number, 'direction':direction,
                                 'reason':'Incomplete stop sequence',
                                 'rowCount':len(values), 'firstSequence':values[0][0],
                                 'lastSequence':values[-1][0], 'missingRanges':ranges,
                                 'missingRangeCount':range_count})
                continue
            raise ValueError(
                f'Incomplete stop sequence for service {number}, direction {direction}; '
                f'observed {len(values)} rows (first={values[0][0]}, last={values[-1][0]}). '
                'Run diagnose-static for details, or sync-stops to load stop locations independently.')
    missing = [{'serviceNo':number,'direction':direction}
               for number,direction in sorted(set(service_map)-set(grouped))]
    return missing, excluded
