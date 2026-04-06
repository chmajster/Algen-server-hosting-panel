from __future__ import annotations

from flask import Blueprint, render_template
from flask_login import login_required

from panel.models import BillingTransaction
from panel.utils.decorators import active_account_required, roles_required
from panel.utils.query import current_client


client_bp = Blueprint("client", __name__, url_prefix="/client")


@client_bp.route("/")
@login_required
@roles_required("client")
@active_account_required
def dashboard():
    client = current_client()
    recent_transactions = BillingTransaction.query.filter_by(client_id=client.id).order_by(BillingTransaction.created_at.desc()).limit(10).all()
    return render_template("client/dashboard.html", client=client, recent_transactions=recent_transactions)
