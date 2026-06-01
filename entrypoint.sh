#!/bin/bash
# Dump container env vars so system cron jobs inherit them.
# (System cron starts with a bare environment; this bridges the gap.)
printenv | grep -v '^_=' | grep -v '^SHLVL=' >> /etc/environment
exec "$@"
