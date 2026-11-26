-- Applied once by the versioned bootstrap, in the same transaction as its marker.
-- Stop metadata is retired in place during imports, so saved trips survive a refresh.
CREATE TABLE transport.saved_journeys (
    journey_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_hash bytea NOT NULL CHECK (octet_length(owner_hash) = 32),
    label text NOT NULL CHECK (label = btrim(label) AND length(label) BETWEEN 1 AND 80),
    origin text NOT NULL REFERENCES transport.bus_stops (stop_code),
    destination text NOT NULL REFERENCES transport.bus_stops (stop_code),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (origin <> destination),
    UNIQUE (owner_hash, origin, destination)
);
