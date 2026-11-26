-- Read-only route queries. Retain known active stops even when another part
-- of a direction is missing. "no_detected_gaps" is a data check, not proof
-- of full provider coverage or of a bus operating at the time of a search.
CREATE VIEW transport.active_route_summaries AS
WITH route_totals AS (
    SELECT r.service_no, r.direction,
           min(r.stop_sequence) FILTER (WHERE st.is_active) AS first_sequence,
           max(r.stop_sequence) FILTER (WHERE st.is_active) AS last_sequence,
           count(*) FILTER (WHERE st.is_active) AS observed_stop_count,
           bool_and(st.is_active) AND min(r.stop_sequence)=1
               AND count(*)=max(r.stop_sequence) AS sequence_gap_free
    FROM transport.bus_routes AS r
    JOIN transport.bus_stops AS st ON st.stop_code=r.stop_code
    GROUP BY r.service_no, r.direction
    HAVING count(*) FILTER (WHERE st.is_active)>0
), assessed AS (
    SELECT s.service_no, s.direction, s.operator, s.category,
           first_stop.stop_code AS origin_code,
           first_stop.description AS origin_description,
           last_stop.stop_code AS destination_code,
           last_stop.description AS destination_description,
           last_route.distance_km-first_route.distance_km AS known_distance_span_km,
           t.observed_stop_count, s.loop_description,
           t.sequence_gap_free
               AND (s.origin_code IS NULL OR s.origin_code=first_stop.stop_code)
               AND (s.destination_code IS NULL OR s.destination_code=last_stop.stop_code)
               AS gap_free
    FROM route_totals AS t
    JOIN transport.bus_services AS s
      ON s.service_no=t.service_no AND s.direction=t.direction AND s.is_active
    JOIN transport.bus_routes AS first_route
      ON first_route.service_no=t.service_no AND first_route.direction=t.direction
     AND first_route.stop_sequence=t.first_sequence
    JOIN transport.bus_stops AS first_stop ON first_stop.stop_code=first_route.stop_code
    JOIN transport.bus_routes AS last_route
      ON last_route.service_no=t.service_no AND last_route.direction=t.direction
     AND last_route.stop_sequence=t.last_sequence
    JOIN transport.bus_stops AS last_stop ON last_stop.stop_code=last_route.stop_code
)
SELECT service_no, direction, operator, category,
       origin_code, origin_description, destination_code, destination_description,
       CASE WHEN gap_free THEN known_distance_span_km ELSE NULL::numeric END AS route_length_km,
       CASE WHEN gap_free THEN observed_stop_count ELSE NULL::bigint END AS stop_count,
       loop_description,
       CASE WHEN gap_free THEN 'no_detected_gaps' ELSE 'partial' END AS route_coverage,
       observed_stop_count, known_distance_span_km
FROM assessed;

CREATE FUNCTION transport.longest_routes()
RETURNS SETOF transport.active_route_summaries
LANGUAGE sql STABLE
SET search_path = pg_catalog, transport
AS $function$
    SELECT r.* FROM transport.active_route_summaries AS r
    WHERE r.route_coverage='no_detected_gaps'
    ORDER BY r.route_length_km DESC, r.service_no, r.direction
    LIMIT 10;
$function$;

CREATE FUNCTION transport.routes_on_road(p_name text)
RETURNS SETOF transport.active_route_summaries
LANGUAGE sql STABLE
SET search_path = pg_catalog, transport
AS $function$
    SELECT summary.* FROM transport.active_route_summaries AS summary
    WHERE length(btrim(p_name))>0 AND EXISTS (
        SELECT 1 FROM transport.bus_routes AS r
        JOIN transport.bus_stops AS st ON st.stop_code=r.stop_code AND st.is_active
        WHERE r.service_no=summary.service_no AND r.direction=summary.direction
          AND strpos(lower(st.road_name),lower(btrim(p_name)))>0
    )
    ORDER BY summary.service_no, summary.direction;
$function$;

