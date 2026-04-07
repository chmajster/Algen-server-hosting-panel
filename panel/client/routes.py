from __future__ import annotations

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, stream_with_context, url_for
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
from panel.services.event_stream import EVENT_CATEGORIES, EVENT_SEVERITIES, iter_sse_events, query_events
from panel.services.migrations import cancel_migration_job, create_migration_job
from panel.services.onboarding import compute_onboarding_view, update_onboarding_step
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


@client_bp.route("/onboarding", methods=["GET", "POST"])
@login_required
@roles_required("client")
@active_account_required
def onboarding():
    client = current_client()

    if request.method == "POST":
        step_id = (request.form.get("step_id") or "").strip()
        action = (request.form.get("action") or "").strip().lower()
        try:
            update_onboarding_step(client=client, step_id=step_id, action=action, actor=current_user)
            log_activity(
                "client.onboarding_step_update",
                "client_onboarding_state",
                "Klient zaktualizowal krok onboarding",
                entity_id=client.id,
                client=client,
                actor=current_user,
                metadata={"step_id": step_id, "action": action},
            )
            db.session.commit()
            flash("Krok onboarding zaktualizowany.", "success")
        except Exception as exc:
            db.session.rollback()
            flash(f"Nie udalo sie zaktualizowac kroku onboarding: {exc}", "danger")

    view = compute_onboarding_view(client)
    db.session.commit()
    return render_template(
        "client/onboarding.html",
        title="Onboarding",
        client=client,
        steps=view["steps"],
        percent=view["percent"],
        completed=view["completed"],
        total=view["total"],
    )


@client_bp.route("/events")
@login_required
@roles_required("client")
@active_account_required
def events():
    client = current_client()
    category = (request.args.get("category") or "").strip().lower() or None
    severity = (request.args.get("severity") or "").strip().lower() or None
    event_type = (request.args.get("event_type") or "").strip() or None
    search = (request.args.get("search") or "").strip() or None

    rows = query_events(
        client_id=client.id,
        category=category,
        severity=severity,
        event_type=event_type,
        search=search,
        limit=200,
    )
    return render_template(
        "client/events.html",
        title="Zdarzenia",
        rows=rows,
        category=category,
        severity=severity,
        event_type=event_type,
        search=search,
        categories=sorted(EVENT_CATEGORIES),
        severities=sorted(EVENT_SEVERITIES),
    )


@client_bp.route("/events/stream")
@login_required
@roles_required("client")
@active_account_required
def events_stream():
    client = current_client()
    category = (request.args.get("category") or "").strip().lower() or None
    severity = (request.args.get("severity") or "").strip().lower() or None
    event_type = (request.args.get("event_type") or "").strip() or None
    search = (request.args.get("search") or "").strip() or None
    last_id_raw = (request.args.get("last_id") or "0").strip()
    try:
        last_id = int(last_id_raw)
    except ValueError:
        last_id = 0

    def generate():
        yield from iter_sse_events(
            last_id=last_id,
            client_id=client.id,
            category=category,
            severity=severity,
            event_type=event_type,
            search=search,
            max_cycles=30,
            poll_seconds=1.0,
        )

    response = Response(stream_with_context(generate()), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response
