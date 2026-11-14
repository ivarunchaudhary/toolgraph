#!/bin/sh
set -eu
: "${HYDRADB_AUTH_TOKEN:?set HYDRADB_AUTH_TOKEN (>= 32 random chars)}"
mkdir -p /run/hydradb "$LOCAL_PATH" /var/cache/slatedb/data /var/cache/idx/data
printf '%s' "$HYDRADB_AUTH_TOKEN" > /run/hydradb/auth-token
chmod 400 /run/hydradb/auth-token
GRAPH_DATA_CACHE_DIR=/var/cache/idx/data /usr/local/bin/graph-indexer &
IDX=$!
trap 'kill $IDX 2>/dev/null' EXIT INT TERM
GRAPH_DATA_CACHE_DIR=/var/cache/slatedb/data exec /usr/local/bin/graph-node
