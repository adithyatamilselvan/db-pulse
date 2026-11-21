#!/usr/bin/env bash
# Linux/macOS equivalent of dev_mongo.ps1: internal DB on 27117, monitored DB on 27118.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
for spec in "27117:mongo-internal" "27118:mongo-monitored"; do
  port="${spec%%:*}"; name="${spec##*:}"
  mkdir -p "$root/.dev/$name"
  if ! (echo > /dev/tcp/127.0.0.1/$port) 2>/dev/null; then
    mongod --dbpath "$root/.dev/$name" --port "$port" --bind_ip 127.0.0.1 \
      --logpath "$root/.dev/$name.log" --fork
  else echo "mongod already listening on $port"; fi
done
