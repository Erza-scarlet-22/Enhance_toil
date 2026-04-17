#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
# FULL DEPLOYMENT SCRIPT — Parts 1 to 5
# Log Aggregator Enhancement: Multi-Agent Auto-Remediation
#
# PREREQUISITES before running:
#   - AWS CLI configured (aws configure)
#   - Docker running
#   - jq installed (sudo apt install jq  OR  brew install jq)
#   - Your existing stack already deployed
#   - ACCOUNT_ID, REGION, ENV set below
# ══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ── Configuration — UPDATE THESE ─────────────────────────────────────────────
ACCOUNT_ID="YOUR_ACCOUNT_ID"        # e.g. 723651357729
REGION="us-east-1"
ENV="dev"                           # matches your EnvironmentName parameter
CLUSTER="log-aggregator-cluster-${ENV}"
STACK_NAME="log-aggregator-${ENV}"

# ── Derived values (do not change) ────────────────────────────────────────────
ECR_BASE="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
DUMMY_APP_REPO="${ECR_BASE}/log-aggregator-dummy-app-${ENV}"

echo ""
echo "═══════════════════════════════════════════════════════"
echo " Log Aggregator Enhancement Deployment"
echo " Account: ${ACCOUNT_ID}  Region: ${REGION}  Env: ${ENV}"
echo "═══════════════════════════════════════════════════════"
echo ""


# ══════════════════════════════════════════════════════════════════════════════
# PART 0 — ECR Login (needed for all image pushes)
# ══════════════════════════════════════════════════════════════════════════════
echo "▶ PART 0: ECR login..."
aws ecr get-login-password --region "${REGION}" | \
    docker login --username AWS --password-stdin "${ECR_BASE}"
echo "✅ ECR login OK"
echo ""


# ══════════════════════════════════════════════════════════════════════════════
# PART 1 — Deploy CloudFormation additions
#
# MANUAL STEP: Open cloudformation-additions.yaml and copy each section
# into your existing log-aggregator-infra.yaml, then upload and update.
# This script does the update after you have manually merged the YAML.
# ══════════════════════════════════════════════════════════════════════════════
echo "▶ PART 1: Update CloudFormation stack..."
echo ""
echo "  ⚠️  MANUAL STEP REQUIRED:"
echo "  1. Open cloudformation-additions.yaml"
echo "  2. Copy each section into your log-aggregator-infra.yaml"
echo "  3. Upload the updated yaml to S3 artifact bucket"
echo "  4. Then press Enter to continue with stack update"
echo ""
read -rp "  Press Enter when YAML is ready in S3..."

aws cloudformation update-stack \
    --stack-name "${STACK_NAME}" \
    --use-previous-template \
    --capabilities CAPABILITY_NAMED_IAM \
    --region "${REGION}"

echo "  Waiting for stack update..."
aws cloudformation wait stack-update-complete \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}"
echo "✅ CloudFormation updated"
echo ""


# ══════════════════════════════════════════════════════════════════════════════
# PART 2 — Build and push dummy-infra-app Docker image
# ══════════════════════════════════════════════════════════════════════════════
echo "▶ PART 2: Build and push dummy-infra-app..."

cd dummy-infra-app
docker build -t "${DUMMY_APP_REPO}:latest" .
docker push "${DUMMY_APP_REPO}:latest"
cd ..

# Force redeploy
aws ecs update-service \
    --cluster "${CLUSTER}" \
    --service "dummy-infra-app-svc-${ENV}" \
    --force-new-deployment \
    --region "${REGION}"

echo "✅ dummy-infra-app deployed"
echo ""


# ══════════════════════════════════════════════════════════════════════════════
# PART 3 — Package and deploy 5 action Lambda functions
# ══════════════════════════════════════════════════════════════════════════════
echo "▶ PART 3: Package and deploy action Lambdas..."

deploy_lambda() {
    local NAME=$1
    local DIR=$2
    local FUNC_NAME="log-aggregator-${NAME}-${ENV}"

    echo "  Packaging ${FUNC_NAME}..."
    cd "${DIR}"

    # Install dependencies into package/
    rm -rf package && mkdir -p package
    pip install -r requirements.txt -t package/ --quiet 2>/dev/null || true
    cp handler.py package/

    cd package
    zip -r9 "../${NAME}.zip" . -x "*.pyc" -x "__pycache__/*" > /dev/null
    cd ..

    # Update Lambda function code
    aws lambda update-function-code \
        --function-name "${FUNC_NAME}" \
        --zip-file "fileb://${NAME}.zip" \
        --region "${REGION}" > /dev/null

    aws lambda wait function-updated \
        --function-name "${FUNC_NAME}" \
        --region "${REGION}"

    echo "  ✅ ${FUNC_NAME} deployed"
    cd -
}

deploy_lambda "servicenow"     "lambda-actions/servicenow_lambda"
deploy_lambda "ssl"            "lambda-actions/ssl_lambda"
deploy_lambda "password-reset" "lambda-actions/password_reset_lambda"
deploy_lambda "db"             "lambda-actions/db_lambda"
deploy_lambda "compute"        "lambda-actions/compute_lambda"

echo "✅ All 5 action Lambdas deployed"
echo ""


# ══════════════════════════════════════════════════════════════════════════════
# PART 4 — Store ServiceNow credentials in Secrets Manager
# ══════════════════════════════════════════════════════════════════════════════
echo "▶ PART 4: ServiceNow credentials setup..."
echo ""
echo "  Enter your ServiceNow details (or press Enter to skip and use demo mode):"
read -rp "  ServiceNow instance URL [e.g. https://dev12345.service-now.com]: " SNOW_URL
read -rp "  ServiceNow username: " SNOW_USER
read -rsp "  ServiceNow password: " SNOW_PASS
echo ""

