# lambda-actions/compute_lambda/handler.py
# Scales up ECS service desired count when CPU/memory overload is detected.

import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ecs    = boto3.client("ecs",    region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
cw     = boto3.client("cloudwatch", region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))

ECS_CLUSTER      = os.getenv("ECS_CLUSTER_NAME", "log-aggregator-cluster-dev")
TARGET_SERVICE   = os.getenv("DUMMY_APP_SERVICE_NAME", "dummy-infra-app-svc-dev")
MAX_DESIRED      = int(os.getenv("MAX_DESIRED_COUNT", "4"))


def _get_current_desired(cluster: str, service: str) -> int:
    resp = ecs.describe_services(cluster=cluster, services=[service])
    svcs = resp.get("services", [])
    if not svcs:
        raise ValueError(f"ECS service not found: {service} in {cluster}")
    return svcs[0]["desiredCount"]


def _scale_up(cluster: str, service: str, current: int) -> int:
    new_count = min(current + 1, MAX_DESIRED)
    if new_count == current:
        logger.info("Already at max desired count: %d", MAX_DESIRED)
        return current

    ecs.update_service(
        cluster=cluster,
        service=service,
        desiredCount=new_count,
    )
    logger.info("ECS scaled up: %s → desiredCount=%d", service, new_count)
    return new_count


def handler(event, context):
    """Bedrock action group handler for ECS compute scaling."""
    logger.info("Compute Lambda invoked: %s", json.dumps(event))

    params   = {p["name"]: p["value"] for p in event.get("parameters", [])}
    cluster  = params.get("ecs_cluster",  ECS_CLUSTER)
    service  = params.get("ecs_service",  TARGET_SERVICE)

    try:
        current   = _get_current_desired(cluster, service)
        new_count = _scale_up(cluster, service, current)

        if new_count > current:
            action  = "scaled_up"
            message = f"ECS service scaled from {current} to {new_count} tasks."
        else:
            action  = "already_at_max"
            message = f"ECS service already at max desired count ({MAX_DESIRED})."

        response_body = {
            "action":      action,
            "old_count":   current,
            "new_count":   new_count,
            "service":     service,
            "cluster":     cluster,
            "max_allowed": MAX_DESIRED,
            "message":     message,
            "status":      "success",
        }

    except Exception as exc:
        logger.error("Compute remediation failed: %s", exc)
        response_body = {"action": "failed", "error": str(exc), "status": "error"}

    return {
        "actionGroup": event.get("actionGroup", "compute_remediation_action_group"),
        "function":    event.get("function", "remediateCompute"),
        "functionResponse": {
            "responseBody": {"TEXT": {"body": json.dumps(response_body)}}
        },
    }
