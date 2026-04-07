from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from panel.extensions import db
from panel.forms.api_tokens import ApiTokenCreateForm
from panel.forms.migrations import MigrationWizardForm
from panel.models import ApiToken
from panel.models import BillingTransaction
from panel.models import MigrationJob
from panel.services.api_tokens import API_TOKEN_SCOPES, issue_api_token, normalize_api_scopes, revoke_api_token
from panel.services.audit import log_activity
from panel.services.billing import financial_enforcement_snapshot
from panel.services.automations import execute_automation_rules
from panel.services.migrations import cancel_migration_job, create_migration_job
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
    form.scopes.choices = API_TOKEN_SCOPES
    created_token = None

    if form.validate_on_submit():
        token, created_token = issue_api_token(
            user=current_user,
            name=(form.name.data or "").strip(),
            scopes=normalize_api_scopes(form.scopes.data),
        )
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
        available_scopes=API_TOKEN_SCOPES,
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


@client_bp.route("/migrations", methods=["GET", "POST"])
@login_required
@roles_required("client")
@active_account_required
def migrations():
    client = current_client()
    form = MigrationWizardForm()
    if form.validate_on_submit():
        job, error = create_migration_job(
            client=client,
            requested_by=current_user,
            source_provider=form.source_provider.data,
            source_hostname=form.source_hostname.data,
            source_username=form.source_username.data,
            source_password=form.source_password.data,
            source_path=form.source_path.data,
            notes=form.notes.data,
        )
        if error:
            flash(error, "danger")
        else:
            log_activity(
                "migration.job_create",
                "migration_job",
                "Klient utworzyl zgloszenie migracji",
                entity_id=job.id,
                client=client,
                actor=current_user,
                metadata={"source_provider": job.source_provider, "status": job.status},
            )
            execute_automation_rules(
                trigger_event="migration.job_created",
                payload={"job_id": job.id, "client_id": client.id, "status": job.status},
                client=client,
                actor=current_user,
            )
            db.session.commit()
            flash("Zgloszenie migracji zostalo utworzone.", "success")
            return redirect(url_for("client.migrations"))

    jobs = (
        MigrationJob.query.filter_by(client_id=client.id)
        .order_by(MigrationJob.created_at.desc())
        .all()
    )
    return render_template(
        "client/migrations.html",
        form=form,
        jobs=jobs,
        title="Migracje",
    )


@client_bp.route("/migrations/<int:job_id>/cancel", methods=["POST"])
@login_required
@roles_required("client")
@active_account_required
def migration_cancel(job_id: int):
    client = current_client()
    job = MigrationJob.query.get_or_404(job_id)
    if job.client_id != client.id:
        abort(404)

    reason = (request.form.get("reason") or "Anulowano przez klienta").strip()
    if cancel_migration_job(job, reason=reason):
        log_activity(
            "migration.job_cancel",
            "migration_job",
            "Klient anulowal zgloszenie migracji",
            entity_id=job.id,
            client=client,
            actor=current_user,
            metadata={"reason": reason[:255]},
        )
        execute_automation_rules(
            trigger_event="migration.job_cancelled",
            payload={"job_id": job.id, "client_id": client.id, "status": job.status},
            client=client,
            actor=current_user,
        )
        db.session.commit()
        flash("Migracja zostala anulowana.", "info")
    else:
        flash("Tej migracji nie mozna juz anulowac.", "warning")

    return redirect(url_for("client.migrations"))
