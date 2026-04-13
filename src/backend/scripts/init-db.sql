-- FM Platform init SQL
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "timescaledb";
CREATE SCHEMA IF NOT EXISTS keycloak;
SELECT 'FM Platform DB initialized' AS status;
