from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from panel.extensions import db
from panel.forms.api_tokens import ApiTokenCreateForm
from panel.models import ApiToken
from panel.models import BillingTransaction
from panel.services.api_tokens import issue_api_token, revoke_api_token
from panel.services.audit import log_activity
from panel.services.billing import financial_enforcement_snapshot
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
    enforcement_states = financial_enforcement_snapshot([service for service in client.services if service.status != "deleted"])
    return render_template(
        "client/dashboard.html",
        client=client,
        recent_transactions=recent_transactions,
        enforcement_states=enforcement_states,
    )


@client_bp.route("/api-tokens", methods=["GET", "POST"])
@login_required
@roles_required("client")
@active_account_required
def api_tokens():
    form = ApiTokenCreateForm()
    created_token = None

    if form.validate_on_submit():
        token, created_token = issue_api_token(user=current_user, name=(form.name.data or "").strip())
        log_activity(
            "api_tokens.create",
            "api_token",
            f"Utworzono token API {token.name}",
            entity_id=token.id,
            client=current_user.client_profile,
            actor=current_user,
        )
        db.session.commit()
        flash("Token API zostal utworzony. Skopiuj go teraz - pozniej nie bedzie widoczny.", "success")

    active_tokens = (
        ApiToken.query.filter_by(user_id=current_user.id, revoked_at=None)
        .order_by(ApiToken.created_at.desc())
        .all()
    )
    revoked_tokens = (
        ApiToken.query.filter(ApiToken.user_id == current_user.id, ApiToken.revoked_at.isnot(None))
        .order_by(ApiToken.revoked_at.desc())
        .all()
    )
    return render_template(
        "client/api_tokens.html",
        form=form,
        created_token=created_token,
        active_tokens=active_tokens,
        revoked_tokens=revoked_tokens,
    )


@client_bp.route("/api-tokens/<int:token_id>/revoke", methods=["POST"])
@login_required
@roles_required("client")
@active_account_required
def api_token_revoke(token_id: int):
    token = ApiToken.query.get_or_404(token_id)
    if token.user_id != current_user.id:
        abort(404)

    if token.revoked_at is None:
        revoke_api_token(token)
        log_activity(
            "api_tokens.revoke",
            "api_token",
            f"Cofnieto token API {token.name}",
            entity_id=token.id,
            client=current_user.client_profile,
            actor=current_user,
        )
        db.session.commit()
        flash("Token API zostal cofniety.", "info")

    return redirect(url_for("client.api_tokens"))
