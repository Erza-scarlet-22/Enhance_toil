# lambda-actions/servicenow_lambda/handler.py
# Called by Bedrock orchestrator agent action group.
# Creates a ServiceNow incident and returns the ticket number.

import json
import logging
import os
import urllib.request
import urllib.parse
import base64
from datetime import datetime, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sm = boto3.client("secretsmanager", region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))

URGENCY_MAP = {
    "ssl_expired":      "1",
    "db_storage":       "1",
    "compute_overload": "2",
    "db_connection":    "2",
    "password_expired": "2",
    "ssl_expiring":     "3",
}

CATEGORY_MAP = {
    "ssl_expired":      "Security",
    "ssl_expiring":     "Security",
    "password_expired": "Security",
    "db_storage":       "Database",
    "db_connection":    "Database",
    "compute_overload": "Infrastructure",
}


def _get_snow_creds() -> dict:
    secret_name = os.getenv("SERVICENOW_SECRET_NAME", "servicenow/credentials")
    resp = sm.get_secret_value(SecretId=secret_name)
    return json.loads(resp["SecretString"])


def _create_incident(creds: dict, payload: dict) -> dict:
    url = f"{creds['instance_url'].rstrip('/')}/api/now/table/incident"
    data = json.dumps(payload).encode("utf-8")

    token = base64.b64encode(
        f"{creds['username']}:{creds['password']}".encode()
    ).decode()

    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type":  "application/json",
            "Accept":        "application/json",
            "Authorization": f"Basic {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def handler(event, context):
    """
    Bedrock action group handler for ServiceNow incident creation.
    Event shape from Bedrock:
    {
      "actionGroup": "servicenow_action_group",
      "function":    "createIncident",
      "parameters":  [ {"name": "error_type", "value": "ssl_expired"}, ... ]
    }
    """
    logger.info("ServiceNow Lambda invoked: %s", json.dumps(event))

    # Parse parameters from Bedrock action group event
    params = {p["name"]: p["value"] for p in event.get("parameters", [])}

    error_type  = params.get("error_type", "unknown")
    description = params.get("error_description", "Error detected by Log Aggregator")
    status_code = params.get("status_code", "")
    count       = params.get("count", "1")
    last_seen   = params.get("last_seen", datetime.now(timezone.utc).isoformat())
    urgency     = params.get("urgency", URGENCY_MAP.get(error_type, "2"))

    try:
        creds = _get_snow_creds()

        incident_payload = {
            "short_description": f"Auto-detected: {description}",
            "description": (
                f"Error detected by AWS Log Aggregator.\n"
                f"Error Type: {error_type}\n"
                f"Status Code: {status_code}\n"
                f"Occurrence Count: {count}\n"
                f"Last Seen: {last_seen}\n"
                f"Source: AWS Log Aggregator Auto-Remediation"
            ),
            "urgency":          urgency,
            "impact":           urgency,
            "category":         CATEGORY_MAP.get(error_type, "Infrastructure"),
            "assignment_group": "AWS Operations",
            "u_source":         "AWS Log Aggregator",
            "u_aws_error_code": str(status_code),
            "u_remediation_status": "automated",
        }

        result    = _create_incident(creds, incident_payload)
        ticket_no = result.get("result", {}).get("number", "UNKNOWN")
        ticket_id = result.get("result", {}).get("sys_id", "")

        logger.info("ServiceNow incident created: %s", ticket_no)

        response_body = {
            "ticket_number":    ticket_no,
            "ticket_sys_id":    ticket_id,
            "ticket_url":       f"{creds['instance_url']}/nav_to.do?uri=incident.do?sys_id={ticket_id}",
            "status":           "created",
            "error_type":       error_type,
        }

    except Exception as exc:
        logger.error("ServiceNow incident creation failed: %s", exc)
        # Return a mock ticket in demo mode so the rest of the flow continues
        response_body = {
            "ticket_number": "INC_DEMO_001",
            "ticket_url":    "https://demo.service-now.com/incident/INC_DEMO_001",
            "status":        "demo_mode",
            "error":         str(exc),
            "note":          "ServiceNow not configured. Set servicenow/credentials in Secrets Manager.",
        }

    # Bedrock action group response format
    return {
        "actionGroup":  event.get("actionGroup", "servicenow_action_group"),
        "function":     event.get("function", "createIncident"),
        "functionResponse": {
            "responseBody": {
                "TEXT": {"body": json.dumps(response_body)}
            }
        },
    }
