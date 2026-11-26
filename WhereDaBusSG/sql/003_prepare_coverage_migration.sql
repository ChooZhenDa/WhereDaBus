-- Run only for a recognized version-1 installation, inside bootstrap's
-- transaction and advisory locks. Recreate queries from 002_queries.sql.
-- Tables, route rows, import history and credentials are not changed.
-- No CASCADE: unexpected external dependencies fail safely and roll back.
DROP FUNCTION transport.longest_routes();
DROP FUNCTION transport.routes_on_road(text);
DROP FUNCTION transport.direct_routes(text,text);
DROP FUNCTION transport.nearby_stops(double precision,double precision,double precision);
DROP VIEW transport.active_route_summaries;
