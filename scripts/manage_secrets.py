# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Generate and rotate the app's bootstrap secrets.

    python scripts/manage_secrets.py generate
    python scripts/manage_secrets.py rotate-flask-secret [--write]
    python scripts/manage_secrets.py rotate-encryption-key [--write]
    python scripts/manage_secrets.py check-encryption

The rotate commands preview by default and change nothing until --write is
given. The old value is always kept as a fallback (see
docs/secrets-and-key-rotation.md)."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from dotenv import load_dotenv, set_key

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backend.crypto as crypto  # noqa: E402
import config  # noqa: E402
from backend.db import get_connection  # noqa: E402
from backend.security.key_rotation import RotationError, rotate_all  # noqa: E402
from backend.security.rotation_plan import (  # noqa: E402
    plan_encryption_key_rotation,
    plan_flask_secret_rotation,
)
from backend.security.secrets_policy import (  # noqa: E402
    ENCRYPTION_KEY_ENV,
    FLASK_SECRET_ENV,
    generate_encryption_key,
    generate_flask_secret,
)

DEFAULT_ENV_FILE = config.BASE_DIR / ".env"


def _write_env(env_file: Path, updates: Mapping[str, str]) -> None:
    env_file.touch(exist_ok=True)
    for name, value in updates.items():
        set_key(str(env_file), name, value)


def _use_in_process(updates: Mapping[str, str]) -> None:
    """Make this process see the new keys, exactly as the app will after a restart."""
    os.environ.update(updates)
    crypto._fernet = None  # noqa: SLF001 - drop the cached keys so they are re-read


def _print_report(report_scanned: dict[str, int], report_rotated: dict[str, int], verb: str) -> None:
    for label, scanned in report_scanned.items():
        print(f"  {label}: {report_rotated.get(label, 0)} of {scanned} {verb}")


def cmd_generate(_args: argparse.Namespace) -> int:
    print(f"{FLASK_SECRET_ENV}={generate_flask_secret()}")
    print(f"{ENCRYPTION_KEY_ENV}={generate_encryption_key()}")
    return 0


def cmd_rotate_flask(args: argparse.Namespace) -> int:
    load_dotenv(args.env_file)
    try:
        updates = plan_flask_secret_rotation(os.environ)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not args.write:
        print("Preview only. --write would set a new FLASK_SECRET_KEY and keep the current one")
        print("in FLASK_SECRET_KEY_FALLBACKS so existing sessions stay valid.")
        return 0
    _write_env(args.env_file, updates)
    print(f"Updated {args.env_file}. Restart the app to use the new secret.")
    print("After your session lifetime has passed (30 days by default), remove")
    print("FLASK_SECRET_KEY_FALLBACKS to retire the old secret.")
    return 0


def cmd_rotate_encryption(args: argparse.Namespace) -> int:
    load_dotenv(args.env_file)
    try:
        updates = plan_encryption_key_rotation(os.environ)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.write:
        # The environment is updated BEFORE the database. If re-encryption then
        # fails, the new primary plus the old fallback still read everything.
        _write_env(args.env_file, updates)
    _use_in_process(updates)

    # ai: claude-opus-5-5 | 2026-11-06 -- rotate_all re-encrypts every account's secrets,
    # not just one, so it connects as the schema owner, which row-level security exempts
    # (backend/db/row_level_security.py) -- RLS is no longer switched off for it.
    conn = get_connection(config.DATABASE_OWNER_URL)
    try:
        try:
            report = rotate_all(conn, dry_run=not args.write)
            if args.write:
                conn.commit()
            else:
                conn.rollback()
        except RotationError as exc:
            conn.rollback()
            print(f"error: {exc}", file=sys.stderr)
            return 1
    finally:
        conn.close()

    if args.write:
        print(f"Updated {args.env_file}. Re-encrypted under the new key:")
        _print_report(report.scanned, report.rotated, "re-encrypted")
        print("Restart the app (and any Celery worker) so it picks up the new key.")
        print("Then run `check-encryption`; once it reports nothing left, remove")
        print("SETTINGS_ENCRYPTION_KEY_FALLBACKS to retire the old key.")
    else:
        print("Preview only. --write would set a new SETTINGS_ENCRYPTION_KEY, keep the current one")
        print("as a fallback, and re-encrypt the stored values:")
        _print_report(report.scanned, report.rotated, "to re-encrypt")
    return 0


def cmd_check_encryption(args: argparse.Namespace) -> int:
    load_dotenv(args.env_file)
    conn = get_connection(config.DATABASE_OWNER_URL)  # scans every account's secrets
    try:
        try:
            report = rotate_all(conn, dry_run=True)
        except RotationError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        finally:
            conn.rollback()
    finally:
        conn.close()
    _print_report(report.scanned, report.rotated, "still under an older key")
    if report.total_rotated:
        print("Some values are still encrypted under an older key; keep the fallback.")
        return 1
    print("Everything is encrypted under the current key; the fallback can be removed.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE, help="path to the .env file")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("generate", help="print a new FLASK_SECRET_KEY and SETTINGS_ENCRYPTION_KEY")
    for name in ("rotate-flask-secret", "rotate-encryption-key"):
        rotate = commands.add_parser(name)
        rotate.add_argument("--write", action="store_true", help="apply the change (default: preview)")
    commands.add_parser("check-encryption", help="report values not yet under the current key")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "generate": cmd_generate,
        "rotate-flask-secret": cmd_rotate_flask,
        "rotate-encryption-key": cmd_rotate_encryption,
        "check-encryption": cmd_check_encryption,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
