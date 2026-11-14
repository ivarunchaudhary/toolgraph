# Single-container HydraDB for PaaS hosts (Railway): the published image, unmodified, plus a launcher
# that writes the auth token from an env var and runs graph-indexer next to graph-node.
FROM ghcr.io/hydra-db/hydradb:0.3.0
USER 0:0
COPY start.sh /usr/local/bin/toolgraph-hydradb-start
ENV CLOUD_PROVIDER=local \
    LOCAL_PATH=/var/lib/hydradb/store \
    GRAPH_NAMESPACE=default GRAPH_ID=default GRAPH_DATABASE=default \
    GRAPH_CELL_ID=cell-0 GRAPH_CELLS=cell-0 \
    GRAPH_DATA_PATH=slatedb-graph/data \
    GRAPH_DATA_CACHE_MAX_OPEN_FILES=512 \
    GRAPH_AUTH_TOKEN_FILE=/run/hydradb/auth-token \
    GRAPH_ALLOW_PLAINTEXT=true \
    GRAPH_BOLT_ADDR=0.0.0.0:7687 GRAPH_HTTP_ADDR=[::]:8443 GRAPH_ADMIN_ADDR=0.0.0.0:9090 \
    GRAPH_NODE_ID=node-0 GRAPH_ADVERTISED_BOLT_ADDR=127.0.0.1:7687 GRAPH_BOLT_NODE_ADDRESSES=node-0=127.0.0.1:7687 \
    GRAPH_INDEXER_ADMIN_ADDR=0.0.0.0:9091 \
    GRAPH_INDEXER_NOTIFY_URL=http://127.0.0.1:9091/v1/changes:process \
    GRAPH_INDEXER_INTERVAL_MS=2000 GRAPH_INDEXER_DIRTY_POLL_INTERVAL_MS=250 GRAPH_INDEXER_BUILD_MODE=full \
    GRAPH_WAL_FLUSH_INTERVAL_MS=50 RUST_MIN_STACK=33554432 RUST_LOG=info
EXPOSE 8443 7687
ENTRYPOINT ["/bin/sh", "/usr/local/bin/toolgraph-hydradb-start"]
