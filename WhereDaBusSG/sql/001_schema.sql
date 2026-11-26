-- Fresh PostgreSQL schema. The bootstrap command owns the transaction and
-- schema-version marker. Existing installations are not altered by this file.
-- No extensions are required.
CREATE SCHEMA transport;

CREATE TABLE transport.bus_stops (
    stop_code text PRIMARY KEY CHECK (stop_code ~ '^[0-9]{5}$'),
    road_name text NOT NULL,
    description text NOT NULL CHECK (length(btrim(description)) > 0),
    latitude double precision NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude double precision NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    is_active boolean NOT NULL DEFAULT true,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX bus_stops_active_coordinates
    ON transport.bus_stops (latitude, longitude) WHERE is_active;
CREATE INDEX bus_stops_active_description
    ON transport.bus_stops (lower(description)) WHERE is_active;

CREATE TABLE transport.bus_services (
    service_no text NOT NULL CHECK (service_no ~ '^[A-Za-z0-9]{1,12}$'),
    direction smallint NOT NULL CHECK (direction IN (1, 2)),
    operator text NOT NULL CHECK (length(btrim(operator)) > 0),
    category text NOT NULL DEFAULT '',
    origin_code text REFERENCES transport.bus_stops (stop_code),
    destination_code text REFERENCES transport.bus_stops (stop_code),
    loop_description text NOT NULL DEFAULT '',
    is_active boolean NOT NULL DEFAULT true,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (service_no, direction)
);

-- Keep these five columns in order: the importer inserts five VALUES directly.
-- A repeated stop on a loop is valid; route occurrence, not stop code, is unique.
CREATE TABLE transport.bus_routes (
    service_no text NOT NULL,
    direction smallint NOT NULL,
    stop_sequence integer NOT NULL CHECK (stop_sequence > 0),
    stop_code text NOT NULL REFERENCES transport.bus_stops (stop_code),
    distance_km numeric NOT NULL CHECK (distance_km >= 0 AND distance_km < 1000),
    PRIMARY KEY (service_no, direction, stop_sequence),
    FOREIGN KEY (service_no, direction)
        REFERENCES transport.bus_services (service_no, direction)
);
CREATE INDEX bus_routes_by_stop
    ON transport.bus_routes (stop_code, service_no, direction, stop_sequence);

CREATE TABLE transport.import_runs (
    run_id uuid PRIMARY KEY,
    fetched_at timestamptz NOT NULL,
    committed_at timestamptz NOT NULL DEFAULT now(),
    stop_count integer NOT NULL CHECK (stop_count > 0),
    service_count integer NOT NULL CHECK (service_count > 0),
    route_count integer NOT NULL CHECK (route_count > 0)
);
CREATE INDEX import_runs_recent ON transport.import_runs (committed_at DESC);

-- Retain the original domain's account/service-favourite constraints. Browser
-- stop bookmarks use local storage. Named stop-to-stop journeys live in the
-- separate table added by the saved-journeys migration.
CREATE TABLE transport.users (
    user_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    auth_subject text NOT NULL UNIQUE CHECK (length(btrim(auth_subject)) > 0),
    email text NOT NULL CHECK (length(btrim(email)) > 0),
    display_name text NOT NULL CHECK (length(btrim(display_name)) > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX users_email_case_insensitive
    ON transport.users (lower(btrim(email)));

CREATE TABLE transport.favourite_routes (
    user_id uuid NOT NULL REFERENCES transport.users (user_id) ON DELETE CASCADE,
    service_no text NOT NULL,
    direction smallint NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, service_no, direction),
    FOREIGN KEY (service_no, direction)
        REFERENCES transport.bus_services (service_no, direction)
);
