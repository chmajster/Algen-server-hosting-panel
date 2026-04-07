from __future__ import annotations

from datetime import datetime

from panel.extensions import db
from panel.models import Client, ClientOnboardingState, User


ONBOARDING_STEPS: list[dict[str, str]] = [
    {
        "id": "connect_domain",
        "title": "Connect domain",
        "description": "Dodaj przynajmniej jedna domene do hostingu.",
    },
    {
        "id": "dns_setup",
        "title": "DNS setup",
        "description": "Skonfiguruj strefe DNS dla domeny.",
    },
    {
        "id": "ssl",
        "title": "SSL",
        "description": "Wlacz SSL i potwierdz aktywny certyfikat.",
    },
    {
        "id": "mailboxes",
        "title": "Mailboxes",
        "description": "Utworz przynajmniej jedna skrzynke e-mail.",
    },
    {
        "id": "backup",
        "title": "Backup",
        "description": "Potwierdz wykonany backup ze statusem completed.",
    },
    {
        "id": "readiness_checklist",
        "title": "Readiness checklist",
        "description": "Zakoncz checklist gotowosci po walidacji wszystkich krokow.",
    },
]


STEP_IDS = {item["id"] for item in ONBOARDING_STEPS}


def _as_set(raw: list | None) -> set[str]:
    return {str(item).strip() for item in (raw or []) if str(item).strip() in STEP_IDS}


def auto_completed_steps(client: Client) -> set[str]:
    completed: set[str] = set()
    if len(client.domains) > 0:
        completed.add("connect_domain")

    if len(client.dns_zones) > 0:
        completed.add("dns_setup")

    has_ssl = any(domain.ssl_enabled for domain in client.domains)
    if has_ssl:
        completed.add("ssl")

    if len(client.mailboxes) > 0:
        completed.add("mailboxes")

    if any(backup.status == "completed" for backup in client.backups):
        completed.add("backup")

    required_before_readiness = {"connect_domain", "dns_setup", "ssl", "mailboxes", "backup"}
    if required_before_readiness.issubset(completed) and client.dr_profile is not None:
        completed.add("readiness_checklist")

    return completed


def get_or_create_state(client: Client, *, updated_by: User | None = None) -> ClientOnboardingState:
    row = ClientOnboardingState.query.filter_by(client_id=client.id).first()
    if row is None:
        row = ClientOnboardingState(
            client=client,
            completed_steps_json=[],
            skipped_steps_json=[],
            completion_percent=0,
            updated_by=updated_by,
        )
        db.session.add(row)
        db.session.flush()
    return row


def compute_onboarding_view(client: Client) -> dict:
    state = get_or_create_state(client)
    auto_done = auto_completed_steps(client)
    manual_done = _as_set(state.completed_steps_json)
    failed = _as_set(state.skipped_steps_json)

    merged_done = set(auto_done)
    merged_done.update(manual_done)

    steps: list[dict] = []
    for item in ONBOARDING_STEPS:
        step_id = item["id"]
        is_done = step_id in merged_done
        is_failed = step_id in failed
        status = "completed" if is_done else ("failed" if is_failed else "pending")
        steps.append(
            {
                "id": step_id,
                "title": item["title"],
                "description": item["description"],
                "status": status,
                "auto_done": step_id in auto_done,
            }
        )

    total = len(ONBOARDING_STEPS)
    completed_count = sum(1 for step in steps if step["status"] == "completed")
    percent = int(round((completed_count / total) * 100)) if total else 100

    state.completion_percent = max(0, min(100, percent))
    if completed_count == total and total > 0:
        state.completed_at = state.completed_at or datetime.utcnow()
    else:
        state.completed_at = None

    return {
        "state": state,
        "steps": steps,
        "percent": state.completion_percent,
        "completed": completed_count,
        "total": total,
    }


def update_onboarding_step(
    *,
    client: Client,
    step_id: str,
    action: str,
    actor: User | None,
) -> dict:
    normalized_step = (step_id or "").strip()
    if normalized_step not in STEP_IDS:
        raise ValueError("Nieznany krok onboarding.")

    normalized_action = (action or "").strip().lower()
    if normalized_action in {"skip", "unskip"}:
        normalized_action = "fail" if normalized_action == "skip" else "clear_fail"
    if normalized_action not in {"complete", "undo", "fail", "clear_fail"}:
        raise ValueError("Nieznana akcja onboarding.")

    state = get_or_create_state(client, updated_by=actor)
    completed = _as_set(state.completed_steps_json)
    failed = _as_set(state.skipped_steps_json)

    validated = auto_completed_steps(client)

    if normalized_action == "complete":
        if normalized_step not in validated:
            raise ValueError("Krok nie przeszedl automatycznej walidacji i nie moze byc oznaczony jako complete.")
        completed.add(normalized_step)
        failed.discard(normalized_step)
    elif normalized_action == "undo":
        completed.discard(normalized_step)
    elif normalized_action == "fail":
        failed.add(normalized_step)
        completed.discard(normalized_step)
    elif normalized_action == "clear_fail":
        failed.discard(normalized_step)

    state.completed_steps_json = sorted(completed)
    state.skipped_steps_json = sorted(failed)
    state.last_step = normalized_step
    state.updated_by = actor

    return compute_onboarding_view(client)
