from __future__ import annotations

from flask import Blueprint, render_template
from flask_login import login_required

from panel.services.monitoring import collect_server_metrics, service_statuses
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
