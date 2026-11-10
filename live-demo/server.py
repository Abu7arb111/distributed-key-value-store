#!/usr/bin/env python3
"""
Local Live Demo Console for Ahmad Harb's Phase 12 Raft-KV Store.
Executes real gRPC RPCs (via packaged DeploymentSmokeClient) and real Docker Compose
lifecycle commands against the 3-node cluster (node-1 leader, node-2 & node-3 followers).
"""

import base64
import http.server
import json
import os
import shlex
import socketserver
import subprocess
import sys
import urllib.parse
import uuid
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent / "raft-kv-store"
COMPOSE_FILE = PROJECT_DIR / "compose.yaml"
NODES = ["node-1", "node-2", "node-3"]
NODE_META = {
    "node-1": {"node_id": "n1", "configured_role": "LEADER", "host_port": "127.0.0.1:50051"},
    "node-2": {"node_id": "n2", "configured_role": "FOLLOWER", "host_port": "cluster-internal:50051"},
    "node-3": {"node_id": "n3", "configured_role": "FOLLOWER", "host_port": "cluster-internal:50051"},
}
DEFAULT_PORT = int(os.environ.get("RAFTKV_DEMO_PORT", "8765"))


def run_cmd(args, timeout=30):
    proc = subprocess.run(
        args,
        cwd=str(PROJECT_DIR),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def compose_cmd(*args, timeout=60):
    full = [
        "docker",
        "compose",
        "--project-directory",
        str(PROJECT_DIR),
        "-f",
        str(COMPOSE_FILE),
        *args,
    ]
    code, out, err = run_cmd(full, timeout=timeout)
    return code, out, err, " ".join(shlex.quote(a) for a in full)


def get_container_state(node):
    if node not in NODES:
        raise ValueError(f"Invalid node: {node}")
    code, cid, err, _ = compose_cmd("ps", "-q", "--all", node, timeout=10)
    if code != 0 or not cid:
        return {"exists": False, "status": "not_created", "health": "none", "container_id": None}
    cid = cid.splitlines()[0].strip()
    icode, iout, _ = run_cmd(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.State.StartedAt}}",
            cid,
        ],
        timeout=10,
    )
    if icode != 0 or "|" not in iout:
        return {"exists": True, "status": "unknown", "health": "unknown", "container_id": cid[:12]}
    parts = iout.split("|", 2)
    return {
        "exists": True,
        "status": parts[0],
        "health": parts[1],
        "started_at": parts[2] if len(parts) > 2 else "",
        "container_id": cid[:12],
    }


def pick_runner(preferred="node-1"):
    order = [preferred] + [n for n in NODES if n != preferred]
    for n in order:
        st = get_container_state(n)
        if st.get("status") == "running":
            return n
    return None


def invoke_smoke_client(operation, target="node-1", key="", request_id="", value_text=None, value_b64=None):
    runner = pick_runner(target)
    if not runner:
        return {
            "ok": False,
            "error": "No running container available in cluster. Click 'Start Cluster' first.",
            "command": "",
        }
    args = [
        "exec",
        "-T",
        runner,
        "java",
        "-cp",
        "/opt/raftkv/app.jar",
        "io.github.ahmadbitaarr.raftkv.client.DeploymentSmokeClient",
        operation,
        f"--host={target}",
        "--port=50051",
        "--leader=n1",
    ]
    if key:
        args.append(f"--key={key}")
    if request_id:
        args.append(f"--request-id={request_id}")
    if operation in ("put", "retry-put"):
        if value_b64 is not None:
            b64 = value_b64
        else:
            b64 = base64.b64encode((value_text or "").encode("utf-8")).decode("ascii")
        args.append(f"--value-base64={b64}")

    code, out, err, display_cmd = compose_cmd(*args, timeout=20)
    parsed = None
    if out:
        last_line = out.splitlines()[-1].strip()
        try:
            parsed = json.loads(last_line)
            if parsed.get("value_base64") is not None:
                try:
                    raw = base64.b64decode(parsed["value_base64"])
                    parsed["decoded_value"] = raw.decode("utf-8", errors="replace")
                except Exception:
                    parsed["decoded_value"] = None
        except json.JSONDecodeError:
            parsed = None

    return {
        "ok": code == 0 and parsed is not None,
        "exit_code": code,
        "runner": runner,
        "target": target,
        "command": display_cmd,
        "result": parsed,
        "stdout": out,
        "stderr": err,
    }


