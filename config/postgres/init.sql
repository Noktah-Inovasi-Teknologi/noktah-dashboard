-- Initialize databases
-- Note: Main database is created automatically from POSTGRES_DB env var

-- Create additional databases using environment variables
-- Use psql variables to get environment variable values
\set dev_db `echo "$POSTGRES_DB_DEV"`
\set prefect_db `echo "$POSTGRES_DB_PREFECT"`

-- Create databases
CREATE DATABASE :dev_db;
CREATE DATABASE :prefect_db;

-- Grant privileges to main user
\set main_user `echo "$POSTGRES_USER"`
GRANT ALL PRIVILEGES ON DATABASE :dev_db TO :main_user;
GRANT ALL PRIVILEGES ON DATABASE :prefect_db TO :main_user;