if [[ -n "${SNOW_URL}" && -n "${SNOW_USER}" && -n "${SNOW_PASS}" ]]; then
    SNOW_SECRET=$(jq -n \
        --arg url  "${SNOW_URL}" \
        --arg user "${SNOW_USER}" \
        --arg pass "${SNOW_PASS}" \
        '{"instance_url": $url, "username": $user, "password": $pass}')

    aws secretsmanager put-secret-value \
        --secret-id "servicenow/credentials" \
        --secret-string "${SNOW_SECRET}" \
        --region "${REGION}"
    echo "  ✅ ServiceNow credentials stored in Secrets Manager"
else
    echo "  ⚠️  Skipped — Lambdas will run in demo mode (returns mock ticket numbers)"
fi
echo ""


# ══════════════════════════════════════════════════════════════════════════════
# PART 5 — Add action groups to Bedrock agent
#
# This cannot be done fully via CLI (requires console for schema upload).
# Script prints the Lambda ARNs you need for the console steps.
# ══════════════════════════════════════════════════════════════════════════════
echo "▶ PART 5: Bedrock Agent action groups setup..."
echo ""
echo "  ── Lambda ARNs (copy these into Bedrock console) ────────────────────"

for NAME in servicenow ssl password-reset db compute; do
    FUNC="log-aggregator-${NAME}-${ENV}"
    ARN=$(aws lambda get-function \
        --function-name "${FUNC}" \
        --region "${REGION}" \
        --query 'Configuration.FunctionArn' \
        --output text 2>/dev/null || echo "NOT FOUND")
    echo "  ${FUNC}: ${ARN}"
done

echo ""
echo "  ── Manual steps in AWS Bedrock Console ─────────────────────────────"
echo ""
echo "  1. Open: https://console.aws.amazon.com/bedrock/home#/agents/HB8PL0CMXJ"
echo "     (replace HB8PL0CMXJ with your actual agent ID)"
echo ""
echo "  2. Click 'Edit in Agent Builder'"
echo ""
echo "  3. Under 'Instructions', REPLACE the system prompt with:"
echo "     Contents of: bedrock/orchestrator_agent_prompt.txt"
echo ""
echo "  4. Under 'Action groups', add EACH of these 5 groups:"
echo "     For each one: click 'Add action group' and fill in:"
echo ""
echo "     ┌─ Action Group 1: servicenow_action_group"
echo "     │  Lambda: log-aggregator-servicenow-${ENV}"
echo "     │  Schema:  bedrock/action_group_schemas/servicenow.json"
echo "     │"
echo "     ├─ Action Group 2: ssl_remediation_action_group"
echo "     │  Lambda: log-aggregator-ssl-${ENV}"
echo "     │  Schema:  bedrock/action_group_schemas/ssl.json"
echo "     │"
echo "     ├─ Action Group 3: password_reset_action_group"
echo "     │  Lambda: log-aggregator-password-reset-${ENV}"
echo "     │  Schema:  bedrock/action_group_schemas/password_reset.json"
echo "     │"
echo "     ├─ Action Group 4: db_remediation_action_group"
echo "     │  Lambda: log-aggregator-db-${ENV}"
echo "     │  Schema:  bedrock/action_group_schemas/db_remediation.json"
echo "     │"
echo "     └─ Action Group 5: compute_remediation_action_group"
echo "        Lambda: log-aggregator-compute-${ENV}"
echo "        Schema:  bedrock/action_group_schemas/compute_remediation.json"
echo ""
echo "  5. Click 'Prepare' to rebuild the agent"
echo ""
echo "  6. Click 'Create Alias' → name it 'auto-remediation-v1'"
echo "     Copy the new Alias ID"
echo ""
echo "  7. Update Secrets Manager with the new Alias ID:"
echo "     aws secretsmanager put-secret-value \\"
echo "       --secret-id log-aggregator/bedrock-${ENV} \\"
echo "       --secret-string '{\"BEDROCK_AGENT_ID\":\"HB8PL0CMXJ\",\"BEDROCK_AGENT_ALIAS_ID\":\"NEW_ALIAS_ID\"}'"
echo ""
echo "  8. Force redeploy dashboard to pick up new credentials:"
echo "     aws ecs update-service --cluster ${CLUSTER} \\"
echo "       --service log-aggregator-dashboard-svc-${ENV} --force-new-deployment"
echo ""


# ══════════════════════════════════════════════════════════════════════════════
# FINAL — Update dashboard with Fix button (Part 5 UI)
# ══════════════════════════════════════════════════════════════════════════════
echo "▶ FINAL: Dashboard file updates needed..."
echo ""
echo "  Copy these files into your project:"
echo ""
echo "  Dashboard/dashboard_blueprint.py  →  log-aggregator/Dashboard/dashboard_blueprint.py"
echo "  Dashboard/dashboard_patch.js      →  read and apply to dashboard.html (see patch file)"
echo ""
echo "  Then re-ZIP and upload to S3 artifact bucket, then run CodeBuild."
echo ""
echo "══════════════════════════════════════════════════════════"
echo " Deployment Complete!"
echo ""
echo " TEST IT:"
echo "  1. curl -X POST http://<alb-dns>/api/dummy/trigger-error"
echo "         -H 'Content-Type: application/json'"
echo "         -d '{\"error_type\": \"compute_overload\"}'"
echo ""
echo "  2. Wait ~60s for Lambda to process logs"
echo ""
echo "  3. Open http://<alb-dns>/dashboard"
echo "     → Click an error row → Chat opens"
echo "     → Click '⚡ Fix This Error'"
echo "     → Watch agent create ticket + fix the error"
echo "══════════════════════════════════════════════════════════"
