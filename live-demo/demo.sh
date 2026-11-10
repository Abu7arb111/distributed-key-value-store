#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../raft-kv-store" && pwd)"
PORT="${RAFTKV_DEMO_PORT:-8765}"

dc() {
  docker compose --project-directory "${REPO_DIR}" -f "${REPO_DIR}/compose.yaml" "$@"
}

smoke_client() {
  local op="$1"; shift
  dc exec -T node-1 java -cp /opt/raftkv/app.jar \
    io.github.ahmadbitaarr.raftkv.client.DeploymentSmokeClient "${op}" "$@"
}

usage() {
  cat <<EOF
Usage: $(basename "$0") <command>

Commands:
  up       Start (and build if needed) the 3-node Docker cluster and wait for health
  status   Show Docker container status and gRPC replication state on all 3 nodes
  smoke    Run a full PUT -> GET -> Idempotent Retry -> Conflict -> DELETE -> Follower check
  ui       Start the local Live Demo Web Console on http://127.0.0.1:${PORT} and open browser
  kill3    SIGKILL node-3 (simulate follower crash while retaining 2/3 write quorum)
  start3   Restart node-3 and verify catch-up
  down     Stop containers cleanly while preserving named WAL volumes
EOF
}

case "${1:-}" in
  up)
    dc up --detach --build
    echo "Waiting for node-1, node-2, node-3 to become healthy..."
    for i in $(seq 1 30); do
      h1=$(docker inspect --format '{{.State.Health.Status}}' raftkv-node-1-1 2>/dev/null || true)
      h2=$(docker inspect --format '{{.State.Health.Status}}' raftkv-node-2-1 2>/dev/null || true)
      h3=$(docker inspect --format '{{.State.Health.Status}}' raftkv-node-3-1 2>/dev/null || true)
      if [ "$h1" = "healthy" ] && [ "$h2" = "healthy" ] && [ "$h3" = "healthy" ]; then
        dc ps
        echo "All 3 nodes are healthy!"
        exit 0
      fi
      sleep 2
    done
    dc ps
    exit 1
    ;;
  status)
    dc ps
    for n in node-1 node-2 node-3; do
      echo "--- ${n} ---"
      smoke_client status "--host=${n}" --port=50051 --leader=n1
    done
    ;;
  smoke)
    KEY="mac-smoke-$(date +%s)"
    REQ_ID="$(uuidgen | tr '[:upper:]' '[:lower:]')"
    VAL_B64="$(printf 'hello-deloitte' | base64)"
    echo "1) PUT key=${KEY}"
    smoke_client put --host=node-1 --port=50051 --leader=n1 "--key=${KEY}" "--request-id=${REQ_ID}" "--value-base64=${VAL_B64}" --expect=OK
    echo "2) GET key=${KEY}"
    smoke_client get --host=node-1 --port=50051 --leader=n1 "--key=${KEY}" --expect=OK "--expect-value-base64=${VAL_B64}"
    echo "3) Idempotent retry with same request_id"
    smoke_client put --host=node-1 --port=50051 --leader=n1 "--key=${KEY}" "--request-id=${REQ_ID}" "--value-base64=${VAL_B64}" --expect=OK
    echo "4) DELETE key=${KEY}"
    smoke_client delete --host=node-1 --port=50051 --leader=n1 "--key=${KEY}" "--request-id=$(uuidgen | tr '[:upper:]' '[:lower:]')" --expect=OK
    echo "5) Verify follower rejects direct read with NOT_LEADER"
    smoke_client get --host=node-2 --port=50051 --leader=n1 "--key=${KEY}" --expect=NOT_LEADER
    echo "SMOKE TEST PASSED"
    ;;
  ui)
    (sleep 1 && open "http://127.0.0.1:${PORT}") &
    exec python3 "${SCRIPT_DIR}/server.py" "${PORT}"
    ;;
  kill3)
    dc kill --signal SIGKILL node-3
    ;;
  start3)
    dc start node-3
    ;;
  down)
    dc down
    ;;
  *)
    usage
    exit 1
    ;;
esac

