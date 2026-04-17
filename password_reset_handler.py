# lambda-actions/password_reset_lambda/handler.py
# Rotates service account password in Secrets Manager and notifies dummy app.

import json
import logging
import os
import secrets
import string
import urllib.request

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sm            = boto3.client("secretsmanager", region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
DUMMY_APP_URL = os.getenv("DUMMY_APP_URL", "http://dummy-infra-app:5001")
SECRET_NAME   = os.getenv("SERVICE_ACCOUNT_SECRET", "dummy-app/service-account-credentials")


def _generate_password(length: int = 24) -> str:
    """Generate a secure random password."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _rotate_secret(secret_name: str, username: str) -> str:
    """Rotate the password in Secrets Manager and return the new password."""
    new_password = _generate_password()

    try:
        current = json.loads(
            sm.get_secret_value(SecretId=secret_name)["SecretString"]
        )
        current["password"]       = new_password
        current["last_rotated"]   = __import__("datetime").datetime.utcnow().isoformat()
        current["rotation_count"] = current.get("rotation_count", 0) + 1

        sm.put_secret_value(
            SecretId=secret_name,
            SecretString=json.dumps(current),
        )
    except ClientError:
        sm.create_secret(
            Name=secret_name,
            SecretString=json.dumps({
                "username": username,
                "password": new_password,
            }),
        )

    logger.info("Password rotated in Secrets Manager: %s", secret_name)
    return new_password


def _notify_dummy_app(details: dict):
    url  = f"{DUMMY_APP_URL}/api/dummy/resolve/password_expired"
    data = json.dumps(details).encode("utf-8")
    req  = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            logger.info("Dummy app notified of password rotation")
    except Exception as exc:
        logger.warning("Could not notify dummy app (non-fatal): %s", exc)


def handler(event, context):
    """Bedrock action group handler for password/credential rotation."""
    logger.info("Password Reset Lambda invoked: %s", json.dumps(event))

    params      = {p["name"]: p["value"] for p in event.get("parameters", [])}
    secret_name = params.get("secret_name", SECRET_NAME)
    username    = params.get("username", "service-account")

    try:
        secret_resp = sm.describe_secret(SecretId=secret_name)
        secret_arn  = secret_resp["ARN"]

        _rotate_secret(secret_name, username)
        _notify_dummy_app({"secret_name": secret_name, "action": "password_rotated"})

        response_body = {
            "action":           "password_rotated",
            "secret_name":      secret_name,
            "secret_arn":       secret_arn,
            "username":         username,
            "next_rotation":    "90 days",
            "status":           "success",
        }

    except Exception as exc:
        logger.error("Password rotation failed: %s", exc)
        response_body = {
            "action": "failed",
            "error":  str(exc),
            "status": "error",
        }

    return {
        "actionGroup": event.get("actionGroup", "password_reset_action_group"),
        "function":    event.get("function", "resetPassword"),
        "functionResponse": {
            "responseBody": {"TEXT": {"body": json.dumps(response_body)}}
        },
    }
