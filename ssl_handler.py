# lambda-actions/ssl_lambda/handler.py
# Handles SSL certificate remediation for both expired and expiring-soon scenarios.

import json
import logging
import os
import urllib.request

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

acm    = boto3.client("acm",             region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
r53    = boto3.client("route53",         region_name="us-east-1")
sm     = boto3.client("secretsmanager",  region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))

DUMMY_APP_URL = os.getenv("DUMMY_APP_URL", "http://dummy-infra-app:5001")
DOMAIN        = os.getenv("SSL_DOMAIN", "api.dummy-app.internal")
HOSTED_ZONE   = os.getenv("ROUTE53_HOSTED_ZONE_ID", "")


def _request_certificate(domain: str) -> str:
    """Request a new ACM certificate and return its ARN."""
    validation = "DNS" if HOSTED_ZONE else "EMAIL"
    resp = acm.request_certificate(
        DomainName=domain,
        ValidationMethod=validation,
        SubjectAlternativeNames=[f"*.{domain}"],
        Tags=[{"Key": "ManagedBy", "Value": "LogAggregatorAutoRemediation"}],
    )
    arn = resp["CertificateArn"]
    logger.info("ACM certificate requested: %s", arn)
    return arn


def _store_cert_arn(cert_arn: str):
    """Store the new cert ARN in Secrets Manager for the dummy app to read."""
    secret_name = os.getenv("SSL_CERT_SECRET_NAME", "dummy-app/ssl-cert-arn")
    try:
        sm.put_secret_value(
            SecretId=secret_name,
            SecretString=json.dumps({"cert_arn": cert_arn}),
        )
        logger.info("Cert ARN stored in Secrets Manager: %s", secret_name)
    except ClientError:
        # Create if doesn't exist
        sm.create_secret(
            Name=secret_name,
            SecretString=json.dumps({"cert_arn": cert_arn}),
        )


def _notify_dummy_app(error_type: str, details: dict):
    """Call dummy app resolve endpoint so it reloads its cert config."""
    url  = f"{DUMMY_APP_URL}/api/dummy/resolve/{error_type}"
    data = json.dumps(details).encode("utf-8")
    req  = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            logger.info("Dummy app notified of resolution: %s", error_type)
    except Exception as exc:
        logger.warning("Could not notify dummy app (non-fatal): %s", exc)


def handler(event, context):
    """Bedrock action group handler for SSL certificate remediation."""
    logger.info("SSL Lambda invoked: %s", json.dumps(event))

    params     = {p["name"]: p["value"] for p in event.get("parameters", [])}
    error_type = params.get("error_type", "ssl_expired")
    domain     = params.get("domain", DOMAIN)

    try:
        cert_arn = _request_certificate(domain)
        _store_cert_arn(cert_arn)

        if error_type == "ssl_expired":
            action = "cert_renewed"
            msg    = f"SSL certificate renewed for {domain}. New cert ARN stored."
        else:
            action = "cert_rotated_proactively"
            msg    = f"SSL certificate rotated proactively for {domain}. 90 days until next expiry."

        _notify_dummy_app(error_type, {"cert_arn": cert_arn, "action": action})

        response_body = {
            "action":           action,
            "domain":           domain,
            "cert_arn":         cert_arn,
            "message":          msg,
            "status":           "success",
        }

    except Exception as exc:
        logger.error("SSL remediation failed: %s", exc)
        response_body = {
            "action":  "failed",
            "error":   str(exc),
            "status":  "error",
        }

    return {
        "actionGroup": event.get("actionGroup", "ssl_remediation_action_group"),
        "function":    event.get("function", "remediateSSL"),
        "functionResponse": {
            "responseBody": {"TEXT": {"body": json.dumps(response_body)}}
        },
    }
