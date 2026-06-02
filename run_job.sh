#!/bin/bash
# Wrapper for cron jobs inside Docker slim containers.
# Debian cron in slim images doesn't source /etc/environment via PAM,
# so env vars written by entrypoint.sh (CSR_DSN, API keys, etc.) are
# invisible to cron without this bridge.
set -a
. /etc/environment
set +a
exec "$@"
