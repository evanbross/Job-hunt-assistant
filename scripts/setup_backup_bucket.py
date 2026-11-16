# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-07
"""Set up the off-site backup bucket and write deploy/.env.backup (deploy/README.md "Backups and restore").

    uv run python scripts/setup_backup_bucket.py

Interactive; secrets are typed at hidden prompts, never passed as arguments (which shell
history and process lists keep). It:

1. creates the bucket with an admin key (used only here; delete it afterwards),
2. sets a lifecycle rule expiring backups after N days (where the provider's S3 API
   supports it; otherwise it says where to set it),
3. generates an age key pair with age-keygen, if you don't already have a public key,
4. checks the server's own key can write to the bucket, and
5. writes deploy/.env.backup with the server's key and the public key.

Object lock is a dashboard step on every provider; the script prints where.
Validation and the S3 client are backend/db/backup.py's own (BackupSettings), so what
this writes is exactly what the maintenance worker will accept.
"""

from __future__ import annotations

import argparse
import getpass
import shutil
import subprocess  # noqa: S404 - runs age-keygen with a fixed argument list
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db.backup import (  # noqa: E402
    BACKUP_ENV_FILE,
    BackupConfigError,
    BackupSettings,
)

ENV_FILE = BACKUP_ENV_FILE  # ai: claude-opus-5-5 | 2026-11-16 -- one definition, shared with app.py
DEFAULT_RETENTION_DAYS = 35
DEFAULT_IDENTITY_FILE = Path.home() / "jha-backup-identity.txt"
_UNSUPPORTED = {"NotImplemented", "NotImplementedError", "InvalidRequest", "MethodNotAllowed"}
# ai: claude-opus-5-5 | 2026-11-07
# A HEAD request's 403 carries no reason, so say what it usually means.
_FORBIDDEN = {"403", "AccessDenied", "Forbidden", "SignatureDoesNotMatch", "InvalidAccessKeyId"}
_FORBIDDEN_HINT = (
    "The key was refused. Usually one of:\n"
    "  - the secret pasted is not the key's Secret Access Key (on R2 that's 64 hex characters,"
    " not the ~40-character 'Token value'),\n"
    "  - the admin key is limited to specific buckets and the bucket doesn't exist yet"
    " (create it in the dashboard, or give the admin key all buckets),\n"
    "  - the server key is scoped to a different bucket than the one you named."
)


@dataclass(frozen=True)
class Provider:
    label: str
    endpoint: Callable[[str], str | None]  # from the account id / region the user gives
    asks_for: str
    default_region: str | None
    lock_hint: str
    server_key_hint: str  # ai: claude-opus-5-5 | 2026-11-07


PROVIDERS: dict[str, Provider] = {
    "r2": Provider(
        "Cloudflare R2", lambda account: f"https://{account}.r2.cloudflarestorage.com",
        "Cloudflare account ID (R2 overview page, right-hand side)", "auto",
        "Cloudflare dashboard > R2 > the bucket > Settings > Bucket lock rules: add a rule"
        " retaining objects for at least 7 days.",
        "an Account API token with Object Read & Write, applied to this bucket only",
    ),
    "b2": Provider(
        "Backblaze B2", lambda region: f"https://s3.{region}.backblazeb2.com",
        "bucket region, e.g. us-west-004 (shown as the S3 endpoint's middle part)", None,
        "Backblaze: Object Lock can only be turned on when a bucket is created. If you want it,"
        " create the bucket in the web UI with Object Lock enabled before running this script.",
        "an application key limited to this bucket, with writeFiles only",
    ),
    "s3": Provider(
        "Amazon S3", lambda _region: None, "AWS region, e.g. us-east-1", None,
        "AWS: Object Lock must be enabled at bucket creation (S3 console > Create bucket >"
        " Advanced settings) -- create it there first if you want it.",
        "an IAM user allowed only s3:PutObject on this bucket's key prefix",
    ),
}


def ensure_bucket(client: Any, bucket: str, region: str | None) -> bool:
    """Create `bucket` unless it already exists. True if it was created."""
    try:
        client.head_bucket(Bucket=bucket)
        return False
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in {"404", "NoSuchBucket", "NotFound"}:
            raise
    kwargs: dict[str, Any] = {"Bucket": bucket}
    if region and region not in {"auto", "us-east-1"}:
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    client.create_bucket(**kwargs)
    return True


