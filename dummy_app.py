# dummy-infra-app/app.py
# Simulates a real infrastructure app that generates known, fixable errors.
# Each error type is resolvable by a specific Bedrock agent action group.

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from error_simulator import ErrorSimulator
from log_shipper import LogShipper

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_DIR  = os.getenv("LOG_DIR", "/app/logs")
LOG_FILE = os.path.join(LOG_DIR, "dummy-app.log")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("dummy-infra-app")

# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__)

# ── State ─────────────────────────────────────────────────────────────────────
# Tracks which errors are currently active
_active_errors: dict = {}
_state_lock = threading.Lock()

simulator = ErrorSimulator(logger, LOG_FILE)
shipper   = LogShipper(logger, LOG_FILE)


# ── Background: log shipping every 60 s ──────────────────────────────────────
def _shipping_loop():
    while True:
        time.sleep(60)
        shipper.ship()


threading.Thread(target=_shipping_loop, daemon=True).start()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "service": "dummy-infra-app",
                    "active_errors": len(_active_errors)}), 200


@app.route("/api/dummy/status", methods=["GET"])
def status():
    with _state_lock:
        return jsonify({
            "active_errors": list(_active_errors.values()),
            "error_count":   len(_active_errors),
            "timestamp":     _now(),
        }), 200


@app.route("/api/dummy/errors", methods=["GET"])
def list_errors():
    with _state_lock:
        return jsonify({"errors": list(_active_errors.values())}), 200


@app.route("/api/dummy/trigger-error", methods=["POST"])
def trigger_error():
    """Trigger a specific error type to generate log entries."""
    body       = request.get_json(silent=True) or {}
    error_type = body.get("error_type", "").strip()

    valid_types = ["ssl_expired", "ssl_expiring", "password_expired",
                   "db_storage", "db_connection", "compute_overload"]

    if not error_type or error_type not in valid_types:
        return jsonify({
            "error": f"Invalid error_type. Valid: {valid_types}"
        }), 400

    log_entry = simulator.generate_error(error_type)
    with _state_lock:
        _active_errors[error_type] = {
            "type":        error_type,
            "triggered_at": _now(),
            "status":      "active",
            "log_entry":   log_entry,
        }

    # Ship logs immediately after triggering
    shipper.ship()

    return jsonify({
        "triggered":  error_type,
        "log_entry":  log_entry,
        "shipped_to": shipper.last_s3_key,
    }), 200


@app.route("/api/dummy/resolve/<error_type>", methods=["POST"])
def resolve_error(error_type):
    """
    Called by Bedrock action group Lambdas after they fix an error.
    Marks the error as resolved and writes a resolution log entry.
    """
    body    = request.get_json(silent=True) or {}
    details = body.get("details", {})

    resolution_msg = simulator.generate_resolution(error_type, details)

    with _state_lock:
        if error_type in _active_errors:
            _active_errors[error_type]["status"]      = "resolved"
            _active_errors[error_type]["resolved_at"] = _now()
            _active_errors[error_type]["details"]     = details

    # Ship resolution log immediately
    shipper.ship()

    return jsonify({
        "resolved":     error_type,
        "log_entry":    resolution_msg,
        "shipped_to":   shipper.last_s3_key,
    }), 200


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    port = int(os.getenv("APP_PORT", 5001))
    logger.info("dummy-infra-app starting on port %d", port)
    app.run(host="0.0.0.0", port=port, debug=False)
