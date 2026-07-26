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
--   LOCAL_POSTGRES_USER=postgres
--   LOCAL_POSTGRES_PASSWORD=<your local postgres superuser password>
--
-- Local Postgres installs typically don't have SSL configured, so
-- tests/conftest.py always connects with sslmode=disable regardless of any
-- LOCAL_POSTGRES_SSLMODE value — that setting only matters for the app's own
-- Neon connection (POSTGRES_SSLMODE), not this test database.

SELECT 'CREATE DATABASE jha_test_db'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'jha_test_db')
\gexec
