from __future__ import annotations

import json
from datetime import datetime

from flask import Blueprint, Response, flash, redirect, render_template, request, session, stream_with_context, url_for
from flask_login import current_user, login_required

from panel.extensions import db
from panel.models import (
    Client,
    ClientOnboardingState,
    ComplianceResult,
    ComplianceRun,
    ComplianceChecklistItem,
    DataLegalHold,
    DisasterRecoveryCheckRun,
    DisasterRecoveryProfile,
    PolicyDocument,
    PolicyEvaluation,
    RetentionCleanupRun,
    User,
    VaultSecret,
    VaultSecretVersion,
)
from panel.services.audit import log_activity
from panel.services.compliance import (
    link_checklist_evidence,
    normalize_control_status,
    run_compliance_checks,
    upsert_checklist_item,
)
from panel.services.dr_readiness import evaluate_dr_readiness, run_dr_readiness_checks, run_failover_simulation, update_dr_profile
from panel.services.event_stream import EVENT_CATEGORIES, EVENT_SEVERITIES, iter_sse_events, query_events
from panel.services.onboarding import compute_onboarding_view
from panel.services.policy_engine import (
    activate_policy,
    archive_policy,
    evaluate_policies,
    parse_policy_definition,
    policy_state,
    rollback_policy,
    validate_policy_definition,
)
from panel.services.retention import (
    create_legal_hold,
    release_legal_hold,
    retention_resource_choices,
    run_retention_cleanup,
    resolve_client_policy,
    upsert_client_policy,
)
from panel.services.secrets_vault import SECRET_TYPES, create_secret, reveal_secret_value, rotate_secret, run_rotation_schedule
from panel.utils.decorators import roles_required


governance_bp = Blueprint("governance", __name__, url_prefix="/admin/governance")


def _safe_int(raw: str | None, *, default: int | None = None) -> int | None:
    text = (raw or "").strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        return default


def _client_choices() -> list[tuple[int, str]]:
    rows = Client.query.order_by(Client.id.asc()).all()
    return [(row.id, row.user.username if row.user else f"client-{row.id}") for row in rows]


def _owner_choices() -> list[tuple[int, str]]:
    rows = User.query.order_by(User.username.asc()).all()
    return [(row.id, row.username) for row in rows]


def _client_from_form(name: str = "client_id") -> Client | None:
    client_id = _safe_int(request.form.get(name), default=None)
    if client_id is None or client_id <= 0:
        return None
    return Client.query.get(client_id)


@governance_bp.route("/retention")
@login_required
@roles_required("administrator")
def retention_index():
    clients = _client_choices()
    selected_client_id = _safe_int(request.args.get("client_id"), default=clients[0][0] if clients else None)
    selected_client = Client.query.get(selected_client_id) if selected_client_id else None

    resource_policies = []
    if selected_client is not None:
        for resource_key, _label in retention_resource_choices():
            resource_policies.append(resolve_client_policy(selected_client.id, resource_key))

    runs = RetentionCleanupRun.query.order_by(RetentionCleanupRun.created_at.desc()).limit(50).all()
    holds_query = DataLegalHold.query.order_by(DataLegalHold.created_at.desc())
    if selected_client is not None:
        holds_query = holds_query.filter(
            (DataLegalHold.client_id == selected_client.id) | (DataLegalHold.client_id.is_(None))
        )
    holds = holds_query.limit(100).all()

    return render_template(
        "admin/governance_retention.html",
        title="Retention i privacy",
        clients=clients,
        selected_client=selected_client,
        resource_policies=resource_policies,
        resources=retention_resource_choices(),
        runs=runs,
        holds=holds,
    )


