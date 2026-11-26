-- Read-only query functions for the API. Distances stored by LTA are cumulative
-- kilometres; nearby-stop distances below are great-circle metres.

-- Summaries require every imported stop on a route to remain active. A
-- stop-only refresh can retire stops without changing old route rows; excluding
-- these routes avoids presenting an incomplete old route as current coverage.
CREATE VIEW transport.active_route_summaries AS
WITH route_totals AS (
    SELECT r.service_no, r.direction,
           min(r.stop_sequence) AS first_sequence,
           max(r.stop_sequence) AS last_sequence,
           max(r.distance_km) - min(r.distance_km) AS route_length_km,
           count(*) AS stop_count
    FROM transport.bus_routes AS r
    JOIN transport.bus_stops AS st ON st.stop_code = r.stop_code
    GROUP BY r.service_no, r.direction
    HAVING bool_and(st.is_active)
)
SELECT s.service_no, s.direction, s.operator, s.category,
       first_stop.stop_code AS origin_code,
       first_stop.description AS origin_description,
       last_stop.stop_code AS destination_code,
       last_stop.description AS destination_description,
       t.route_length_km, t.stop_count, s.loop_description
FROM route_totals AS t
JOIN transport.bus_services AS s
    ON s.service_no = t.service_no AND s.direction = t.direction AND s.is_active
JOIN transport.bus_routes AS first_route
    ON first_route.service_no = t.service_no AND first_route.direction = t.direction
   AND first_route.stop_sequence = t.first_sequence
JOIN transport.bus_stops AS first_stop ON first_stop.stop_code = first_route.stop_code
JOIN transport.bus_routes AS last_route
    ON last_route.service_no = t.service_no AND last_route.direction = t.direction
   AND last_route.stop_sequence = t.last_sequence
JOIN transport.bus_stops AS last_stop ON last_stop.stop_code = last_route.stop_code;

CREATE FUNCTION transport.longest_routes()
RETURNS SETOF transport.active_route_summaries
LANGUAGE sql STABLE
SET search_path = pg_catalog, transport
AS $function$
    SELECT r.*
    FROM transport.active_route_summaries AS r
    ORDER BY r.route_length_km DESC, r.service_no, r.direction
    LIMIT 10;
$function$;

CREATE FUNCTION transport.routes_on_road(p_name text)
RETURNS SETOF transport.active_route_summaries
LANGUAGE sql STABLE
SET search_path = pg_catalog, transport
AS $function$
    SELECT summary.*
    FROM transport.active_route_summaries AS summary
    WHERE length(btrim(p_name)) > 0
      AND EXISTS (
          SELECT 1
          FROM transport.bus_routes AS r
          JOIN transport.bus_stops AS st ON st.stop_code = r.stop_code AND st.is_active
          WHERE r.service_no = summary.service_no AND r.direction = summary.direction
            -- Literal case-insensitive substring; % and _ are not wildcards.
            AND strpos(lower(st.road_name), lower(btrim(p_name))) > 0
      )
    ORDER BY summary.service_no, summary.direction;
$function$;

CREATE FUNCTION transport.direct_routes(p_origin text, p_destination text)
RETURNS TABLE (
    service_no text, direction smallint, operator text,
    origin_code text, origin_description text,
    destination_code text, destination_description text,
    origin_sequence integer, destination_sequence integer,
    stops_between integer, distance_km numeric
)
LANGUAGE sql STABLE
SET search_path = pg_catalog, transport
AS $function$
    -- There can be multiple occurrences of either stop on a loop. Choose the
    -- shortest forward pair per service/direction, not an arbitrary occurrence.
    -- The number of stops is the sequence difference (including the alighting
    -- stop and excluding the boarding stop). No wrap beyond the imported route
    -- end is invented. Returning to the same code is possible only when it has
    -- a later occurrence in the imported sequence.
    WITH journeys AS (
        SELECT s.service_no, s.direction, s.operator,
               origin.stop_code AS origin_code, origin.description AS origin_description,
               destination.stop_code AS destination_code,
               destination.description AS destination_description,
               boarding.stop_sequence AS origin_sequence,
               alighting.stop_sequence AS destination_sequence,
               alighting.stop_sequence - boarding.stop_sequence AS stops_between,
               alighting.distance_km - boarding.distance_km AS distance_km,
               row_number() OVER (
                   PARTITION BY s.service_no, s.direction
                   ORDER BY alighting.distance_km - boarding.distance_km,
                            alighting.stop_sequence - boarding.stop_sequence,
                            boarding.stop_sequence, alighting.stop_sequence
               ) AS choice
        FROM transport.bus_routes AS boarding
        JOIN transport.bus_routes AS alighting
          ON alighting.service_no = boarding.service_no
         AND alighting.direction = boarding.direction
         AND alighting.stop_sequence > boarding.stop_sequence
        JOIN transport.bus_services AS s
          ON s.service_no = boarding.service_no AND s.direction = boarding.direction AND s.is_active
        JOIN transport.bus_stops AS origin
          ON origin.stop_code = boarding.stop_code AND origin.is_active
        JOIN transport.bus_stops AS destination
          ON destination.stop_code = alighting.stop_code AND destination.is_active
        WHERE boarding.stop_code = p_origin AND alighting.stop_code = p_destination
          AND alighting.distance_km >= boarding.distance_km
          AND NOT EXISTS (
              SELECT 1
              FROM transport.bus_routes AS segment
              JOIN transport.bus_stops AS st ON st.stop_code = segment.stop_code
              WHERE segment.service_no = boarding.service_no
                AND segment.direction = boarding.direction
                AND segment.stop_sequence BETWEEN boarding.stop_sequence AND alighting.stop_sequence
                AND NOT st.is_active
          )
    )
    SELECT j.service_no, j.direction, j.operator, j.origin_code, j.origin_description,
           j.destination_code, j.destination_description,
           j.origin_sequence, j.destination_sequence, j.stops_between, j.distance_km
    FROM journeys AS j
    WHERE j.choice = 1
    ORDER BY j.distance_km, j.stops_between, j.service_no, j.direction;
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
