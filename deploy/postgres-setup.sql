-- Run once as the postgres superuser:  sudo -u postgres psql -f deploy/postgres-setup.sql
CREATE ROLE geoagent WITH LOGIN PASSWORD 'change-me';
CREATE DATABASE geoagent OWNER geoagent ENCODING 'UTF8';
\c geoagent
GRANT ALL ON SCHEMA public TO geoagent;
-- then apply the schema:  psql -U geoagent -d geoagent -f backend/db/schema.sql
