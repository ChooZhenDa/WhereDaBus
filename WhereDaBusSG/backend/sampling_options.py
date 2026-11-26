"""Read-only choices for a small, stable cohort of actual imported stops."""
import os
import re

from .storage import sql_connection


CHOOSE_SAMPLE_STOPS_SQL = '''WITH known_services AS (
    SELECT DISTINCT r.stop_code, r.service_no
    FROM transport.bus_routes r
    JOIN transport.active_route_summaries a
      ON a.service_no=r.service_no AND a.direction=r.direction
), candidates AS (
    SELECT s.stop_code, s.description, s.road_name, s.latitude, s.longitude,
           COALESCE(array_agg(k.service_no ORDER BY k.service_no)
               FILTER (WHERE k.service_no IS NOT NULL), ARRAY[]::text[]) AS services
    FROM transport.bus_stops s LEFT JOIN known_services k ON k.stop_code=s.stop_code
    WHERE s.is_active
    GROUP BY s.stop_code, s.description, s.road_name, s.latitude, s.longitude
), filtered AS (
    SELECT * FROM candidates
    WHERE cardinality(%s::text[])=0 OR services && %s::text[]
), spread AS (
    SELECT *, row_number() OVER (
        PARTITION BY floor(latitude/0.02), floor(longitude/0.02)
        ORDER BY cardinality(services) DESC, stop_code) AS cell_rank
    FROM filtered
)
SELECT stop_code AS "stopCode", description, road_name AS "roadName", services
FROM spread
ORDER BY (cardinality(services)>0) DESC, cell_rank,
         cardinality(services) DESC, stop_code
LIMIT 10'''


CONFIGURED_STOP_OPTIONS_SQL = '''WITH known_services AS (
    SELECT DISTINCT r.stop_code, r.service_no
    FROM transport.bus_routes r
    JOIN transport.active_route_summaries a
      ON a.service_no=r.service_no AND a.direction=r.direction
    WHERE r.stop_code=ANY(%s)
)
SELECT s.stop_code AS "stopCode", s.description, s.road_name AS "roadName",
       COALESCE(array_agg(k.service_no ORDER BY k.service_no)
           FILTER (WHERE k.service_no IS NOT NULL), ARRAY[]::text[]) AS services
FROM transport.bus_stops s LEFT JOIN known_services k ON k.stop_code=s.stop_code
WHERE s.is_active AND s.stop_code=ANY(%s)
GROUP BY s.stop_code, s.description, s.road_name
ORDER BY s.stop_code'''


def sampling_selection(environment=None):
    """Only return validated public stop/service identifiers, never arbitrary env values."""
    environment = os.environ if environment is None else environment
    stops = sorted(set(x.strip() for x in environment.get('SAMPLE_STOPS', '').split(',') if x.strip()))
    services = sorted(set(x.strip() for x in environment.get('SAMPLE_SERVICES', '').split(',') if x.strip()))
    valid = (1 <= len(stops) <= 20 and all(re.fullmatch(r'[0-9]{5}', x) for x in stops)
             and all(re.fullmatch(r'[A-Za-z0-9]{1,12}', x) for x in services))
    return (stops, services) if valid else ([], [])


def choose_sample_stops():
    """Prefer known services and spread choices across roughly 2 km grid cells.

    This is a bounded starter sample, not all Singapore stops or a claim about
    live operating service. Stop-only imports remain sufficient for selection.
    """
    from psycopg.rows import dict_row
    services = sorted(set(x.strip() for x in os.getenv('SAMPLE_SERVICES', '').split(',') if x.strip()))
    if any(not re.fullmatch(r'[A-Za-z0-9]{1,12}', x) for x in services):
        raise ValueError('SAMPLE_SERVICES contains an invalid service number. Correct or clear the optional service filter.')
    with sql_connection() as connection:
        connection.execute("SET LOCAL statement_timeout = '5s'")
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(CHOOSE_SAMPLE_STOPS_SQL, (services, services))
            stops = cursor.fetchall()
    if not stops and services:
        raise ValueError('No imported stops are known to serve your configured service filter. Run Refresh-Data.cmd or change SAMPLE_SERVICES, then try again.')
    if not stops:
        raise ValueError('No active stops are imported. Run Refresh-Data.cmd, then Start-Analytics.cmd again.')
    return {'stops': stops}