@governance_bp.route("/retention/policies/save", methods=["POST"])
@login_required
@roles_required("administrator")
def retention_policy_save():
    client = _client_from_form()
    resource_type = (request.form.get("resource_type") or "").strip().lower()
    if client is None or not resource_type:
        flash("Wybierz klienta i resource type.", "danger")
        return redirect(url_for("governance.retention_index"))

    anonymize_after_days = _safe_int(request.form.get("anonymize_after_days"), default=None)
    archive_after_days = _safe_int(request.form.get("archive_after_days"), default=None)
    delete_after_days = _safe_int(request.form.get("delete_after_days"), default=None)
    legal_hold_enabled = request.form.get("legal_hold_enabled") == "1"
    is_active = request.form.get("is_active") == "1"
    notes = (request.form.get("notes") or "").strip()

    row = upsert_client_policy(
        client_id=client.id,
        resource_type=resource_type,
        anonymize_after_days=anonymize_after_days,
        archive_after_days=archive_after_days,
        delete_after_days=delete_after_days,
        legal_hold_enabled=legal_hold_enabled,
        is_active=is_active,
        notes=notes,
    )
    log_activity(
        "governance.retention_policy_save",
        "tenant_retention_policy",
        f"Zapisano polityke retencji {resource_type} dla klienta {client.id}",
        entity_id=row.id,
        actor=current_user,
        client=client,
        metadata={
            "resource_type": resource_type,
            "anonymize_after_days": anonymize_after_days,
            "archive_after_days": archive_after_days,
            "delete_after_days": delete_after_days,
            "legal_hold_enabled": legal_hold_enabled,
            "is_active": is_active,
        },
    )
    db.session.commit()
    flash("Polityka retencji zapisana.", "success")
    return redirect(url_for("governance.retention_index", client_id=client.id))


@governance_bp.route("/retention/cleanup", methods=["POST"])
@login_required
@roles_required("administrator")
def retention_cleanup_run():
    client = _client_from_form()
    run_key = (request.form.get("run_key") or "").strip() or None
    if not run_key:
        run_key = f"manual-{datetime.utcnow().strftime('%Y%m%d%H%M')}"

    result = run_retention_cleanup(run_key=run_key, triggered_by=current_user, client_id=client.id if client else None)
    log_activity(
        "governance.retention_cleanup_run",
        "retention_cleanup_run",
        "Uruchomiono cleanup retencji",
        entity_id=result.get("run_id"),
        actor=current_user,
        client=client,
        metadata={"run_key": run_key, "result": result},
    )
    db.session.commit()

    if result.get("idempotent"):
        flash("Cleanup pominięty: run_key juz wykonany (idempotent).", "info")
    else:
        flash("Cleanup retencji zakonczony.", "success")
    return redirect(url_for("governance.retention_index", client_id=client.id if client else None))


@governance_bp.route("/retention/holds/create", methods=["POST"])
@login_required
@roles_required("administrator")
def retention_hold_create():
    client = _client_from_form()
    resource_type = (request.form.get("resource_type") or "").strip().lower()
    resource_id = (request.form.get("resource_id") or "").strip() or None
    reason = (request.form.get("reason") or "").strip()

    expires_raw = (request.form.get("expires_at") or "").strip()
    expires_at = None
    if expires_raw:
        try:
            expires_at = datetime.strptime(expires_raw, "%Y-%m-%d")
        except ValueError:
            flash("Niepoprawny format daty expiry (YYYY-MM-DD).", "warning")

    if not resource_type or not reason:
        flash("Resource type i reason sa wymagane dla legal hold.", "danger")
        return redirect(url_for("governance.retention_index", client_id=client.id if client else None))

    row = create_legal_hold(
        client_id=client.id if client else None,
        resource_type=resource_type,
        resource_id=resource_id,
        reason=reason,
        created_by=current_user,
        expires_at=expires_at,
    )
    log_activity(
        "governance.legal_hold_create",
        "data_legal_hold",
        "Utworzono legal hold",
        entity_id=row.id,
        actor=current_user,
        client=client,
        metadata={"resource_type": resource_type, "resource_id": resource_id, "expires_at": expires_raw or None},
    )
    db.session.commit()
    flash("Legal hold utworzony.", "success")
    return redirect(url_for("governance.retention_index", client_id=client.id if client else None))


@governance_bp.route("/retention/holds/<int:hold_id>/release", methods=["POST"])
@login_required
@roles_required("administrator")
def retention_hold_release(hold_id: int):
    row = DataLegalHold.query.get_or_404(hold_id)
    release_legal_hold(row)
    log_activity(
        "governance.legal_hold_release",
        "data_legal_hold",
        "Zwolniono legal hold",
        entity_id=row.id,
        actor=current_user,
        client=row.client,
        metadata={"resource_type": row.resource_type, "resource_id": row.resource_id},
    )
    db.session.commit()
    flash("Legal hold zostal zwolniony.", "info")
    return redirect(url_for("governance.retention_index", client_id=row.client_id))