CREATE FUNCTION transport.direct_routes(p_origin text, p_destination text)
RETURNS TABLE (
    service_no text, direction smallint, operator text,
    origin_code text, origin_description text,
    destination_code text, destination_description text,
    origin_sequence integer, destination_sequence integer,
    stops_between integer, distance_km numeric,
    segment_coverage text, route_coverage text, observed_stops_between integer
)
LANGUAGE sql STABLE
SET search_path = pg_catalog, transport
AS $function$
    -- Known endpoints remain searchable in forward sequence order. A gap
    -- outside the selected interval does not invalidate that interval.
    -- Across a gap, the connection is partial and exact counts/distances are
    -- withheld. Never infer a wrap past the final recorded loop occurrence.
    WITH candidates AS (
        SELECT s.service_no, s.direction, s.operator,
               origin.stop_code AS origin_code, origin.description AS origin_description,
               destination.stop_code AS destination_code,
               destination.description AS destination_description,
               boarding.stop_sequence AS origin_sequence,
               alighting.stop_sequence AS destination_sequence,
               alighting.stop_sequence-boarding.stop_sequence AS sequence_span,
               alighting.distance_km-boarding.distance_km AS distance_span,
               segment.row_count-1 AS observed_stops_between,
               segment.row_count=alighting.stop_sequence::bigint-boarding.stop_sequence+1 AS gap_free,
               summary.route_coverage
        FROM transport.bus_routes AS boarding
        JOIN transport.bus_routes AS alighting
          ON alighting.service_no=boarding.service_no
         AND alighting.direction=boarding.direction
         AND alighting.stop_sequence>boarding.stop_sequence
        JOIN transport.bus_services AS s
          ON s.service_no=boarding.service_no AND s.direction=boarding.direction AND s.is_active
        JOIN transport.active_route_summaries AS summary
          ON summary.service_no=s.service_no AND summary.direction=s.direction
        JOIN transport.bus_stops AS origin
          ON origin.stop_code=boarding.stop_code AND origin.is_active
        JOIN transport.bus_stops AS destination
          ON destination.stop_code=alighting.stop_code AND destination.is_active
        CROSS JOIN LATERAL (
            SELECT count(*) AS row_count, bool_and(st.is_active) AS all_active
            FROM transport.bus_routes AS r
            JOIN transport.bus_stops AS st ON st.stop_code=r.stop_code
            WHERE r.service_no=boarding.service_no AND r.direction=boarding.direction
              AND r.stop_sequence BETWEEN boarding.stop_sequence AND alighting.stop_sequence
        ) AS segment
        WHERE boarding.stop_code=p_origin AND alighting.stop_code=p_destination
          AND alighting.distance_km>=boarding.distance_km AND segment.all_active
    ), ranked AS (
        SELECT c.*, row_number() OVER (
            PARTITION BY c.service_no,c.direction
            ORDER BY c.gap_free DESC, c.distance_span, c.sequence_span,
                     c.origin_sequence,c.destination_sequence
        ) AS choice FROM candidates c
    )
    SELECT j.service_no,j.direction,j.operator,j.origin_code,j.origin_description,
           j.destination_code,j.destination_description,j.origin_sequence,j.destination_sequence,
           CASE WHEN j.gap_free THEN j.sequence_span ELSE NULL::integer END,
           CASE WHEN j.gap_free THEN j.distance_span ELSE NULL::numeric END,
           CASE WHEN j.gap_free THEN 'no_detected_gaps' ELSE 'partial' END,
           j.route_coverage,j.observed_stops_between::integer
    FROM ranked AS j WHERE j.choice=1
    ORDER BY j.gap_free DESC,j.distance_span,j.sequence_span,j.service_no,j.direction;
$function$;

CREATE FUNCTION transport.nearby_stops(
    p_lat double precision, p_lon double precision, p_radius double precision DEFAULT 500
)
RETURNS TABLE (
    stop_code text, description text, road_name text,
    latitude double precision, longitude double precision,
    distance_m double precision, services text[]
)
LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, transport
AS $function$
BEGIN
    IF p_lat IS NULL OR NOT (p_lat BETWEEN -90 AND 90)
       OR p_lon IS NULL OR NOT (p_lon BETWEEN -180 AND 180)
       OR p_radius IS NULL OR NOT (p_radius BETWEEN 1 AND 5000) THEN
        RAISE EXCEPTION 'Coordinates or radius outside the supported range'
            USING ERRCODE = '22023';
    END IF;

    RETURN QUERY
    WITH distances AS (
        SELECT st.stop_code, st.description, st.road_name, st.latitude, st.longitude,
               6371000.0 * 2.0 * asin(sqrt(least(1.0, greatest(0.0,
                   power(sin(radians(st.latitude - p_lat) / 2.0), 2)
                   + cos(radians(p_lat)) * cos(radians(st.latitude))
                     * power(sin(radians(st.longitude - p_lon) / 2.0), 2)
               )))) AS distance_m
        FROM transport.bus_stops AS st
        WHERE st.is_active
    )
    SELECT d.stop_code, d.description, d.road_name, d.latitude, d.longitude,
           d.distance_m,
           (SELECT array_agg(DISTINCT s.service_no ORDER BY s.service_no)
            FROM transport.bus_routes AS r
            JOIN transport.bus_services AS s
              ON s.service_no = r.service_no AND s.direction = r.direction AND s.is_active
            WHERE r.stop_code = d.stop_code) AS services
    FROM distances AS d
    WHERE d.distance_m <= p_radius
    ORDER BY d.distance_m, d.stop_code;
END;
$function$;

COMMENT ON FUNCTION transport.nearby_stops(double precision, double precision, double precision)
IS 'Straight-line haversine distance in metres, not walking distance. NULL services means no imported active route coverage; it does not mean no buses operate.';
