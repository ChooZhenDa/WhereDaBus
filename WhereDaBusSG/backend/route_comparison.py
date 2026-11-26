"""Compare direct services at a boarding stop using one shared arrival snapshot."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from pymongo.errors import PyMongoError

from .analytics import SG, morning_peak
from .analytics_window import analytics_window
from .fares import adult_card_fare
from .feedback import add_feedback_keys
from .live import LiveUnavailable


# Read the chosen journeys and every direction of each matching service in one
# PostgreSQL snapshot. A service can also visit the boarding stop on a direction
# that does not reach the requested destination.
DIRECT_COMPARISON_SQL = """
WITH direct AS MATERIALIZED (
    SELECT d.*, s.category
    FROM transport.direct_routes(%s, %s) d
    JOIN transport.bus_services s USING (service_no, direction, operator)
), patterns AS (
    SELECT s.service_no, s.operator, s.direction, s.origin_code, s.destination_code,
           array_agg(r.stop_code ORDER BY r.stop_sequence)
               FILTER (WHERE r.stop_code IS NOT NULL) AS stops,
           array_agg(r.stop_sequence ORDER BY r.stop_sequence)
               FILTER (WHERE r.stop_code IS NOT NULL) AS sequences,
           coalesce(min(r.stop_sequence) = 1 AND count(r.stop_code) = max(r.stop_sequence)
               AND bool_and(st.is_active)
               AND (s.origin_code IS NULL OR s.origin_code =
                    (array_agg(r.stop_code ORDER BY r.stop_sequence))[1])
               AND (s.destination_code IS NULL OR s.destination_code =
                    (array_agg(r.stop_code ORDER BY r.stop_sequence DESC))[1]),
               false) AS complete
    FROM (SELECT DISTINCT service_no, operator FROM direct) d
    JOIN transport.bus_services s USING (service_no, operator)
    LEFT JOIN transport.bus_routes r USING (service_no, direction)
    LEFT JOIN transport.bus_stops st ON st.stop_code = r.stop_code
    WHERE s.is_active
    GROUP BY s.service_no, s.operator, s.direction, s.origin_code, s.destination_code
), by_service AS (
    SELECT service_no, operator, jsonb_agg(jsonb_build_object(
        'direction', direction, 'stops', stops, 'sequences', sequences,
        'origin', origin_code, 'terminal', destination_code, 'complete', complete)) AS patterns
    FROM patterns GROUP BY service_no, operator
)
SELECT d.*, p.patterns FROM direct d JOIN by_service p USING (service_no, operator)
ORDER BY (d.segment_coverage = 'no_detected_gaps') DESC, d.distance_km NULLS LAST,
         d.destination_sequence - d.origin_sequence, d.service_no, d.direction
