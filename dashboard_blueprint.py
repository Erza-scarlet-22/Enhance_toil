# Dashboard Flask blueprint for the Log Aggregator.
#
# Routes:
#   GET  /dashboard                — Serves the single-page dashboard HTML.
#   GET  /api/dashboard-data       — Returns the JSON payload for the front-end charts.
#   GET  /api/dashboard-report.pdf — Downloads a landscape PDF summary report.
#   POST /api/chat-insights        — Proxies a question to the AWS Bedrock agent.
#   POST /api/fix-error            — NEW: Triggers auto-remediation via Bedrock orchestrator.

from datetime import date
from flask import Blueprint, jsonify, render_template, request, send_file

try:
    from bedrock_chat_service import generate_error_insight  # type: ignore[reportMissingImports]
    BEDROCK_CHAT_AVAILABLE = True
except Exception:
    BEDROCK_CHAT_AVAILABLE = False

try:
    from dashboard_pdf_service import build_dashboard_pdf, REPORTLAB_AVAILABLE  # type: ignore[reportMissingImports]
except Exception:
    REPORTLAB_AVAILABLE = False
    def build_dashboard_pdf(_): ...

from dashboard_data_service import build_dashboard_payload  # type: ignore[reportMissingImports]


def create_dashboard_blueprint(conversion_dir: str, run_conversion_outputs):

    dashboard_bp = Blueprint('dashboard', __name__, template_folder='templates')

    # ── Existing routes ───────────────────────────────────────────────────────

    @dashboard_bp.route('/dashboard', methods=['GET'])
    def dashboard_page():
        return render_template('dashboard.html')

    @dashboard_bp.route('/api/dashboard-data', methods=['GET'])
    def dashboard_data():
        payload = build_dashboard_payload(conversion_dir, run_conversion_outputs, request.args)
        return jsonify(payload), 200

    @dashboard_bp.route('/api/dashboard-report.pdf', methods=['GET'])
    def dashboard_report_pdf():
        if not REPORTLAB_AVAILABLE:
            return jsonify({'error': 'PDF export unavailable. Install reportlab.'}), 503
        payload    = build_dashboard_payload(conversion_dir, run_conversion_outputs, request.args)
        pdf_buffer = build_dashboard_pdf(payload)
        filename   = f"error-dashboard-report-{date.today().isoformat()}.pdf"
        return send_file(pdf_buffer, mimetype='application/pdf',
                         as_attachment=True, download_name=filename)

    @dashboard_bp.route('/api/chat-insights', methods=['POST'])
    def chat_insights():
        """Forward a user question to the Bedrock orchestrator agent."""
        payload       = request.get_json(silent=True) or {}
        error_context = payload.get('error') or {}
        user_message  = (payload.get('message') or '').strip()
        history       = payload.get('history') or []
        session_id    = (payload.get('sessionId') or '').strip()

        if not isinstance(error_context, dict):
            return jsonify({'error': 'Invalid payload: error context must be an object'}), 400
        if not isinstance(history, list):
            return jsonify({'error': 'Invalid payload: history must be a list'}), 400
        if not user_message:
            user_message = 'Provide insights and remediation steps for this selected error.'

        if not BEDROCK_CHAT_AVAILABLE:
            return jsonify({'error': 'Bedrock chat service unavailable.'}), 503

        try:
            reply_text, metadata = generate_error_insight(
                error_context, user_message, history, session_id
            )
            return jsonify({
                'reply':     reply_text,
                'provider':  'aws-bedrock-agent',
                'modelId':   metadata.get('model_id', ''),
                'region':    metadata.get('region', ''),
                'sessionId': metadata.get('session_id', session_id),
            }), 200
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500

    # ── NEW: Auto-fix endpoint ────────────────────────────────────────────────

    @dashboard_bp.route('/api/fix-error', methods=['POST'])
    def fix_error():
        """
        Trigger auto-remediation via the Bedrock orchestrator agent.

        Request body:
        {
          "error": { ...error row from dashboard... },
          "auto_fix": true
        }

        The agent will:
          1. Create a ServiceNow incident
          2. Call the appropriate remediation Lambda
          3. Return ticket number + action taken
        """
        if not BEDROCK_CHAT_AVAILABLE:
            return jsonify({'error': 'Bedrock agent unavailable. Cannot auto-fix.'}), 503

        payload       = request.get_json(silent=True) or {}
        error_context = payload.get('error') or {}

        if not error_context:
            return jsonify({'error': 'No error context provided'}), 400

        # Build a fix instruction message for the orchestrator agent
        error_type  = _classify_error_type(error_context)
        fix_message = (
            f"Please fix this error automatically. "
            f"Error type: {error_type}. "
            f"Description: {error_context.get('Description', '')}. "
            f"Status code: {error_context.get('Status Code', '')}. "
            f"This has occurred {error_context.get('Count', 1)} times. "
            f"Create a ServiceNow incident and apply the appropriate remediation."
        )

        try:
            reply_text, metadata = generate_error_insight(
                error_context,
                fix_message,
                [],
                None,
            )
            return jsonify({
                'reply':      reply_text,
                'provider':   'aws-bedrock-agent',
                'sessionId':  metadata.get('session_id', ''),
                'error_type': error_type,
                'status':     'remediation_initiated',
            }), 200

        except Exception as exc:
            return jsonify({'error': str(exc), 'status': 'failed'}), 500

    return dashboard_bp


# ── Helper ────────────────────────────────────────────────────────────────────

def _classify_error_type(error_context: dict) -> str:
    """Map error context to a known error type for the orchestrator agent."""
    status_code = str(error_context.get('Status Code', ''))
    error_code  = str(error_context.get('Error Code', ''))
    description = (error_context.get('Description') or '').lower()

    # Match by error code first (most precise)
    code_map = {
        '9010': 'ssl_expired',
        '9011': 'ssl_expiring',
        '9012': 'password_expired',
        '9013': 'db_storage',
        '9014': 'db_connection',
        '9015': 'compute_overload',
    }
    if error_code in code_map:
        return code_map[error_code]

    # Match by status code
    status_map = {
        '495': 'ssl_expired',
        '507': 'db_storage',
        '504': 'db_connection',
        '503': 'compute_overload',
        '401': 'password_expired',
    }
    if status_code in status_map:
        return status_map[status_code]

    # Match by description keywords
    if 'ssl' in description or 'cert' in description:
        return 'ssl_expired'
    if 'password' in description or 'auth' in description:
        return 'password_expired'
    if 'storage' in description or 'capacity' in description:
        return 'db_storage'
    if 'connection' in description or 'pool' in description:
        return 'db_connection'
    if 'cpu' in description or 'memory' in description or 'compute' in description:
        return 'compute_overload'

    return 'unknown'
