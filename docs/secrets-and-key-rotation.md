# Secrets and key rotation

Two secrets can never live in the database they protect, so they are supplied
through the environment (`.env` locally, a secrets manager in production). The
app **refuses to start** if either is missing, copied from an example, or weak.

| Variable | What it does | Requirement |
|---|---|---|
| `FLASK_SECRET_KEY` | Signs session cookies | At least 32 characters, not a placeholder, not repetitive |
| `SETTINGS_ENCRYPTION_KEY` | Encrypts stored API keys, Google/Greenhouse tokens, resume PDFs, the Google OAuth client config | A valid Fernet key |

Generate both with:

```bash
python scripts/manage_secrets.py generate
```

The Celery worker also refuses to start without a valid `SETTINGS_ENCRYPTION_KEY`.

## Environments

`APP_ENV` is `production` by default. In production a missing or weak secret is
a startup error that lists every problem.

`APP_ENV=development` (opt in, local use only) additionally lets a **missing**
`FLASK_SECRET_KEY` be generated once and saved to `.env`. It never overwrites a
weak value you set on purpose, and it never generates `SETTINGS_ENCRYPTION_KEY`:
that key protects data that already exists, and a silently minted replacement
would make everything encrypted under the lost one unreadable.

Back up `SETTINGS_ENCRYPTION_KEY` with your database credentials. Losing it
makes every stored secret unrecoverable.

## Session cookies

The session cookie is always `HttpOnly` and `SameSite=Lax`. `Secure` follows how
the app is reached:

- `SESSION_COOKIE_SECURE=auto` (default): `Secure` when the app base URL
  (Settings > System, or `APP_BASE_URL`) starts with `https://`.
- `true` / `false` force it.

When the cookie is `Secure` it is also named `__Host-session`, which makes a
browser reject it unless it is `Secure`, `Path=/`, and has no `Domain`, so a
sibling subdomain cannot plant or overwrite it. A browser will not accept a
`Secure` cookie set over plain HTTP from a non-loopback address, which is why
this is derived rather than always on. Serve production over HTTPS and set the
base URL to match; the app logs a warning at startup if it is not.

## Rotating `FLASK_SECRET_KEY`

The old secret is kept as a fallback so people stay signed in.

```bash
python scripts/manage_secrets.py rotate-flask-secret           # preview
python scripts/manage_secrets.py rotate-flask-secret --write   # apply
```

This sets a new `FLASK_SECRET_KEY` and moves the old one to
`FLASK_SECRET_KEY_FALLBACKS` (comma-separated). Restart the app. Sessions signed
with the old secret keep working and are re-signed with the new one as they are
used. After the session lifetime has passed (30 days by default), remove
`FLASK_SECRET_KEY_FALLBACKS` to retire the old secret. A secret that is too weak
to start with (for example, under 32 characters) is exactly what this fixes.

## Rotating `SETTINGS_ENCRYPTION_KEY`

```bash
python scripts/manage_secrets.py rotate-encryption-key           # preview, changes nothing
python scripts/manage_secrets.py rotate-encryption-key --write   # apply
```

`--write` does this in a safe order:

1. Writes the new key as `SETTINGS_ENCRYPTION_KEY` and keeps the old one in
   `SETTINGS_ENCRYPTION_KEY_FALLBACKS`. From this moment everything is readable
   with either key, so a failure later cannot strand data.
2. Re-encrypts every stored ciphertext under the new key in one transaction. If
   any value cannot be read with a configured key it stops, names it, and rolls
   the whole thing back.

Then restart the app and any Celery worker, and confirm the old key is no longer
needed:

```bash
python scripts/manage_secrets.py check-encryption
```

When it reports that everything is under the current key, remove
`SETTINGS_ENCRYPTION_KEY_FALLBACKS`. New values are always encrypted with the
current key; the fallbacks exist only to read older ones.

The re-encryption covers: secret entries in system and user settings (from the
settings registry), resume PDFs, Google tokens, and Greenhouse sessions. Values
that are not secrets are never touched.

## In production

Supply these from your secrets manager as environment variables rather than a
`.env` file, and do not bake them into images. Every process that decrypts
(web app, Celery workers) needs the same `SETTINGS_ENCRYPTION_KEY` and its
fallbacks; every web instance needs the same `FLASK_SECRET_KEY` and fallbacks,
or a session signed by one instance will be rejected by another.
