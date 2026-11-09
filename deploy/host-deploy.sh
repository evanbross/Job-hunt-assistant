#!/bin/sh
# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================
#
# ai: claude-opus-5-5 | 2026-11-08
# One deploy, run on the server from its deploy directory (docs/cloud-deployment-
# readiness-plan.md 4.4). `scripts/release.py deploy` uploads this file, the compose file
# and the Caddyfile from the image's own commit, then runs it:
#
#   sh host-deploy.sh <image reference>
#
# In order, stopping at the first failure:
#   1. pull the image
#   2. back up the database (with the new image), so a bad migration can be undone
#   3. migrate (the new image's migrate; serving processes refuse an unmigrated schema)
#   4. start the new containers and wait until every health check passes
#   5. record the image in .env.release (JHA_IMAGE), the previous one in .env.release.previous
#
# Rolling back is deploying the previous image the same way: its migrate re-records its
# own schema version. A release whose migration removed something the old code needs
# can't be rolled back that way; restore the step-2 backup instead (deploy/README.md).
set -eu

image="${1:?usage: sh host-deploy.sh <image reference>}"
cd "$(dirname "$0")"

for required in docker-compose.prod.yml Caddyfile .env.production .env.migrate .env.backup \
  secrets/redis-tls/ca.crt; do
  if [ ! -e "$required" ]; then
    echo "host-deploy: $(pwd)/$required is missing (deploy/README.md \"Setting it up\")" >&2
    exit 1
  fi
done

# One deploy at a time: two interleaved would migrate and start different images.
if ! mkdir .deploy.lock 2>/dev/null; then
  echo "host-deploy: another deploy is running (or one crashed: remove $(pwd)/.deploy.lock)" >&2
  exit 1
fi

next_release=.env.release.next
stage=pull

on_exit() {
  status=$?
  rm -rf .deploy.lock
  rm -f "$next_release"
  [ "$status" -eq 0 ] && return
  case "$stage" in
    pull|backup)
      echo "host-deploy: stopped at $stage; nothing changed, the running containers are untouched." >&2
      ;;
    *)
      echo "host-deploy: stopped at $stage, after the database was migrated for $image." >&2
      echo "  The old containers keep running until they restart, then refuse the new schema." >&2
      echo "  Fix forward, or roll back by deploying the image in .env.release." >&2
      ;;
  esac
}
trap on_exit EXIT

compose() {
  docker compose -f docker-compose.prod.yml --env-file .env.production --env-file "$next_release" "$@"
}

echo "==> pull $image"
docker pull "$image"
printf 'JHA_IMAGE=%s\n' "$image" > "$next_release"

stage=backup
echo "==> back up the database"
compose run --rm maintenance backup run

stage=migrate
echo "==> migrate"
compose run --rm migrate

stage=start
echo "==> start, and wait for every health check"
compose up -d --remove-orphans --wait --wait-timeout 300

if [ -f .env.release ]; then
  mv .env.release .env.release.previous
fi
mv "$next_release" .env.release
echo "==> deployed $image"
