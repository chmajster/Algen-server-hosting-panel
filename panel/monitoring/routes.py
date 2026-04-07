from __future__ import annotations

import hmac
from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, jsonify, render_template, request
from flask_login import login_required

from panel.extensions import get_client_ip, limiter
from panel.models import Client, ClientResourceSample, ResourceLimitAlert
from panel.services.client_resources import collect_client_resource_usage
from panel.services.monitoring import collect_server_metrics, service_statuses
from panel.services.resource_limits import resource_usage_report
from panel.services.smoketest import run_app_smoke_test, write_smoke_test_log
from panel.utils.decorators import active_account_required, roles_required
from panel.utils.query import current_client
from panel.utils.security import is_ip_allowed


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


@monitoring_bp.route("/admin/monitoring/clients")
@login_required
@roles_required("administrator")
def admin_clients():
    usage = collect_client_resource_usage()
    usage_by_client = {item["client_id"]: item for item in usage}
    latest_samples = (
        ClientResourceSample.query.order_by(ClientResourceSample.client_id.asc(), ClientResourceSample.created_at.desc()).all()
    )

    latest_by_client: dict[int, ClientResourceSample] = {}
    for sample in latest_samples:
        latest_by_client.setdefault(sample.client_id, sample)

    rows = []
    for client_id, snapshot in usage_by_client.items():
        client = Client.query.get(client_id)
        limit_report = resource_usage_report(client) if client is not None else {}
        rows.append(
            {
                "snapshot": snapshot,
                "latest_sample": latest_by_client.get(client_id),
                "limit_report": limit_report,
            }
        )

    rows.sort(key=lambda item: item["snapshot"]["username"])
    recent_alerts = (
        ResourceLimitAlert.query.order_by(ResourceLimitAlert.triggered_at.desc(), ResourceLimitAlert.id.desc())
        .limit(50)
        .all()
    )
    return render_template("monitoring/admin_clients.html", rows=rows, recent_alerts=recent_alerts)


@monitoring_bp.route("/client/monitoring")
@login_required
@roles_required("client")
@active_account_required
def client_usage():
    client = current_client()
    snapshot = None
    for item in collect_client_resource_usage():
        if item.get("client_id") == client.id:
            snapshot = item
            break

    history = (
        ClientResourceSample.query.filter_by(client_id=client.id)
        .order_by(ClientResourceSample.created_at.desc())
        .limit(30)
        .all()
    )
    return render_template(
        "monitoring/client_usage.html",
        snapshot=snapshot,
        history=history,
        resource_report=resource_usage_report(client),
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
