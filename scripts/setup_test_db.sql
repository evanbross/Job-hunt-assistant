-- =============================================================================
-- Copyright (c) 2026 ZombieTech LLC. All rights reserved.
--
-- LICENSE: Personal Use Only
-- This source code is made available for personal, non-commercial use only.
-- You may read, study, and run this code for personal purposes.
-- You may NOT:
--   - Use this code, in whole or in part, in any commercial product or service
--   - Sell, sublicense, or otherwise monetize this code or derivatives of it
--   - Redistribute this code without explicit written permission from ZombieTech LLC
--
-- No warranty is provided, express or implied. Use at your own risk.
--
-- Licensing inquiries: license@zombietech.biz
-- =============================================================================

-- One-time setup for the local Postgres test database used by the pytest
-- suite's integration tests (see tests/conftest.py's pg_dsn fixture). This is
-- entirely separate from the Neon database used by the running application —
-- it never touches real job-hunt data.
--
-- Run once, as a superuser, against your local Postgres install:
--   psql -U postgres -h localhost -f scripts/setup_test_db.sql
--
-- Then set these in your .env (not committed) to match:
--   LOCAL_POSTGRES_HOST=localhost
--   LOCAL_POSTGRES_PORT=5432
--   LOCAL_POSTGRES_DB=jha_test_db
--   LOCAL_POSTGRES_USER=jha_test_app
--   LOCAL_POSTGRES_PASSWORD=jha_test_app_password
--
-- Local Postgres installs typically don't have SSL configured, so
-- tests/conftest.py always connects with sslmode=disable regardless of any
-- LOCAL_POSTGRES_SSLMODE value — that setting only matters for the app's own
-- Neon connection (POSTGRES_SSLMODE), not this test database.
--
-- ai: claude-sonnet-5 | 2026-09-27 -- jha_test_app is a real, non-superuser role, not
-- just a name. The suite's connecting role must NOT be a superuser: Postgres
-- superusers bypass row-level security unconditionally, FORCE ROW LEVEL SECURITY
-- included (backend/db/row_level_security.py), so testing through one would make
-- every RLS test pass or fail regardless of whether the policies actually work. This
-- also matches production: a managed Postgres provider's connection role (e.g. Neon's)
-- is not a superuser either, so this keeps local/CI honest about what prod really allows.
-- CREATEROLE is needed only for ensure_readonly_role() (backend/db/readonly_role.py) to
-- provision the separate jobhunt_readonly role at startup.

SELECT 'CREATE ROLE jha_test_app LOGIN PASSWORD ''jha_test_app_password'''
    || ' CREATEROLE NOSUPERUSER NOBYPASSRLS'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'jha_test_app')
\gexec

ALTER ROLE jha_test_app WITH LOGIN PASSWORD 'jha_test_app_password' CREATEROLE NOSUPERUSER NOBYPASSRLS;

-- If jha_test_db already exists, it may have been created (or had tables added) under
-- the old convention of connecting as the bootstrap superuser. REASSIGN OWNED BY can fail
-- on a few objects that role also owns at the cluster level ("required by the database
-- system"), so the reliable fix is to drop and recreate: the suite already TRUNCATEs
-- every table before each test and never keeps state between runs, so nothing of value
-- lives here to reassign in place.
DROP DATABASE IF EXISTS jha_test_db;
CREATE DATABASE jha_test_db OWNER jha_test_app;

\c jha_test_db

GRANT ALL ON SCHEMA public TO jha_test_app;
GRANT ALL PRIVILEGES ON DATABASE jha_test_db TO jha_test_app;