@governance_bp.route("/secrets")
@login_required
@roles_required("administrator")
def secrets_index():
    clients = _client_choices()
    selected_client_id = _safe_int(request.args.get("client_id"), default=None)

    query = VaultSecret.query.order_by(VaultSecret.created_at.desc())
    if selected_client_id is not None and selected_client_id > 0:
        query = query.filter(VaultSecret.client_id == selected_client_id)

    secrets_rows = query.limit(200).all()
    current_versions = {
        row.secret_id: row
        for row in VaultSecretVersion.query.filter_by(is_current=True)
        .order_by(VaultSecretVersion.secret_id.asc(), VaultSecretVersion.version.desc())
        .all()
    }

    revealed_secret_value = session.pop("vault_reveal_value", None)
    revealed_secret_name = session.pop("vault_reveal_name", None)

    due_summary = run_rotation_schedule(actor=None, auto_rotate=False)

    return render_template(
        "admin/governance_secrets.html",
        title="Secrets vault",
        clients=clients,
        selected_client_id=selected_client_id,
        secrets_rows=secrets_rows,
        current_versions=current_versions,
        secret_types=SECRET_TYPES,
        due_summary=due_summary,
        revealed_secret_value=revealed_secret_value,
        revealed_secret_name=revealed_secret_name,
    )


@governance_bp.route("/secrets/create", methods=["POST"])
@login_required
@roles_required("administrator")
def secrets_create():
    client = _client_from_form()
    name = (request.form.get("name") or "").strip()
    secret_type = (request.form.get("secret_type") or "other").strip().lower()
    plain_value = request.form.get("plain_value") or ""
    description = (request.form.get("description") or "").strip()
    rotation_interval_days = _safe_int(request.form.get("rotation_interval_days"), default=None)

    if not name or not plain_value:
        flash("Nazwa i wartosc sekretu sa wymagane.", "danger")
        return redirect(url_for("governance.secrets_index", client_id=client.id if client else None))

    try:
        row = create_secret(
            client=client,
            name=name,
            secret_type=secret_type,
            plain_value=plain_value,
            created_by=current_user,
            rotation_interval_days=rotation_interval_days,
            description=description,
        )
    except Exception as exc:
        db.session.rollback()
        flash(f"Nie udalo sie utworzyc sekretu: {exc}", "danger")
        return redirect(url_for("governance.secrets_index", client_id=client.id if client else None))

    log_activity(
        "governance.secret_create",
        "vault_secret",
        f"Utworzono sekret {row.name}",
        entity_id=row.id,
        actor=current_user,
        client=client,
        metadata={"secret_type": row.secret_type, "rotation_interval_days": row.rotation_interval_days},
    )
    db.session.commit()
    flash("Sekret zostal utworzony w vault.", "success")
    return redirect(url_for("governance.secrets_index", client_id=client.id if client else None))


@governance_bp.route("/secrets/<int:secret_id>/rotate", methods=["POST"])
@login_required
@roles_required("administrator")
def secrets_rotate(secret_id: int):
    row = VaultSecret.query.get_or_404(secret_id)
    plain_value = request.form.get("plain_value") or ""
    reason = (request.form.get("reason") or "").strip()
    if not plain_value:
        flash("Podaj nowa wartosc sekretu do rotacji.", "danger")
        return redirect(url_for("governance.secrets_index", client_id=row.client_id))

    try:
        version = rotate_secret(secret=row, plain_value=plain_value, rotated_by=current_user, reason=reason)
    except Exception as exc:
        db.session.rollback()
        flash(f"Rotacja sekretu nie powiodla sie: {exc}", "danger")
        return redirect(url_for("governance.secrets_index", client_id=row.client_id))

    log_activity(
        "governance.secret_rotate",
        "vault_secret",
        f"Zrotowano sekret {row.name}",
        entity_id=row.id,
        actor=current_user,
        client=row.client,
        metadata={"new_version": version.version, "reason": reason or None},
    )
    db.session.commit()
    flash("Sekret zostal zrotowany.", "success")
    return redirect(url_for("governance.secrets_index", client_id=row.client_id))