def set_retention(client: Any, bucket: str, prefix: str, days: int) -> bool:
    """Expire backups under `prefix` after `days`. False if the provider's S3 API
    doesn't support lifecycle rules (set it in its dashboard instead)."""
    try:
        client.put_bucket_lifecycle_configuration(
            Bucket=bucket,
            LifecycleConfiguration={"Rules": [{
                "ID": "expire-jha-backups",
                "Status": "Enabled",
                "Filter": {"Prefix": prefix},
                "Expiration": {"Days": days},
                "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
            }]},
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in _UNSUPPORTED:
            return False
        raise
    return True


def check_server_can_write(settings: BackupSettings) -> str:
    """Write a small marker object with the server's key; returns its key. It isn't a
    .dump.age file, so `backup list` ignores it, and the lifecycle rule expires it."""
    key = f"{settings.s3_prefix}setup-check-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.txt"
    settings.s3_client().put_object(Bucket=settings.s3_bucket, Key=key, Body=b"backup setup check\n")
    return key


def generate_identity(path: Path) -> str:
    """Run age-keygen to write a new identity at `path`; returns its public key."""
    if path.exists():
        raise FileExistsError(f"{path} already exists; not overwriting a key that may protect backups.")
    keygen = shutil.which("age-keygen")
    if keygen is None:
        raise FileNotFoundError(
            "age-keygen is not installed. Windows: `winget install FiloSottile.age`; macOS:"
            " `brew install age`; Debian/Ubuntu: `apt install age`. Then run this again."
        )
    result = subprocess.run(  # noqa: S603 - fixed argument list
        [keygen, "-o", str(path)], capture_output=True, text=True, check=True
    )
    for line in (result.stderr + result.stdout).splitlines():
        if line.lower().startswith("public key:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError(f"age-keygen did not print a public key: {result.stderr}")


def env_file_text(settings: BackupSettings) -> str:
    """deploy/.env.backup for `settings`: one BACKUP_* line per BackupSettings field."""
    lines = [
        "# Written by scripts/setup_backup_bucket.py. Only the maintenance service reads this.",
        "# See deploy/.env.backup.example for what each setting means.",
    ]
    for name in BackupSettings.model_fields:
        value = getattr(settings, name)
        if value is None:
            continue
        if isinstance(value, tuple):
            value = " ".join(value)
        lines.append(f"BACKUP_{name.upper()}={value}")
    return "\n".join(lines) + "\n"


def write_env_file(path: Path, settings: BackupSettings, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists.")
    path.write_text(env_file_text(settings), encoding="utf-8", newline="\n")
    path.chmod(0o600)  # owner-only where the filesystem supports it


# ── interactive layer ───────────────────────────────────────────────────────────


def _ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        answer = input(f"{prompt}{suffix}: ").strip() or (default or "")
        if answer:
            return answer


def _ask_secret(prompt: str) -> str:
    while True:
        answer = getpass.getpass(f"{prompt} (hidden): ").strip()
        if answer:
            return answer


def _yes(prompt: str, default: bool) -> bool:
    answer = input(f"{prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not answer else answer.startswith("y")


def run(args: argparse.Namespace) -> int:
    provider = PROVIDERS[args.provider]
    print(f"Setting up an off-site backup bucket on {provider.label}.\n")
    where = _ask(provider.asks_for)
    region = provider.default_region or where
    endpoint = provider.endpoint(where)
    bucket = _ask("Bucket name", "jha-backups")
    prefix = _ask("Key prefix inside the bucket", "jha-backups/")

    print("\nThe ADMIN key creates the bucket and its retention rule. It's used only now;"
          " delete it in the provider's dashboard afterwards.")
    admin = BackupSettings(
        s3_bucket=bucket, s3_endpoint_url=endpoint, s3_region=region, s3_prefix=prefix,
        s3_access_key_id=_ask("Admin access key ID"), s3_secret_access_key=_ask_secret("Admin secret access key"),
    )
    client = admin.s3_client()
    print(f"Bucket {bucket}: {'created' if ensure_bucket(client, bucket, region) else 'already exists'}.")
    if set_retention(client, bucket, admin.s3_prefix, args.retention_days):
        print(f"Retention: backups expire after {args.retention_days} days.")
    else:
        print(f"Retention: this provider's S3 API doesn't take lifecycle rules -- set one in its"
              f" dashboard (delete objects under {admin.s3_prefix} after {args.retention_days} days).")

    recipients = args.recipient
    if not recipients:
        identity = Path(_ask("\nWhere to write the new age PRIVATE key (keep it off the server)",
                             str(DEFAULT_IDENTITY_FILE)))
        recipients = [generate_identity(identity)]
        print(f"Wrote the private key to {identity}.\n"
              "  -> Copy it into your password manager (and a sealed offline copy), then delete"
              " the file.\n  -> Without it the backups can never be decrypted.")

    # ai: claude-opus-5-5 | 2026-11-07
    print(f"\nThe SERVER key is what the maintenance worker uses. Make it {provider.server_key_hint}"
          " -- a separate key from the admin one.")
    try:
        server = BackupSettings(
            s3_bucket=bucket, s3_endpoint_url=endpoint, s3_region=region, s3_prefix=prefix,
            s3_access_key_id=_ask("Server access key ID"),
            s3_secret_access_key=_ask_secret("Server secret access key"),
            age_recipients=tuple(recipients), interval_hours=args.interval_hours,
        )
    except ValueError as exc:  # pydantic's ValidationError is a ValueError
        raise BackupConfigError(str(exc)) from exc
    if server.s3_access_key_id == admin.s3_access_key_id:
        print("Warning: the server key is the admin key. Make a narrower one for the server.")
    print(f"Server key can write: wrote {check_server_can_write(server)}.")

    overwrite = not ENV_FILE.exists() or _yes(f"{ENV_FILE} exists. Overwrite it?", default=False)
    if not overwrite:
        print("Left deploy/.env.backup unchanged.")
        return 1
    write_env_file(ENV_FILE, server, overwrite=True)
    print(f"\nWrote {ENV_FILE} (git-ignored).\n\nStill to do by hand:\n"
          f"  1. {provider.lock_hint}\n"
          "  2. Delete the admin key in the provider's dashboard.\n"
          "  3. Store the age private key and SETTINGS_ENCRYPTION_KEY together, away from the"
          " server and the bucket.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("provider", choices=sorted(PROVIDERS), help="r2 (Cloudflare), b2 (Backblaze), s3 (AWS)")
    parser.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    parser.add_argument("--interval-hours", type=float, default=24.0)
    parser.add_argument("--recipient", action="append", default=[],
                        help="an existing age public key (age1...) instead of generating one; repeatable")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except (BackupConfigError, FileExistsError, FileNotFoundError, ClientError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        # ai: claude-opus-5-5 | 2026-11-07
        if isinstance(exc, ClientError) and exc.response.get("Error", {}).get("Code") in _FORBIDDEN:
            print(_FORBIDDEN_HINT, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
