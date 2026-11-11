# Distributed Key-Value Store (Java 21 + gRPC + WAL + Docker)

A Raft-inspired, 3-node distributed key-value store supporting `PUT`, `GET`, and `DELETE` over gRPC with durable binary Write-Ahead Logging (WAL), majority (2/3) commit quorum, crash recovery, follower catch-up, and idempotent client retries.

## Architecture Overview

- **Static 3-Node Cluster** (`raft-kv-store/compose.yaml`):
  - `node-1` (`n1`): Configured Leader (`127.0.0.1:50051`)
  - `node-2` (`n2`) & `node-3` (`n3`): Followers (`50051` on internal Docker bridge network)
- **Write & Commit Invariant**:
  1. Leader checks `RequestDeduplicator` using client-supplied `request_id` (returns existing `commit_index` for exact retries; rejects mismatched payload reuse with `REQUEST_ID_CONFLICT`).
  2. Leader appends the entry to its local WAL (`FileWalStore`, CRC32C-framed records + `FileChannel.force(true)`).
  3. Leader replicates via gRPC (`ReplicationService`) to followers.
  4. Once at least **2 of 3 nodes** durably persist the entry, the leader commits, applies to `KvStateMachine`, and returns `OK`.
- **Fault Tolerance & Recovery**:
  - Survives single-follower crashes (`SIGKILL`) while maintaining 2/3 write quorum; restarted followers automatically catch up.
  - Rejects writes with `NO_QUORUM` / `NOT_READY` if majority is lost.
  - Rejects direct client reads/writes on followers with `NOT_LEADER` + `LeaderHint`.
  - Non-empty recovered leader requires follower witness verification (`RecoveryManager`) before serving traffic.

## Quick Start (macOS / Linux)

Requires Docker Desktop (or Docker Engine + Compose v2) and Python 3 (for the optional live web console).

```bash
# 1. Build and start the 3-node cluster
./live-demo/demo.sh up

# 2. Check node health and gRPC replication status
./live-demo/demo.sh status

# 3. Run the automated gRPC smoke check
./live-demo/demo.sh smoke

# 4. Launch the interactive Live Demo Web Console (http://127.0.0.1:8765)
./live-demo/demo.sh ui

# 5. Stop containers cleanly (preserves named WAL volumes)
./live-demo/demo.sh down
```

## Quick Start (Windows PowerShell)

Run from `raft-kv-store/`:

```powershell
.\scripts\cluster-up.ps1
.\scripts\cluster-status.ps1
.\scripts\smoke-test.ps1
.\scripts\deployment-acceptance.ps1
```

## Repository Layout

- `raft-kv-store/` — Java 21 / Maven / gRPC / Protobuf implementation, Dockerfile, `compose.yaml`, and PowerShell scripts
- `raft_kv_design/` — Architecture, API, Storage, Scope, and Test design specs
- `IMPLEMENTATION_ROADMAP.md` — Bottom-up phased build strategy
- `live-demo/` — macOS Bash helper (`demo.sh`) and zero-dependency local web console (`server.py`) backed by real Docker + `DeploymentSmokeClient` RPCs

