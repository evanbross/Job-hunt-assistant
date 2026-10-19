# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""CI-only counterpart to scripts/setup_test_db.sql: creates the non-superuser
jha_test_app role and a fresh jha_test_db owned by it, against the pytest job's
throwaway postgres service container.

The service container's own bootstrap role (POSTGRES_USER, default "postgres") is a
superuser -- that is simply how the official postgres Docker image's bootstrap role
works, name aside -- and Postgres superusers bypass row-level security unconditionally
(backend/db/row_level_security.py). Running the suite as that role would make every RLS
test pass or fail regardless of whether the policies actually work, so the pytest job
runs this first and points TEST_DATABASE_URL at jha_test_app instead.

    python scripts/provision_ci_test_role.py <superuser_dsn> <app_password>
"""

from __future__ import annotations

import sys

import psycopg2
from psycopg2 import sql


def provision(superuser_dsn: str, app_password: str) -> None:
    conn = psycopg2.connect(superuser_dsn)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'jha_test_app'")
        verb = sql.SQL("ALTER" if cur.fetchone() else "CREATE")
        cur.execute(
            sql.SQL(
                "{verb} ROLE jha_test_app WITH LOGIN PASSWORD %s"
                " CREATEROLE NOSUPERUSER NOBYPASSRLS"
            ).format(verb=verb),
            (app_password,),
        )
        # A fresh database rather than reassigning objects on an existing one: the
        # service container is created new for every job, so there is never anything
        # here worth preserving, and REASSIGN OWNED BY can fail on the bootstrap role's
        # cluster-level objects ("required by the database system").
        cur.execute("DROP DATABASE IF EXISTS jha_test_db")
        cur.execute("CREATE DATABASE jha_test_db OWNER jha_test_app")
    finally:
        conn.close()

    db_dsn = superuser_dsn.rsplit("/", 1)[0] + "/jha_test_db"
    conn = psycopg2.connect(db_dsn)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute("GRANT ALL ON SCHEMA public TO jha_test_app")
        cur.execute("GRANT ALL PRIVILEGES ON DATABASE jha_test_db TO jha_test_app")
    finally:
        conn.close()


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    provision(argv[1], argv[2])
    print("jha_test_app provisioned; jha_test_db owned by it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
