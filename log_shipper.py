# dummy-infra-app/log_shipper.py
# Uploads the current log file to the S3 raw-logs bucket every 60 seconds.
# Each upload uses a timestamped key so Lambda gets a fresh S3 ObjectCreated event.

import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError


class LogShipper:
    """Ships the dummy app log file to S3 raw-logs bucket."""

    def __init__(self, logger, log_file: str):
        self._logger    = logger
        self._log_file  = log_file
        self._s3        = boto3.client("s3", region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
        self._bucket    = os.getenv("RAW_LOGS_BUCKET", "")
        self._prefix    = os.getenv("RAW_LOGS_PREFIX", "raw-logs/")
        self.last_s3_key = ""

    def ship(self) -> bool:
        """
        Upload current log file to S3.
        Uses a timestamped key so every upload fires a new S3 ObjectCreated event,
        which triggers the Lambda processor.
        Returns True on success.
        """
        if not self._bucket:
            self._logger.warning("RAW_LOGS_BUCKET not set — skipping S3 ship")
            return False

        if not os.path.exists(self._log_file):
            self._logger.warning("Log file not found: %s", self._log_file)
            return False

        ts  = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        key = f"{self._prefix}dummy-app-{ts}.log"

        try:
            self._s3.upload_file(
                self._log_file, self._bucket, key,
                ExtraArgs={"ContentType": "text/plain"},
            )
            self.last_s3_key = key
            self._logger.info("Shipped log to s3://%s/%s", self._bucket, key)
            return True
        except ClientError as exc:
            self._logger.error("S3 ship failed: %s", exc)
            return False
