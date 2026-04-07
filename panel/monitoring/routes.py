from __future__ import annotations

import hmac
from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, jsonify, render_template, request
from flask_login import login_required

from panel.extensions import get_client_ip, limiter
from panel.services.monitoring import collect_server_metrics, service_statuses
from panel.services.smoketest import run_app_smoke_test, write_smoke_test_log
from panel.utils.security import is_ip_allowed
from panel.utils.decorators import roles_required


monitoring_bp = Blueprint("monitoring", __name__)


@monitoring_bp.route("/admin/monitoring")
@login_required
@roles_required("administrator")
def index():
    return render_template(
        "monitoring/index.html",
        metrics=collect_server_metrics(),
        service_states=service_statuses(),
    )


def _smoke_token_is_valid() -> tuple[bool, bool]:
    expected = str(current_app.config.get("SMOKE_TEST_API_TOKEN", "")).strip()
    if not expected:
        return False, False
    provided = request.headers.get("X-Smoke-Test-Token", "")
    provided = provided.strip()
    if not provided:
        return True, False
    return True, hmac.compare_digest(expected, provided)


def _smoke_ip_is_valid() -> bool:
    return is_ip_allowed(get_client_ip(), current_app.config.get("SMOKE_TEST_API_ALLOWLIST"), default_allow=False)


@monitoring_bp.route("/monitoring/smoke-test.json", methods=["GET"])
@limiter.limit(lambda: current_app.config.get("SMOKE_TEST_API_RATELIMIT", "5 per minute"))
def smoke_test_json():
    configured, valid = _smoke_token_is_valid()
    if not configured:
        abort(404)
    if not valid:
        abort(403)
    if not _smoke_ip_is_valid():
        abort(403)

    result = run_app_smoke_test()
    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "source": "http",
        **result.as_dict(),
    }
    log_error = write_smoke_test_log(result, source="http")
    if log_error:
        payload["log_error"] = log_error

    return jsonify(payload), 200 if result.success else 503
