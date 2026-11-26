"""Read-only, usable journey examples from the imported route network.

Examples establish connectivity, not whether a bus is operating at this moment.
The existing live-arrivals endpoint supplies current operation and ETA data.
"""

DIRECT_ROUTE_EXAMPLES_SQL = '''WITH candidates AS (
    SELECT summary.service_no, summary.direction,
           summary.origin_code, summary.origin_description,
           destination.stop_code AS destination_code,
           destination.description AS destination_description,
           summary.route_length_km
    FROM transport.active_route_summaries summary
    CROSS JOIN LATERAL (
        SELECT stop.stop_code, stop.description
        FROM transport.bus_routes route
        JOIN transport.bus_stops stop ON stop.stop_code=route.stop_code
        WHERE route.service_no=summary.service_no
          AND route.direction=summary.direction
          AND route.stop_code<>summary.origin_code
          AND stop.is_active
        ORDER BY route.stop_sequence DESC
        LIMIT 1
    ) destination
    WHERE summary.route_coverage='no_detected_gaps'
), unique_pairs AS (
    SELECT *, row_number() OVER (
        PARTITION BY origin_code, destination_code
        ORDER BY route_length_km DESC, service_no, direction) AS choice
    FROM candidates
)
SELECT origin_code, origin_description, destination_code,
       destination_description, service_no, direction
FROM unique_pairs
WHERE choice=1
ORDER BY route_length_km DESC, service_no, direction, origin_code, destination_code
LIMIT 5'''
