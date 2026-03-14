"""
Tactical Edge Service - Demo service for migration benchmarking.

This service simulates a lightweight edge workload that maintains state,
processes requests, and can be migrated between edge nodes.
"""

import json
import os
import time
import threading
import psutil
import logging
from flask import Flask, jsonify, request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

app = Flask(__name__)

SERVICE_NAME = os.environ.get("SERVICE_NAME", "edge-service")
STATE_FILE = os.environ.get("STATE_FILE", "/app/state/service_state.json")

# In-memory state (serialised to disk for migration checkpoints)
state = {
    "service_name": SERVICE_NAME,
    "request_count": 0,
    "start_time": time.time(),
    "last_processed": None,
    "data_buffer": [],
    "node_id": os.environ.get("NODE_ID", "node-0"),
}

state_lock = threading.Lock()


def load_state() -> None:
    """Load persisted state from disk if it exists."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                loaded = json.load(f)
                state.update(loaded)
            log.info("State loaded from %s", STATE_FILE)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not load state from %s: %s", STATE_FILE, exc)


def save_state() -> None:
    """Persist current state to disk."""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": SERVICE_NAME})


@app.route("/process", methods=["POST"])
def process():
    """Simulate a simple data processing task."""
    payload = request.get_json(silent=True) or {}
    data = payload.get("data", "")

    with state_lock:
        state["request_count"] += 1
        state["last_processed"] = time.time()
        state["data_buffer"].append({"ts": time.time(), "data": str(data)[:64]})
        # Keep buffer bounded to avoid unbounded memory growth
        if len(state["data_buffer"]) > 1000:
            state["data_buffer"] = state["data_buffer"][-1000:]
        save_state()

    result = {"echo": data, "request_count": state["request_count"]}
    return jsonify(result)


@app.route("/state")
def get_state():
    """Return current service state (for migration checkpoint verification)."""
    with state_lock:
        snapshot = dict(state)
    snapshot["uptime_seconds"] = time.time() - snapshot["start_time"]
    return jsonify(snapshot)


@app.route("/metrics")
def get_metrics():
    """Return current resource utilisation metrics."""
    proc = psutil.Process()
    return jsonify(
        {
            "cpu_percent": proc.cpu_percent(interval=0.1),
            "memory_rss_mb": proc.memory_info().rss / (1024 * 1024),
            "memory_vms_mb": proc.memory_info().vms / (1024 * 1024),
            "request_count": state["request_count"],
            "uptime_seconds": time.time() - state["start_time"],
        }
    )


@app.route("/checkpoint")
def checkpoint():
    """Trigger an explicit application-level state checkpoint."""
    with state_lock:
        save_state()
    return jsonify({"status": "checkpointed", "state_file": STATE_FILE})


if __name__ == "__main__":
    load_state()
    log.info("Starting %s on port 8080", SERVICE_NAME)
    app.run(host="0.0.0.0", port=8080, threaded=True)