def get_cluster_status():
    nodes_info = []
    for n in NODES:
        cstate = get_container_state(n)
        rpc_status = None
        rpc_error = None
        if cstate.get("status") == "running":
            res = invoke_smoke_client("status", target=n)
            if res["ok"]:
                rpc_status = res["result"]
            else:
                rpc_error = res.get("stderr") or res.get("error") or "status RPC failed"
        nodes_info.append(
            {
                "service": n,
                **NODE_META[n],
                "container": cstate,
                "rpc": rpc_status,
                "rpc_error": rpc_error,
            }
        )
    return {"nodes": nodes_info}


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Raft-KV Distributed Store — Live Demo Console</title>
<style>
  :root {
    --bg: #0b1020;
    --panel: #121932;
    --panel-2: #182244;
    --border: #26335d;
    --text: #e8eefc;
    --muted: #94a3c8;
    --accent: #4f8cff;
    --accent-2: #22d3ee;
    --green: #22c55e;
    --yellow: #eab308;
    --red: #ef4444;
    --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: radial-gradient(circle at top right, #16224a 0%, var(--bg) 55%);
    color: var(--text);
    min-height: 100vh;
  }
  header {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 18px 28px;
    border-bottom: 1px solid var(--border);
    background: rgba(11, 16, 32, 0.85);
    backdrop-filter: blur(8px);
    position: sticky;
    top: 0;
    z-index: 10;
  }
  .title-block h1 {
    margin: 0;
    font-size: 1.25rem;
    letter-spacing: -0.01em;
  }
  .title-block p {
    margin: 3px 0 0;
    font-size: 0.85rem;
    color: var(--muted);
  }
  .header-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }
  .container {
    max-width: 1320px;
    margin: 0 auto;
    padding: 22px 24px 48px;
    display: grid;
    gap: 20px;
  }
  .btn {
    border: 1px solid var(--border);
    background: var(--panel-2);
    color: var(--text);
    padding: 8px 13px;
    border-radius: 8px;
    font-size: 0.85rem;
    font-weight: 600;
    cursor: pointer;
    transition: transform 0.06s, background 0.15s, border-color 0.15s;
  }
  .btn:hover { background: #213061; border-color: #3b5296; }
  .btn:active { transform: translateY(1px); }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  .btn-primary:hover { background: #3b7bff; }
  .btn-green { background: rgba(34, 197, 94, 0.16); border-color: rgba(34, 197, 94, 0.45); color: #86efac; }
  .btn-red { background: rgba(239, 68, 68, 0.16); border-color: rgba(239, 68, 68, 0.45); color: #fca5a5; }
  .btn-yellow { background: rgba(234, 179, 8, 0.15); border-color: rgba(234, 179, 8, 0.45); color: #fde047; }
  .btn-cyan { background: rgba(34, 211, 238, 0.14); border-color: rgba(34, 211, 238, 0.4); color: #67e8f9; }

  .nodes-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
    gap: 16px;
  }
  .card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px 18px;
    box-shadow: 0 10px 24px rgba(0, 0, 0, 0.25);
  }
  .node-card {
    display: flex;
    flex-direction: column;
    gap: 12px;
    position: relative;
  }
  .node-card.leader { border-color: rgba(79, 140, 255, 0.65); }
  .node-top {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 10px;
  }
  .node-name { font-size: 1.08rem; font-weight: 700; }
  .node-sub { font-size: 0.78rem; color: var(--muted); font-family: var(--mono); margin-top: 2px; }
  .badges { display: flex; gap: 6px; flex-wrap: wrap; }
  .badge {
    font-size: 0.72rem;
    font-weight: 700;
    padding: 3px 8px;
    border-radius: 999px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .badge-leader { background: rgba(79, 140, 255, 0.2); color: #93c5fd; border: 1px solid rgba(79, 140, 255, 0.45); }
  .badge-follower { background: rgba(148, 163, 200, 0.14); color: #cbd5e1; border: 1px solid rgba(148, 163, 200, 0.3); }
  .badge-ok { background: rgba(34, 197, 94, 0.16); color: #86efac; border: 1px solid rgba(34, 197, 94, 0.45); }
  .badge-warn { background: rgba(234, 179, 8, 0.16); color: #fde047; border: 1px solid rgba(234, 179, 8, 0.45); }
  .badge-down { background: rgba(239, 68, 68, 0.16); color: #fca5a5; border: 1px solid rgba(239, 68, 68, 0.45); }

  .metrics {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
  }
  .metric {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 8px 10px;
  }
  .metric-label { font-size: 0.7rem; color: var(--muted); text-transform: uppercase; }
  .metric-val { font-family: var(--mono); font-size: 1.15rem; font-weight: 700; margin-top: 2px; }
  .node-controls { display: flex; gap: 6px; flex-wrap: wrap; }

  .main-grid {
    display: grid;
    grid-template-columns: 1.05fr 1fr;
    gap: 18px;
  }
  @media (max-width: 960px) {
    .main-grid { grid-template-columns: 1fr; }
  }
  .card h2 {
    margin: 0 0 12px;
    font-size: 1.02rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .form-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
  }
  .field { display: flex; flex-direction: column; gap: 5px; }
  .field.full { grid-column: 1 / -1; }
  label { font-size: 0.78rem; color: var(--muted); font-weight: 600; }
  input, select {
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 9px 11px;
    border-radius: 8px;
    font-family: var(--mono);
    font-size: 0.86rem;
  }
  input:focus, select:focus { outline: none; border-color: var(--accent); }
  .op-buttons {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 12px;
  }
  .presets {
    margin-top: 14px;
    padding-top: 12px;
    border-top: 1px solid var(--border);
  }
  .presets-title {
    font-size: 0.78rem;
    color: var(--muted);
    margin-bottom: 8px;
    font-weight: 600;
  }
  pre.terminal {
    background: #060913;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px;
    font-family: var(--mono);
    font-size: 0.8rem;
    line-height: 1.45;
    overflow-x: auto;
    max-height: 360px;
    margin: 0;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .history-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
    max-height: 320px;
    overflow-y: auto;
  }
  .history-item {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 9px 12px;
    font-size: 0.82rem;
  }
  .history-item-head {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    font-family: var(--mono);
    font-weight: 700;
  }
  .history-cmd {
    font-family: var(--mono);
    font-size: 0.72rem;
    color: var(--muted);
    margin-top: 4px;
    word-break: break-all;
  }
  .quorum-banner {
    padding: 10px 14px;
    border-radius: 10px;
    font-size: 0.86rem;
    font-weight: 600;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 10px;
    border: 1px solid var(--border);
    background: var(--panel);
  }
  .quorum-ok { border-color: rgba(34, 197, 94, 0.45); background: rgba(34, 197, 94, 0.08); }
  .quorum-bad { border-color: rgba(239, 68, 68, 0.5); background: rgba(239, 68, 68, 0.1); }
  .interview-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
    gap: 12px;
    font-size: 0.84rem;
    color: #cbd5e1;
  }
  .interview-step {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 11px 13px;
  }
  .interview-step strong { color: var(--accent-2); display: block; margin-bottom: 4px; }
</style>
</head>
<body>
<header>
  <div class="title-block">
    <h1>Distributed Key-Value Store (Java 21 + gRPC + WAL) — Live Demo</h1>
    <p>Ahmad Harb • Phase 12 Docker Compose Cluster • Static Leader (<code>n1</code>) + 2 Followers (<code>n2</code>, <code>n3</code>) • Quorum = 2/3</p>
  </div>
  <div class="header-actions">
    <button class="btn" onclick="refreshStatus()">Refresh Status</button>
    <button class="btn btn-green" onclick="clusterAction('up')">Start / Restore All Nodes</button>
    <button class="btn btn-cyan" onclick="runAutomatedSmoke()">Run Full Smoke Suite</button>
    <label style="display:flex;align-items:center;gap:5px;font-size:0.8rem;cursor:pointer;">
      <input type="checkbox" id="autoPoll" checked onchange="togglePoll()" /> Auto-refresh (3s)
    </label>
  </div>
</header>

<div class="container">
  <div id="quorumBanner" class="quorum-banner">
    <span>Checking 3-node cluster status...</span>
    <span id="lastUpdated" style="font-family:var(--mono);font-size:0.75rem;color:var(--muted);"></span>
  </div>

  <div id="nodesGrid" class="nodes-grid"></div>

  <div class="main-grid">
    <div class="card">
      <h2>
        <span>Interactive gRPC Client (Real <code>DeploymentSmokeClient</code>)</span>
        <button class="btn" style="padding:4px 9px;font-size:0.75rem;" onclick="newRequestId()">New Request ID</button>
      </h2>
      <div class="form-grid">
        <div class="field">
          <label for="keyInput">Key</label>
          <input id="keyInput" value="deloitte-demo" />
        </div>
        <div class="field">
          <label for="targetSelect">Target gRPC Node</label>
          <select id="targetSelect">
            <option value="node-1">node-1 (Leader n1)</option>
            <option value="node-2">node-2 (Follower n2 — rejects writes/reads)</option>
            <option value="node-3">node-3 (Follower n3 — rejects writes/reads)</option>
          </select>
        </div>
        <div class="field full">
          <label for="valueInput">Value (UTF-8, Base64-encoded for gRPC PutRequest)</label>
          <input id="valueInput" value="hello-deloitte-gps" />
        </div>
        <div class="field full">
          <label for="reqIdInput">Idempotency Request ID (reused for deduplication / conflict demos)</label>
          <input id="reqIdInput" />
        </div>
      </div>

      <div class="op-buttons">
        <button class="btn btn-primary" onclick="sendKvOp('put')">PUT</button>
        <button class="btn btn-green" onclick="sendKvOp('get')">GET</button>
        <button class="btn btn-red" onclick="sendKvOp('delete')">DELETE</button>
        <button class="btn btn-cyan" onclick="sendKvOp('retry-put')">Retry PUT (RetryingMutationClient)</button>
      </div>

      <div class="presets">
        <div class="presets-title">One-Click Interview Demo Scenarios:</div>
        <div class="op-buttons" style="margin-top:0;">
          <button class="btn" onclick="demoIdempotency()">1. Retry Same Request ID (Deduplication)</button>
          <button class="btn btn-yellow" onclick="demoConflict()">2. Reuse Request ID w/ New Value (Conflict)</button>
          <button class="btn" onclick="demoFollowerReject()">3. Query Follower Directly (NOT_LEADER)</button>
          <button class="btn btn-red" onclick="demoFailover()">4. Kill node-3 &amp; Write w/ Quorum (2/3)</button>
          <button class="btn btn-green" onclick="nodeAction('node-3','start')">5. Restore node-3 &amp; Watch Catch-Up</button>
        </div>
      </div>
    </div>

    <div class="card">
      <h2>
        <span>Execution History &amp; Raw gRPC JSON</span>
        <button class="btn" style="padding:4px 9px;font-size:0.75rem;" onclick="clearHistory()">Clear</button>
      </h2>
      <div id="historyList" class="history-list">
        <div class="history-item" style="color:var(--muted);">Run any PUT, GET, DELETE, or node fault action to see the real gRPC command and JSON response here.</div>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>
      <span>Live Docker Container Logs</span>
      <div style="display:flex;gap:8px;align-items:center;">
        <select id="logNodeSelect" onchange="loadLogs()" style="padding:5px 9px;font-size:0.8rem;">
          <option value="all">All Nodes</option>
          <option value="node-1">node-1 (Leader)</option>
          <option value="node-2">node-2 (Follower)</option>
          <option value="node-3">node-3 (Follower)</option>
        </select>
        <button class="btn" style="padding:5px 10px;font-size:0.78rem;" onclick="loadLogs()">Refresh Logs</button>
      </div>
    </h2>
    <pre id="logsBox" class="terminal">Loading container logs...</pre>
  </div>

  <div class="card">
    <h2>5-Minute Deloitte Demo Script (What to Say While Clicking)</h2>
    <div class="interview-grid">
      <div class="interview-step">
        <strong>1. Architecture (3 Nodes)</strong>
        "This runs three isolated Java 21 gRPC containers with separate persistent WAL volumes: <code>node-1</code> is the fixed leader, and <code>node-2</code> &amp; <code>node-3</code> are followers."
      </div>
      <div class="interview-step">
        <strong>2. Replication &amp; Quorum (PUT / GET)</strong>
        "When I click PUT, the leader assigns a log index, appends to its local WAL, replicates over gRPC to followers, and commits only after a majority (2/3) durably persist it."
      </div>
      <div class="interview-step">
        <strong>3. Idempotency &amp; Safety</strong>
        "Clients send a unique <code>request_id</code>. Retrying the same ID returns the original <code>commit_index</code> without re-applying; reusing an ID with a different payload returns <code>REQUEST_ID_CONFLICT</code>."
      </div>
      <div class="interview-step">
        <strong>4. Fault Tolerance &amp; Recovery</strong>
        "If I SIGKILL <code>node-3</code>, writes still commit because <code>node-1 + node-2</code> form a 2/3 majority. When <code>node-3</code> starts back up, it automatically catches up to the leader's log index."
      </div>
    </div>
  </div>
</div>

<script>
let pollTimer = null;

function newRequestId() {
  const id = (crypto && crypto.randomUUID) ? crypto.randomUUID() : ('req-' + Date.now() + '-' + Math.floor(Math.random()*10000));
  document.getElementById('reqIdInput').value = id;
  return id;
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

async function api(path, body = null) {
  const opts = body ? {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  } : { method: 'GET' };
  const res = await fetch(path, opts);
  return await res.json();
}

function addHistory(title, data) {
  const list = document.getElementById('historyList');
  if (list.children.length === 1 && list.children[0].textContent.includes('Run any PUT')) {
    list.innerHTML = '';
  }
  const item = document.createElement('div');
  item.className = 'history-item';
  const r = data.result || {};
  const code = r.code || (data.ok ? 'OK' : 'ERROR');
  const badgeCls = (code === 'OK') ? 'badge-ok' : (code === 'NOT_FOUND' || code === 'NOT_LEADER' || code === 'REQUEST_ID_CONFLICT') ? 'badge-warn' : 'badge-down';
  let summary = '';
  if (r.code) {
    summary = `code=${r.code}`;
    if (r.commit_index !== null && r.commit_index !== undefined) summary += ` | commit_index=${r.commit_index}`;
    if (r.has_value) summary += ` | value="${r.decoded_value ?? r.value_base64}"`;
    if (r.request_id) summary += ` | req=${r.request_id.slice(0, 14)}...`;
  } else {
    summary = data.message || data.stdout || data.error || 'Completed';
  }
  item.innerHTML = `
    <div class="history-item-head">
      <span>${escapeHtml(title)}</span>
      <span class="badge ${badgeCls}">${escapeHtml(code)}</span>
    </div>
    <div style="margin-top:4px;">${escapeHtml(summary)}</div>
    ${data.command ? `<div class="history-cmd">$ ${escapeHtml(data.command)}</div>` : ''}
    ${data.stderr && !data.ok ? `<div class="history-cmd" style="color:#fca5a5;">${escapeHtml(data.stderr)}</div>` : ''}
  `;
  list.prepend(item);
}

function clearHistory() {
  document.getElementById('historyList').innerHTML = '<div class="history-item" style="color:var(--muted);">History cleared.</div>';
}

function renderNodes(nodes) {
  const grid = document.getElementById('nodesGrid');
  let runningCount = 0;
  let leaderReady = false;
  grid.innerHTML = nodes.map(n => {
    const c = n.container || {};
    const r = n.rpc || {};
    const isRunning = c.status === 'running';
    if (isRunning) runningCount++;
    if (n.service === 'node-1' && isRunning && c.health === 'healthy') leaderReady = true;

    const statusBadge = !isRunning
      ? `<span class="badge badge-down">${escapeHtml(c.status || 'stopped')}</span>`
      : (c.health === 'healthy'
          ? `<span class="badge badge-ok">healthy</span>`
          : `<span class="badge badge-warn">${escapeHtml(c.health || 'starting')}</span>`);

    const roleBadge = n.configured_role === 'LEADER'
      ? `<span class="badge badge-leader">Leader (${escapeHtml(n.node_id)})</span>`
      : `<span class="badge badge-follower">Follower (${escapeHtml(n.node_id)})</span>`;

    return `
      <div class="card node-card ${n.configured_role === 'LEADER' ? 'leader' : ''}">
        <div class="node-top">
          <div>
            <div class="node-name">${escapeHtml(n.service)}</div>
            <div class="node-sub">${escapeHtml(n.host_port)} ${c.container_id ? '• ' + escapeHtml(c.container_id) : ''}</div>
          </div>
          <div class="badges">${roleBadge}${statusBadge}</div>
        </div>
        <div class="metrics">
          <div class="metric">
            <div class="metric-label">Last Log</div>
            <div class="metric-val">${isRunning && r ? r.last_log_index : '—'}</div>
          </div>
          <div class="metric">
            <div class="metric-label">Committed</div>
            <div class="metric-val">${isRunning && r ? r.durable_commit_index : '—'}</div>
          </div>
          <div class="metric">
            <div class="metric-label">Applied</div>
            <div class="metric-val">${isRunning && r ? r.applied_index : '—'}</div>
          </div>
        </div>
        <div class="node-controls">
          <button class="btn btn-green" onclick="nodeAction('${n.service}','start')" ${isRunning ? 'disabled' : ''}>Start</button>
          <button class="btn btn-yellow" onclick="nodeAction('${n.service}','restart')" ${!c.exists ? 'disabled' : ''}>Restart</button>
          <button class="btn" onclick="nodeAction('${n.service}','stop')" ${!isRunning ? 'disabled' : ''}>Stop</button>
          <button class="btn btn-red" onclick="nodeAction('${n.service}','kill')" ${!isRunning ? 'disabled' : ''}>SIGKILL (Crash)</button>
        </div>
      </div>
    `;
  }).join('');

  const node1 = nodes.find(n => n.service === 'node-1');
  const node1Running = node1 && node1.container && node1.container.status === 'running';
  const banner = document.getElementById('quorumBanner');
  const hasQuorum = runningCount >= 2;
  banner.className = 'quorum-banner ' + (hasQuorum && leaderReady ? 'quorum-ok' : 'quorum-bad');
  banner.firstElementChild.textContent =
    `Running Nodes: ${runningCount}/3 • Write Quorum (need 2/3): ${hasQuorum ? 'AVAILABLE' : 'LOST (NO_QUORUM)'} • Leader (node-1): ${leaderReady ? 'READY (healthy)' : (node1Running ? 'STARTING / NOT_READY' : 'DOWN')}`;
  document.getElementById('lastUpdated').textContent = 'Updated ' + new Date().toLocaleTimeString();
}

async function refreshStatus() {
  try {
    const data = await api('/api/status');
    renderNodes(data.nodes || []);
  } catch (e) {
    console.error(e);
  }
}

async function loadLogs() {
  const node = document.getElementById('logNodeSelect').value;
  try {
    const data = await api('/api/logs?node=' + encodeURIComponent(node));
    const box = document.getElementById('logsBox');
    box.textContent = data.logs || '(no logs yet)';
    box.scrollTop = box.scrollHeight;
  } catch (e) {
    document.getElementById('logsBox').textContent = 'Failed to fetch logs: ' + e;
  }
}

async function sendKvOp(op, overrides = {}) {
  const key = overrides.key ?? document.getElementById('keyInput').value.trim();
  const value = overrides.value ?? document.getElementById('valueInput').value;
  const target = overrides.target ?? document.getElementById('targetSelect').value;
  let reqId = overrides.requestId ?? document.getElementById('reqIdInput').value.trim();
  if (!reqId) reqId = newRequestId();
  const data = await api('/api/kv', { operation: op, key, value, target, request_id: reqId });
  addHistory(`${op.toUpperCase()} "${key}" -> ${target}`, data);
  await refreshStatus();
  await loadLogs();
  return data;
}

async function nodeAction(node, action) {
  addHistory(`Docker ${action.toUpperCase()} on ${node} (running...)`, { ok: true, message: 'Executing docker compose ' + action + ' ' + node });
  const data = await api('/api/node', { node, action });
  addHistory(`Docker ${action.toUpperCase()} ${node}`, data);
  await refreshStatus();
  await loadLogs();
}

async function clusterAction(action) {
  addHistory(`Cluster ${action.toUpperCase()} (running...)`, { ok: true, message: 'Executing docker compose ' + action });
  const data = await api('/api/cluster', { action });
  addHistory(`Cluster ${action.toUpperCase()}`, data);
  await refreshStatus();
  await loadLogs();
}

async function runAutomatedSmoke() {
  addHistory('Automated Smoke Suite started...', { ok: true, message: 'Running PUT -> GET -> Idempotent Retry -> Request-ID Conflict -> Empty Value -> DELETE -> Follower NOT_LEADER' });
  const data = await api('/api/smoke', {});
  addHistory('Automated Smoke Suite', data);
  await refreshStatus();
  await loadLogs();
}

async function demoIdempotency() {
  const id = newRequestId();
  document.getElementById('targetSelect').value = 'node-1';
  await sendKvOp('put', { requestId: id });
  await sendKvOp('put', { requestId: id });
}

async function demoConflict() {
  const id = document.getElementById('reqIdInput').value.trim() || newRequestId();
  document.getElementById('targetSelect').value = 'node-1';
  await sendKvOp('put', { requestId: id, value: 'original-value' });
  await sendKvOp('put', { requestId: id, value: 'tampered-different-value' });
}

async function demoFollowerReject() {
  await sendKvOp('get', { target: 'node-2' });
}

async function demoFailover() {
  await nodeAction('node-3', 'kill');
  const id = newRequestId();
  await sendKvOp('put', { key: 'survived-node3-crash', value: 'majority-2-of-3', requestId: id, target: 'node-1' });
}

function togglePoll() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  if (document.getElementById('autoPoll').checked) {
    pollTimer = setInterval(refreshStatus, 3000);
  }
}

newRequestId();
refreshStatus();
loadLogs();
togglePoll();
</script>
</body>
</html>
"""


def run_smoke_suite():
    key = f"live-smoke-{uuid.uuid4().hex[:8]}"
    req_id = str(uuid.uuid4())
    steps = []

    def step(name, op, **kw):
        res = invoke_smoke_client(op, **kw)
        steps.append({"step": name, "ok": res["ok"], "code": (res.get("result") or {}).get("code"), "res": res})
        return res

    r1 = step("1. PUT key", "retry-put", target="node-1", key=key, request_id=req_id, value_text="phase12-verified-value")
    idx1 = (r1.get("result") or {}).get("commit_index")
    step("2. GET key", "get", target="node-1", key=key)
    r3 = step("3. Retry PUT (same request_id)", "put", target="node-1", key=key, request_id=req_id, value_text="phase12-verified-value")
    step("4. Conflict PUT (same request_id, new value)", "put", target="node-1", key=key, request_id=req_id, value_text="different-value")
    del_id = str(uuid.uuid4())
    step("5. DELETE key", "delete", target="node-1", key=key, request_id=del_id)
    step("6. GET deleted key", "get", target="node-1", key=key)
    step("7. GET on follower node-2", "get", target="node-2", key=key)

    summary = " | ".join(f"{s['step']}: {s['code']}" for s in steps)
    all_expected = (
        steps[0]["code"] == "OK"
        and steps[1]["code"] == "OK"
        and steps[2]["code"] == "OK"
        and (steps[2]["res"].get("result") or {}).get("commit_index") == idx1
        and steps[3]["code"] == "REQUEST_ID_CONFLICT"
        and steps[4]["code"] == "OK"
        and steps[5]["code"] == "NOT_FOUND"
        and steps[6]["code"] == "NOT_LEADER"
    )
    return {
        "ok": all_expected,
        "result": {"code": "OK" if all_expected else "FAILED", "commit_index": idx1},
        "message": summary,
        "command": f"Full 7-step gRPC smoke test on key={key}",
        "steps": steps,
    }


class DemoHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            body = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/api/status":
            self._send_json(get_cluster_status())
        elif parsed.path == "/api/logs":
            qs = urllib.parse.parse_qs(parsed.query)
            node = qs.get("node", ["all"])[0]
            args = ["logs", "--tail=35"]
            if node in NODES:
                args.append(node)
            code, out, err, cmd = compose_cmd(*args, timeout=15)
            self._send_json({"ok": code == 0, "logs": out or err, "command": cmd})
        else:
            self._send_json({"error": "Not found"}, status=404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send_json({"ok": False, "error": "Invalid JSON body"}, status=400)
            return

        if self.path == "/api/kv":
            op = data.get("operation", "get")
            if op not in ("put", "get", "delete", "retry-put", "retry-delete"):
                self._send_json({"ok": False, "error": f"Unsupported operation: {op}"}, status=400)
                return
            target = data.get("target", "node-1")
            if target not in NODES:
                self._send_json({"ok": False, "error": f"Invalid target: {target}"}, status=400)
                return
            res = invoke_smoke_client(
                op,
                target=target,
                key=data.get("key", ""),
                request_id=data.get("request_id", ""),
                value_text=data.get("value", ""),
            )
            self._send_json(res)
        elif self.path == "/api/node":
            node = data.get("node", "")
            action = data.get("action", "")
            if node not in NODES or action not in ("start", "stop", "restart", "kill"):
                self._send_json({"ok": False, "error": "Invalid node or action"}, status=400)
                return
            if action == "kill":
                code, out, err, cmd = compose_cmd("kill", "--signal", "SIGKILL", node, timeout=20)
            else:
                code, out, err, cmd = compose_cmd(action, node, timeout=45)
            self._send_json({
                "ok": code == 0,
                "command": cmd,
                "stdout": out,
                "stderr": err,
                "message": f"{action} {node} completed (exit={code})",
            })
        elif self.path == "/api/cluster":
            action = data.get("action", "")
            if action not in ("up", "down"):
                self._send_json({"ok": False, "error": "Invalid cluster action"}, status=400)
                return
            if action == "up":
                code, out, err, cmd = compose_cmd("up", "--detach", timeout=90)
            else:
                code, out, err, cmd = compose_cmd("down", timeout=45)
            self._send_json({
                "ok": code == 0,
                "command": cmd,
                "stdout": out,
                "stderr": err,
                "message": f"cluster {action} completed (exit={code})",
            })
        elif self.path == "/api/smoke":
            self._send_json(run_smoke_suite())
        else:
            self._send_json({"error": "Not found"}, status=404)


class ReusableTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    with ReusableTCPServer(("127.0.0.1", port), DemoHandler) as httpd:
        print(f"Raft-KV Live Demo Console running at http://127.0.0.1:{port}", flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