@governance_bp.route("/secrets/<int:secret_id>/reveal", methods=["POST"])
@login_required
@roles_required("administrator")
def secrets_reveal(secret_id: int):
    row = VaultSecret.query.get_or_404(secret_id)
    try:
        plain_value = reveal_secret_value(row, revealed_by=current_user)
    except Exception as exc:
        db.session.rollback()
        flash(f"Nie udalo sie odczytac sekretu: {exc}", "danger")
        return redirect(url_for("governance.secrets_index", client_id=row.client_id))

    log_activity(
        "governance.secret_reveal",
        "vault_secret",
        f"Jednorazowe ujawnienie sekretu {row.name}",
        entity_id=row.id,
        actor=current_user,
        client=row.client,
        metadata={"version": row.current_version},
    )
    db.session.commit()

    session["vault_reveal_name"] = row.name
    session["vault_reveal_value"] = plain_value
    return redirect(url_for("governance.secrets_index", client_id=row.client_id))


@governance_bp.route("/secrets/rotation/run", methods=["POST"])
@login_required
@roles_required("administrator")
def secrets_rotation_run():
    auto_rotate = request.form.get("auto_rotate") == "1"
    summary = run_rotation_schedule(actor=current_user, auto_rotate=auto_rotate)
    log_activity(
        "governance.secrets_rotation_schedule",
        "vault_secret",
        "Uruchomiono skan rotacji sekretow",
        actor=current_user,
        metadata=summary,
    )
    db.session.commit()
    flash(
        f"Rotacja sekretow: due={summary['due']}, rotated={summary['rotated']}, errors={summary['errors']}",
        "info",
    )
    return redirect(url_for("governance.secrets_index"))


@governance_bp.route("/events")
@login_required
@roles_required("administrator")
def events_index():
    clients = _client_choices()
    client_id = _safe_int(request.args.get("client_id"), default=None)
    category = (request.args.get("category") or "").strip().lower() or None
    severity = (request.args.get("severity") or "").strip().lower() or None
    event_type = (request.args.get("event_type") or "").strip() or None
    search = (request.args.get("search") or "").strip() or None

    rows = query_events(
        client_id=client_id,
        category=category,
        severity=severity,
        event_type=event_type,
        search=search,
        limit=200,
    )

    return render_template(
        "admin/governance_events.html",
        title="Event stream",
        clients=clients,
        selected_client_id=client_id,
        category=category,
        severity=severity,
        event_type=event_type,
        search=search,
        categories=sorted(EVENT_CATEGORIES),
        severities=sorted(EVENT_SEVERITIES),
        rows=rows,
    )


