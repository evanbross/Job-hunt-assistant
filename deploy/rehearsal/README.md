# Local production rehearsal

Runs the real `deploy/docker-compose.prod.yml` and `deploy/Caddyfile` on this PC, in
hosted mode, so problems a server would hit show up before paying for one. Use it before
the first deployment and before any release that changes the image, compose file or
Caddyfile.

`docker-compose.rehearsal.yml` is layered on top of the production file, not a copy,
and changes only what a PC can't provide (its header lists each difference and why):
a throwaway Postgres instead of Neon, certificates from Caddy's own CA instead of Let's
Encrypt, port 443 on loopback only, and a made-up domain.

## One-time setup

1. **Hosts file.** Add to `C:\Windows\System32\drivers\etc\hosts`, as Administrator
   (if it's read-only, e.g. from Spybot, run `attrib -r` on it first and `attrib +r` after):

   ```
   # JHA local production rehearsal — remove when done
   127.0.0.1 rehearsal.zombietech.biz
   127.0.0.1 listener.rehearsal.zombietech.biz
   127.0.0.1 listener-ws.rehearsal.zombietech.biz
   ```

   Then `ipconfig /flushdns`. Hosted mode refuses `localhost`, and the extension needs
   the `listener.` and `listener-ws.` names, as on a server.

2. **Throwaway secrets** in this folder (all git-ignored): `.env.production`,
   `.env.migrate`, `.env.backup`, `.env.release` (`JHA_IMAGE=jha:rehearsal`), and
   `.env.rehearsal` (the Postgres passwords; passed to compose for interpolation only,
   never to a container). Fresh values, never production's. Redis TLS:
   `uv run python scripts/generate_redis_tls.py --out deploy/rehearsal/secrets/redis-tls`.
   Backups go to the dev stack's MinIO (`../infra`) in bucket `jha-rehearsal-backups`,
   encrypted to a throwaway age key.

## Each rehearsal

All commands from the repository root. To shorten them:

```bash
R=deploy/rehearsal
dc() { docker compose -f deploy/docker-compose.prod.yml -f $R/docker-compose.rehearsal.yml \
  --project-directory $R --env-file $R/.env.production --env-file $R/.env.rehearsal \
  --env-file $R/.env.release "$@"; }
```

1. Build the image from the working tree: `docker build -t jha:rehearsal .`
   (A real release builds from a tested, pushed commit with `scripts/release.py build`.)
2. Migrate, as a deploy does first: `dc --profile migrate run --rm migrate`. On an empty
   database it prints the bootstrap admin's one-time password (`admin@localhost`).
3. Start everything and wait for the health checks: `dc up -d --wait`.
4. Trust Caddy's local CA, once per fresh `caddy_data` volume (the browser and the
   extension reject its certificates otherwise; HSTS means no click-through):

   ```bash
   docker cp jha-rehearsal-caddy-1:/data/caddy/pki/authorities/local/root.crt "$TEMP/jha-rehearsal-root.crt"
   certutil -user -addstore Root "$TEMP/jha-rehearsal-root.crt"
   ```

5. Open `https://rehearsal.zombietech.biz`, log in as the bootstrap admin, and go
   through the checklist below.

Logs: `dc logs -f <service>`. Status: `dc ps`.

## Checklist

- Bootstrap admin login forces a real email and password; the SMTP form saves.
- Settings > System: SMTP saves; desktop-only settings (App Base URL, listener ports) are absent.
- Invite an address, register with it, receive the email, set a password, log in.
- Settings shows no desktop-only fields (save folder, notify email addresses).
- Cover letter / resume save downloads a file instead of writing to a server folder
  (needs an AI key on the account to generate a letter first).
- Browser extension pointed at `https://listener.rehearsal.zombietech.biz` connects
  and imports a job. Load it unpacked in a separate browser profile (Brave: ☰ menu >
  Profiles > Add new profile, then switch to it there): its settings are per profile
  and synced, so pointing your everyday copy here disconnects it from your own install.
- Gmail connect, if `https://rehearsal.zombietech.biz/api/auth/google/callback` is
  registered on the OAuth client.
- `dc logs maintenance` shows the backup check passing; a backup lands in MinIO.
- `dc restart app`: sessions survive, and the page reconnects.

## Cleanup

```bash
dc --profile migrate down -v        # containers plus the database, Redis and Caddy volumes
certutil -user -delstore Root "Caddy Local Authority - <year> ECC Root"
```

`certutil -user -store Root Caddy` shows the exact name. Remove the hosts-file lines
too. Leave the CA trusted only while rehearsing: anyone holding that CA's private key
(in the `caddy_data` volume) could impersonate any site to this PC.