"""


def positions(stops, code):
    return [index for index, stop in enumerate(stops) if stop == code]


def partial_forward_visit(route, pattern, bus):
    """The nth visit is upstream if at least n visits are published before alighting.

    An upstream gap might hide an earlier visit to the boarding stop. It
    cannot move the nth visit past the selected destination. Use this only
    for a vehicle with the uniquely identified full direction's endpoints.
    """
    if route.get('segment_coverage') != 'no_detected_gaps' or not bus.get('visitNumber'):
        return False
    visits = [seq for seq, stop in zip(pattern.get('sequences') or [], pattern['stops'] or [])
              if stop == route['origin_code'] and seq <= route['origin_sequence']]
    return (bool(visits) and visits[-1] == route['origin_sequence'] and
            bus['visitNumber'] == len(visits))


def serves_journey(route, patterns, bus):
    """All plausible interpretations of this bus must serve this exact leg.

    Endpoint codes can describe a short working. VisitNumber counts from
    the vehicle's starting point and can disambiguate repeat visits on loops.
    Route gaps need a separate forward-reachability check.
    """
    if not bus.get('destinationCode'):
        return False
    exact = [p['direction'] for p in patterns
             if bus.get('originCode') and p.get('origin') == bus['originCode']
             and p.get('terminal') == bus['destinationCode']]
    confirmed = False
    for pattern in patterns:
        if not pattern['complete']:
            if len(exact) != 1:
                return False
            if pattern['direction'] == exact[0]:
                if route['direction'] != exact[0] or not partial_forward_visit(route, pattern, bus):
                    return False
                confirmed = True
            continue
        stops = pattern['stops']
        boarding = positions(stops, route['origin_code'])
        if not boarding:
            continue
        start_code = bus.get('originCode')
        # Without a provider origin, an ordinal on a repeated stop cannot
        # select a visit: a short working could have begun mid-route.
        use_visit = start_code is not None
        starts = ([0] if not start_code or start_code == stops[0]
                  else positions(stops, start_code))
        ends = ([len(stops) - 1] if bus['destinationCode'] == stops[-1]
                else positions(stops, bus['destinationCode']))
        for start in starts:
            for end in ends:
                if start >= end:
                    continue
                visits = [index for index in boarding if start <= index <= end]
                for visit, board in enumerate(visits, 1):
                    reported_visit = bus.get('visitNumber')
                    if reported_visit and (visit < reported_visit or
                                           (use_visit and reported_visit != visit)):
                        continue
                    if (pattern['direction'] != route['direction']
                            or board + 1 != route['origin_sequence']
                            or route['destination_sequence'] > end + 1):
                        return False
                    confirmed = True
    return confirmed


def next_bus(route, patterns, buses, status, now):
    result = {'status': 'no_prediction', 'eta': None, 'load': None, 'monitored': None}
    if status in ('unavailable', 'stale'):
        return {**result, 'status': status}
    future = sorted((bus for bus in buses if bus.get('eta') and bus['eta'] >= now),
                    key=lambda bus: bus['eta'])
    for bus in future:
        if serves_journey(route, patterns, bus):
            return {'status': 'available', 'eta': bus['eta'],
                    'load': bus.get('load') if bus.get('monitored') == 1 else None,
                    'monitored': bus.get('monitored'),
                    **({'feedbackKey': bus['feedbackKey']} if 'feedbackKey' in bus else {})}
    if future:
        result['status'] = 'unconfirmed'
    return result


def crowding_history(db, origin, services, start, end):
    if db is None:
        return None
    # morning_peak already groups by service/operator, deduplicates replayed
    # polls and excludes ineligible loads. Query all candidate services once.
    pipeline = morning_peak({'$in': sorted(services)}, start, end, origin)
    try:
        rows = db.arrival_observations.aggregate(pipeline, allowDiskUse=True, maxTimeMS=3500)
        return {(row['_id']['serviceNo'], row['_id']['operator']): row for row in rows}
    except PyMongoError:
        return None


def summary_for(cohorts, service):
    if cohorts is None:
        return {'status': 'unavailable', 'observations': None, 'validSamples': None,
                'daysWithValidSamples': None, 'seatsAvailablePct': None}
    row = cohorts.get(service, {})
    valid, days = row.get('validSamples', 0), row.get('daysWithValidSamples', 0)
    # Use the same sample/day threshold as analytics.crowded_stops.
    sufficient = valid >= 30 and days >= 3
    return {'status': 'available' if sufficient else 'limited',
            'validSamples': valid, 'daysWithValidSamples': days,
            'observations': row.get('observations', 0),
            'seatsAvailablePct': row.get('seatsAvailablePct') if sufficient else None}


def compare_routes(origin, destination, sql_query, live, db, date_from=None, date_to=None):
    try:
        start, end = analytics_window(date_from, date_to)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    routes = sql_query(DIRECT_COMPARISON_SQL, (origin, destination))
    snapshot = {'status': 'unavailable'}
    cohorts = None
    if routes:
        if live is not None:
            try:
                snapshot = live.get(origin)
            except (LiveUnavailable, PyMongoError):
                pass  # A provider or cache outage must keep the SQL routes visible.
        cohorts = crowding_history(db, origin, {r['service_no'] for r in routes}, start, end)
    now = datetime.now(timezone.utc)
    # A slow history query may outlast the snapshot's remaining freshness.
    status = snapshot['status']
    if status in ('available', 'no_data') and snapshot.get('freshUntil') and snapshot['freshUntil'] <= now:
        status = 'stale'
    add_feedback_keys(snapshot, now)
    by_service = defaultdict(list)
    for obs in snapshot.get('observations', []):
        meta = obs['meta']
        if meta['stopCode'] == origin:
            by_service[meta['serviceNo'], meta['operator']].extend(obs['arrivals'])
    results = []
    for row in routes:
        route = dict(row)
        patterns = route.pop('patterns')
        service = (route['service_no'], route['operator'])
        route['fare'] = adult_card_fare(route, now.astimezone(SG).date())
        route['live'] = next_bus(route, patterns, by_service[service], status, now)
        route['history'] = summary_for(cohorts, service)
        results.append(route)
    results.sort(key=lambda row: (row['live']['eta'] is None, row['live']['eta'] or now))
    return {'origin': origin, 'destination': destination, 'asOf': now,
            'capturedAt': snapshot.get('capturedAt'), 'freshUntil': snapshot.get('freshUntil'),
            'liveStatus': status, 'routes': results,
            'historyWindow': {'dateFrom': start.astimezone(SG).date(),
                              'dateTo': (end - timedelta(microseconds=1)).astimezone(SG).date(),
                              'start': start, 'endExclusive': end, 'timezone': 'Asia/Singapore',
                              'startHour': 7, 'endHour': 9}}