@governance_bp.route("/events/stream")
@login_required
@roles_required("administrator")
def events_stream():
    client_id = _safe_int(request.args.get("client_id"), default=None)
    category = (request.args.get("category") or "").strip().lower() or None
    severity = (request.args.get("severity") or "").strip().lower() or None
    event_type = (request.args.get("event_type") or "").strip() or None
    search = (request.args.get("search") or "").strip() or None
    last_id = _safe_int(request.args.get("last_id"), default=0) or 0

    def generate():
        yield from iter_sse_events(
            last_id=last_id,
            client_id=client_id,
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


@governance_bp.route("/compliance")
@login_required
@roles_required("administrator")
def compliance_index():
    client_id = _safe_int(request.args.get("client_id"), default=None)
    clients = _client_choices()
    owner_choices = _owner_choices()

    runs_query = ComplianceRun.query.order_by(ComplianceRun.created_at.desc())
    if client_id is not None and client_id > 0:
        runs_query = runs_query.filter(ComplianceRun.client_id == client_id)
    runs = runs_query.limit(50).all()

    latest_run = runs[0] if runs else None
    latest_results = []
    if latest_run is not None:
        latest_results = (
            ComplianceResult.query.filter_by(run_id=latest_run.id)
            .order_by(ComplianceResult.check_code.asc())
            .all()
        )

    checklist_query = ComplianceChecklistItem.query.order_by(
        ComplianceChecklistItem.due_date.asc(),
        ComplianceChecklistItem.id.asc(),
    )
    if client_id is not None and client_id > 0:
        checklist_query = checklist_query.filter(ComplianceChecklistItem.client_id == client_id)
    checklist_items = checklist_query.limit(300).all()

    return render_template(
        "admin/governance_compliance.html",
        title="Compliance center",
        clients=clients,
        owner_choices=owner_choices,
        selected_client_id=client_id,
        runs=runs,
        latest_run=latest_run,
        latest_results=latest_results,
        checklist_items=checklist_items,
        control_states=sorted([
            "not_started",
            "in_progress",
            "compliant",
            "partial",
            "non_compliant",
        ]),
    )


@governance_bp.route("/compliance/checklist/save", methods=["POST"])
@login_required
@roles_required("administrator")
def compliance_checklist_save():
    client = _client_from_form()
    control_code = (request.form.get("control_code") or "").strip().lower()
    title = (request.form.get("title") or "").strip()
    description = (request.form.get("description") or "").strip()
    status = normalize_control_status((request.form.get("status") or "").strip().lower())

    owner = None
    owner_id = _safe_int(request.form.get("owner_user_id"), default=None)
    if owner_id is not None and owner_id > 0:
        owner = User.query.get(owner_id)

    due_date = None
    due_date_raw = (request.form.get("due_date") or "").strip()
    if due_date_raw:
        try:
            due_date = datetime.strptime(due_date_raw, "%Y-%m-%d").date()
        except ValueError:
            flash("Niepoprawny format due_date (YYYY-MM-DD).", "warning")

    try:
        row = upsert_checklist_item(
            client_id=client.id if client else None,
            control_code=control_code,
            title=title,
            description=description,
            status=status,
            owner=owner,
            due_date=due_date,
            actor=current_user,
        )
    except Exception as exc:
        db.session.rollback()
        flash(f"Nie udalo sie zapisac kontrolki compliance: {exc}", "danger")
        return redirect(url_for("governance.compliance_index", client_id=client.id if client else None))

    log_activity(
        "governance.compliance_control_save",
        "compliance_checklist_item",
        f"Zapisano kontrolke compliance {row.control_code}",
        entity_id=row.id,
        actor=current_user,
        client=row.client,
        metadata={
            "control_code": row.control_code,
            "status": row.status,
            "owner_user_id": row.owner_user_id,
            "due_date": row.due_date.isoformat() if row.due_date else None,
        },
    )
    db.session.commit()
    flash("Kontrolka compliance zostala zapisana.", "success")
    return redirect(url_for("governance.compliance_index", client_id=row.client_id))


@governance_bp.route("/compliance/checklist/<int:item_id>/evidence/link", methods=["POST"])
@login_required
@roles_required("administrator")
def compliance_checklist_evidence_link(item_id: int):
    item = ComplianceChecklistItem.query.get_or_404(item_id)
    evidence_type = (request.form.get("evidence_type") or "").strip().lower()
    reference_id = (request.form.get("reference_id") or "").strip()

    try:
        row = link_checklist_evidence(
            checklist_item=item,
            evidence_type=evidence_type,
            reference_id=reference_id,
            actor=current_user,
        )
    except Exception as exc:
        db.session.rollback()
        flash(f"Nie udalo sie podpiac evidence: {exc}", "danger")
        return redirect(url_for("governance.compliance_index", client_id=item.client_id))

    log_activity(
        "governance.compliance_evidence_link",
        "compliance_evidence_link",
        "Podpieto evidence do kontrolki compliance",
        entity_id=row.id,
        actor=current_user,
        client=item.client,
        metadata={
            "checklist_item_id": item.id,
            "evidence_type": row.evidence_type,
            "reference_id": row.reference_id,
        },
    )
    db.session.commit()
    flash("Evidence zostalo podpiete.", "success")
    return redirect(url_for("governance.compliance_index", client_id=item.client_id))


@governance_bp.route("/compliance/run", methods=["POST"])
@login_required
@roles_required("administrator")
def compliance_run():
    client = _client_from_form()
    run = run_compliance_checks(actor=current_user, client_id=client.id if client else None)
    log_activity(
        "governance.compliance_run",
        "compliance_run",
        "Uruchomiono compliance checks",
        entity_id=run.id,
        actor=current_user,
        client=client,
        metadata={"status": run.status, "score": run.score},
    )
    db.session.commit()
    flash(f"Compliance run zakonczony: status={run.status}, score={run.score}", "info")
    return redirect(url_for("governance.compliance_index", client_id=client.id if client else None))


@governance_bp.route("/policies")
@login_required
@roles_required("administrator")
def policies_index():
    policies = PolicyDocument.query.order_by(PolicyDocument.created_at.desc()).limit(200).all()
    evaluations = PolicyEvaluation.query.order_by(PolicyEvaluation.created_at.desc()).limit(100).all()
    clients = _client_choices()
    test_result = session.pop("governance_policy_test_result", None)
    return render_template(
        "admin/governance_policies.html",
        title="Policy-as-code",
        policies=policies,
        evaluations=evaluations,
        clients=clients,
        test_result=test_result,
        policy_state=policy_state,
    )


@governance_bp.route("/policies/create", methods=["POST"])
@login_required
@roles_required("administrator")
def policies_create():
    name = (request.form.get("name") or "").strip()
    scope = (request.form.get("scope") or "global").strip().lower()
    client = _client_from_form()
    definition_raw = request.form.get("definition_json") or "{}"
    enforcement_mode = (request.form.get("enforcement_mode") or "advisory").strip().lower()
    lifecycle_state = (request.form.get("lifecycle_state") or "draft").strip().lower()
    version = (request.form.get("version") or "v1").strip() or "v1"
    description = (request.form.get("description") or "").strip()

    if scope not in {"global", "tenant"}:
        scope = "global"
    if scope == "tenant" and client is None:
        flash("Dla scope=tenant wybierz klienta.", "danger")
        return redirect(url_for("governance.policies_index"))
    if enforcement_mode not in {"advisory", "enforce"}:
        enforcement_mode = "advisory"
    if lifecycle_state not in {"draft", "active", "archived"}:
        lifecycle_state = "draft"

    try:
        definition = parse_policy_definition(definition_raw)
        errors = validate_policy_definition(definition)
        if errors:
            raise ValueError("; ".join(errors))
    except Exception as exc:
        flash(f"Niepoprawna definicja policy JSON: {exc}", "danger")
        return redirect(url_for("governance.policies_index"))

    if not name:
        flash("Nazwa policy jest wymagana.", "danger")
        return redirect(url_for("governance.policies_index"))

    row = PolicyDocument(
        name=name[:120],
        scope=scope,
        client=client if scope == "tenant" else None,
        version=version[:32],
        enforcement_mode=enforcement_mode,
        is_active=False,
        description=description[:255] or None,
        definition_json=definition,
        created_by=current_user,
        updated_by=current_user,
    )
    db.session.add(row)
    db.session.flush()

    if lifecycle_state == "active":
        try:
            activate_policy(row, actor=current_user)
        except Exception as exc:
            db.session.rollback()
            flash(f"Aktywacja policy nieudana: {exc}", "danger")
            return redirect(url_for("governance.policies_index"))
    elif lifecycle_state == "archived":
        archive_policy(row, actor=current_user)
    else:
        definition_json = dict(row.definition_json or {})
        definition_json["_meta"] = {"state": "draft", "updated_at": datetime.utcnow().isoformat()}
        row.definition_json = definition_json
        row.is_active = False

    log_activity(
        "governance.policy_create",
        "policy_document",
        f"Utworzono policy {row.name}",
        entity_id=row.id,
        actor=current_user,
        client=row.client,
        metadata={"scope": row.scope, "enforcement_mode": row.enforcement_mode},
    )
    db.session.commit()
    flash("Policy zapisane.", "success")
    return redirect(url_for("governance.policies_index"))


@governance_bp.route("/policies/<int:policy_id>/toggle", methods=["POST"])
@login_required
@roles_required("administrator")
def policies_toggle(policy_id: int):
    row = PolicyDocument.query.get_or_404(policy_id)
    if row.is_active:
        archive_policy(row, actor=current_user)
    else:
        try:
            activate_policy(row, actor=current_user)
        except Exception as exc:
            db.session.rollback()
            flash(f"Nie udalo sie aktywowac policy: {exc}", "danger")
            return redirect(url_for("governance.policies_index"))
    log_activity(
        "governance.policy_toggle",
        "policy_document",
        f"Zmieniono status policy {row.name}",
        entity_id=row.id,
        actor=current_user,
        client=row.client,
        metadata={"is_active": row.is_active, "state": policy_state(row)},
    )
    db.session.commit()
    flash("Status policy zaktualizowany.", "info")
    return redirect(url_for("governance.policies_index"))


@governance_bp.route("/policies/<int:policy_id>/activate", methods=["POST"])
@login_required
@roles_required("administrator")
def policies_activate(policy_id: int):
    row = PolicyDocument.query.get_or_404(policy_id)
    try:
        activate_policy(row, actor=current_user)
    except Exception as exc:
        db.session.rollback()
        flash(f"Nie udalo sie aktywowac policy: {exc}", "danger")
        return redirect(url_for("governance.policies_index"))

    log_activity(
        "governance.policy_activate",
        "policy_document",
        f"Aktywowano policy {row.name}",
        entity_id=row.id,
        actor=current_user,
        client=row.client,
        metadata={"state": policy_state(row)},
    )
    db.session.commit()
    flash("Policy aktywowane.", "success")
    return redirect(url_for("governance.policies_index"))


@governance_bp.route("/policies/<int:policy_id>/archive", methods=["POST"])
@login_required
@roles_required("administrator")
def policies_archive(policy_id: int):
    row = PolicyDocument.query.get_or_404(policy_id)
    archive_policy(row, actor=current_user)
    log_activity(
        "governance.policy_archive",
        "policy_document",
        f"Zarchiwizowano policy {row.name}",
        entity_id=row.id,
        actor=current_user,
        client=row.client,
        metadata={"state": policy_state(row)},
    )
    db.session.commit()
    flash("Policy zarchiwizowane.", "info")
    return redirect(url_for("governance.policies_index"))


@governance_bp.route("/policies/<int:policy_id>/rollback", methods=["POST"])
@login_required
@roles_required("administrator")
def policies_rollback(policy_id: int):
    row = PolicyDocument.query.get_or_404(policy_id)
    try:
        target = rollback_policy(row, actor=current_user)
    except Exception as exc:
        db.session.rollback()
        flash(f"Rollback policy nieudany: {exc}", "danger")
        return redirect(url_for("governance.policies_index"))

    log_activity(
        "governance.policy_rollback",
        "policy_document",
        f"Rollback policy z {row.id} do {target.id}",
        entity_id=target.id,
        actor=current_user,
        client=target.client,
        metadata={"from_policy_id": row.id, "to_policy_id": target.id},
    )
    db.session.commit()
    flash("Rollback policy zakonczony.", "success")
    return redirect(url_for("governance.policies_index"))


@governance_bp.route("/policies/<int:policy_id>/test", methods=["POST"])
@login_required
@roles_required("administrator")
def policies_test(policy_id: int):
    row = PolicyDocument.query.get_or_404(policy_id)
    event_type = (request.form.get("event_type") or "").strip()
    target_type = (request.form.get("target_type") or "").strip() or None
    target_id = (request.form.get("target_id") or "").strip() or None
    context_raw = request.form.get("context_json") or "{}"

    if not event_type:
        flash("Event type jest wymagany do testu polityki.", "danger")
        return redirect(url_for("governance.policies_index"))

    try:
        context = json.loads(context_raw)
        if not isinstance(context, dict):
            raise ValueError("context_json musi byc obiektem")
    except Exception as exc:
        flash(f"Niepoprawny context JSON: {exc}", "danger")
        return redirect(url_for("governance.policies_index"))

    result = evaluate_policies(
        event_type=event_type,
        context=context,
        client=row.client if row.scope == "tenant" else None,
        actor=current_user,
        target_type=target_type,
        target_id=target_id,
        persist=False,
        policy_ids=[row.id],
        include_inactive=True,
    )
    session["governance_policy_test_result"] = {
        "policy_id": row.id,
        "event_type": event_type,
        "blocked": result.get("blocked"),
        "matched": result.get("matched"),
        "decisions": result.get("decisions", []),
    }
    flash("Test policy wykonany.", "info")
    return redirect(url_for("governance.policies_index"))


@governance_bp.route("/onboarding")
@login_required
@roles_required("administrator")
def onboarding_index():
    clients = _client_choices()
    selected_client_id = _safe_int(request.args.get("client_id"), default=clients[0][0] if clients else None)
    selected_client = Client.query.get(selected_client_id) if selected_client_id else None

    states = ClientOnboardingState.query.order_by(ClientOnboardingState.updated_at.desc()).limit(500).all()
    state_by_client = {row.client_id: row for row in states}

    rows = []
    for client in Client.query.order_by(Client.id.asc()).all():
        row_state = state_by_client.get(client.id)
        rows.append(
            {
                "client": client,
                "state": row_state,
                "percent": row_state.completion_percent if row_state else 0,
                "failed": len(row_state.skipped_steps_json or []) if row_state else 0,
                "completed": len(row_state.completed_steps_json or []) if row_state else 0,
            }
        )

    selected_view = compute_onboarding_view(selected_client) if selected_client is not None else None

    return render_template(
        "admin/governance_onboarding.html",
        title="Onboarding visibility",
        clients=clients,
        selected_client=selected_client,
        rows=rows,
        selected_view=selected_view,
    )


@governance_bp.route("/dr")
@login_required
@roles_required("administrator")
def dr_index():
    clients = _client_choices()
    selected_client_id = _safe_int(request.args.get("client_id"), default=None)
    selected_client = Client.query.get(selected_client_id) if selected_client_id else None

    profiles_query = DisasterRecoveryProfile.query.order_by(DisasterRecoveryProfile.created_at.desc())
    if selected_client is not None:
        profiles_query = profiles_query.filter(DisasterRecoveryProfile.client_id == selected_client.id)
    profiles = profiles_query.limit(100).all()

    checks_query = DisasterRecoveryCheckRun.query.order_by(DisasterRecoveryCheckRun.checked_at.desc())
    if selected_client is not None:
        checks_query = checks_query.filter(DisasterRecoveryCheckRun.client_id == selected_client.id)
    checks = checks_query.limit(100).all()

    current_snapshot = evaluate_dr_readiness(selected_client) if selected_client is not None else None

    return render_template(
        "admin/governance_dr.html",
        title="DR readiness",
        clients=clients,
        selected_client=selected_client,
        profiles=profiles,
        checks=checks,
        current_snapshot=current_snapshot,
    )


@governance_bp.route("/dr/profile/save", methods=["POST"])
@login_required
@roles_required("administrator")
def dr_profile_save():
    client = _client_from_form()
    if client is None:
        flash("Wybierz klienta dla profilu DR.", "danger")
        return redirect(url_for("governance.dr_index"))

    primary_region = (request.form.get("primary_region") or "").strip()
    secondary_region = (request.form.get("secondary_region") or "").strip()
    rpo_target_minutes = _safe_int(request.form.get("rpo_target_minutes"), default=1440) or 1440
    rto_target_minutes = _safe_int(request.form.get("rto_target_minutes"), default=240) or 240
    notes = (request.form.get("notes") or "").strip()

    profile = update_dr_profile(
        client=client,
        primary_region=primary_region,
        secondary_region=secondary_region,
        rpo_target_minutes=rpo_target_minutes,
        rto_target_minutes=rto_target_minutes,
        notes=notes,
    )

    log_activity(
        "governance.dr_profile_save",
        "disaster_recovery_profile",
        "Zapisano profil DR",
        entity_id=profile.id,
        actor=current_user,
        client=client,
        metadata={
            "primary_region": profile.primary_region,
            "secondary_region": profile.secondary_region,
            "rpo_target_minutes": profile.rpo_target_minutes,
            "rto_target_minutes": profile.rto_target_minutes,
        },
    )
    db.session.commit()
    flash("Profil DR zapisany.", "success")
    return redirect(url_for("governance.dr_index", client_id=client.id))


@governance_bp.route("/dr/run", methods=["POST"])
@login_required
@roles_required("administrator")
def dr_run():
    client = _client_from_form()
    summary = run_dr_readiness_checks(actor=current_user, client_id=client.id if client else None)
    status_changes = sum(1 for item in summary.get("snapshots", []) if item.get("status_changed"))
    log_activity(
        "governance.dr_run",
        "disaster_recovery_check_run",
        "Uruchomiono DR readiness checks",
        actor=current_user,
        client=client,
        metadata={
            "clients": summary["clients"],
            "overall_score": summary["overall_score"],
            "status_changes": status_changes,
        },
    )
    db.session.commit()
    flash(
        (
            "DR checks zakonczone: "
            f"clients={summary['clients']}, overall_score={summary['overall_score']}, "
            f"status_changes={status_changes}"
        ),
        "info",
    )
    return redirect(url_for("governance.dr_index", client_id=client.id if client else None))


@governance_bp.route("/dr/failover-test", methods=["POST"])
@login_required
@roles_required("administrator")
def dr_failover_test():
    client = _client_from_form()
    if client is None:
        flash("Wybierz klienta do failover test.", "danger")
        return redirect(url_for("governance.dr_index"))

    safe_mode = request.form.get("safe_mode", "1") != "0"
    summary = run_failover_simulation(client=client, actor=current_user, safe_mode=safe_mode)
    log_activity(
        "governance.dr_failover_test",
        "disaster_recovery_check_run",
        "Uruchomiono failover simulation",
        entity_id=summary.get("run_id"),
        actor=current_user,
        client=client,
        metadata=summary,
    )
    db.session.commit()

    flash(
        f"Failover simulation: result={summary['result']} (safe_mode={summary['safe_mode']})",
        "info",
    )
    return redirect(url_for("governance.dr_index", client_id=client.id))
