# dummy-infra-app/error_simulator.py
# Generates realistic log entries for each known error type.
# Log format matches the existing log_parser.py regex patterns so the
# Lambda processor can parse them into the dashboard automatically.

import random
from datetime import datetime, timezone


class ErrorSimulator:
    """Generates structured log entries that match the log_parser.py regex."""

    # Error definitions: (http_code, error_code, log_message)
    ERROR_DEFINITIONS = {
        "ssl_expired": (
            495, 9010,
            "SSL certificate expired for domain api.dummy-app.internal",
            "GET /api/dummy/status",
        ),
        "ssl_expiring": (
            200, 9011,
            "SSL certificate expires in 7 days for domain api.dummy-app.internal",
            "GET /api/dummy/status",
        ),
        "password_expired": (
            401, 9012,
            "Service account password expired, authentication failed",
            "POST /api/dummy/auth",
        ),
        "db_storage": (
            507, 9013,
            "Database storage at 92% capacity, writes may fail",
            "POST /api/dummy/db-write",
        ),
        "db_connection": (
            504, 9014,
            "RDS connection pool exhausted, timeout after 30s",
            "GET /api/dummy/db-read",
        ),
        "compute_overload": (
            503, 9015,
            "CPU at 95%, memory at 88%, dropping requests",
            "POST /api/dummy/process",
        ),
    }

    RESOLUTION_MESSAGES = {
        "ssl_expired":      "SSL certificate renewed successfully. New cert ARN stored in Secrets Manager.",
        "ssl_expiring":     "SSL certificate rotated proactively. 90 days until next expiry.",
        "password_expired": "Service account password rotated via Secrets Manager. Auth reconnected.",
        "db_storage":       "RDS allocated storage increased. New capacity applied successfully.",
        "db_connection":    "RDS instance class upgraded. Connection pool limits increased.",
        "compute_overload": "ECS desired count increased. Additional tasks launched and healthy.",
    }

    def __init__(self, logger, log_file: str):
        self._logger   = logger
        self._log_file = log_file

    def _ts(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    def generate_error(self, error_type: str) -> str:
        """
        Write a 3-line log entry (request → error → status) matching
        the log_parser.py pattern so it gets parsed into the dashboard.
        Returns the error log line.
        """
        if error_type not in self.ERROR_DEFINITIONS:
            raise ValueError(f"Unknown error type: {error_type}")

        http_code, err_code, description, api_path = self.ERROR_DEFINITIONS[error_type]
        ts      = self._ts()
        fake_ip = f"10.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,254)}"

        # Line 1: request arrival (matched by API_WITH_IP_PATTERN)
        line1 = f"[{ts}] [INFO] {api_path} IP: {fake_ip}"
        # Line 2: error detail (matched by ERROR_CODE_PATTERN)
        line2 = f"[{ts}] [ERROR] {description} {{'error_code': {err_code}}}"
        # Line 3: response status (matched by STATUS_PATTERN)
        line3 = f"[{ts}] [INFO] {api_path} Status Code: {http_code}"

        with open(self._log_file, "a", encoding="utf-8") as f:
            f.write(f"{line1}\n{line2}\n{line3}\n")

        self._logger.info("Error triggered: %s (code=%d)", error_type, err_code)
        return line2

    def generate_resolution(self, error_type: str, details: dict) -> str:
        """Write a resolution log entry and return it."""
        ts  = self._ts()
        msg = self.RESOLUTION_MESSAGES.get(error_type, f"{error_type} resolved.")

        if details:
            msg += f" Details: {details}"

        line = f"[{ts}] [INFO] RESOLVED: {msg}"

        with open(self._log_file, "a", encoding="utf-8") as f:
            f.write(f"{line}\n")

        self._logger.info("Resolved: %s", error_type)
        return line
