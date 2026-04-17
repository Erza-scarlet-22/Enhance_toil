# lambda-actions/db_lambda/handler.py
# Handles RDS remediation: storage increase (507) and instance upgrade (504).

import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

rds = boto3.client("rds", region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))

DB_INSTANCE_ID = os.getenv("RDS_DB_INSTANCE_ID", "log-aggregator-db")

INSTANCE_UPGRADE_MAP = {
    "db.t3.micro":   "db.t3.small",
    "db.t3.small":   "db.t3.medium",
    "db.t3.medium":  "db.t3.large",
    "db.t3.large":   "db.t3.xlarge",
    "db.t3.xlarge":  "db.t3.xlarge",   # already at top of t3
}


def _describe_db(db_id: str) -> dict:
    resp = rds.describe_db_instances(DBInstanceIdentifier=db_id)
    return resp["DBInstances"][0]


def _handle_storage(db_id: str, db_info: dict) -> dict:
    """Increase allocated storage by 20%."""
    current_gb = db_info["AllocatedStorage"]
    new_gb     = max(current_gb + 20, int(current_gb * 1.2))

    rds.modify_db_instance(
        DBInstanceIdentifier=db_id,
        AllocatedStorage=new_gb,
        ApplyImmediately=True,
    )
    logger.info("RDS storage increased: %dGB → %dGB", current_gb, new_gb)

    return {
        "action":     "storage_increased",
        "old_gb":     current_gb,
        "new_gb":     new_gb,
        "db_id":      db_id,
        "status":     "success",
    }


def _handle_connection(db_id: str, db_info: dict) -> dict:
    """Upgrade instance class to next tier."""
    current_class = db_info["DBInstanceClass"]
    new_class     = INSTANCE_UPGRADE_MAP.get(current_class, current_class)

    if new_class == current_class:
        return {
            "action":  "no_upgrade_available",
            "message": f"Already at max tier: {current_class}",
            "status":  "skipped",
        }

    rds.modify_db_instance(
        DBInstanceIdentifier=db_id,
        DBInstanceClass=new_class,
        ApplyImmediately=True,
    )
    logger.info("RDS instance upgraded: %s → %s", current_class, new_class)

    return {
        "action":       "instance_upgraded",
        "old_class":    current_class,
        "new_class":    new_class,
        "db_id":        db_id,
        "status":       "success",
    }


def handler(event, context):
    """Bedrock action group handler for RDS remediation."""
    logger.info("DB Lambda invoked: %s", json.dumps(event))

    params     = {p["name"]: p["value"] for p in event.get("parameters", [])}
    error_type = params.get("error_type", "db_storage")
    db_id      = params.get("db_instance_id", DB_INSTANCE_ID)

    try:
        db_info = _describe_db(db_id)

        if error_type == "db_storage":
            response_body = _handle_storage(db_id, db_info)
        elif error_type == "db_connection":
            response_body = _handle_connection(db_id, db_info)
        else:
            response_body = {"action": "unknown_error_type", "error_type": error_type}

    except rds.exceptions.DBInstanceNotFoundFault:
        logger.warning("RDS instance %s not found — demo mode", db_id)
        response_body = {
            "action":  "demo_mode",
            "message": f"RDS instance '{db_id}' not found. Set RDS_DB_INSTANCE_ID env var.",
            "status":  "demo",
        }
    except Exception as exc:
        logger.error("DB remediation failed: %s", exc)
        response_body = {"action": "failed", "error": str(exc), "status": "error"}

    return {
        "actionGroup": event.get("actionGroup", "db_remediation_action_group"),
        "function":    event.get("function", "remediateDB"),
        "functionResponse": {
            "responseBody": {"TEXT": {"body": json.dumps(response_body)}}
        },
    }
